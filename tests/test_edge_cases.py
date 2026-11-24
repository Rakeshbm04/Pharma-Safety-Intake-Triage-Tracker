"""Edge cases: duplicate submission, SQL injection, unicode, search, migration, concurrency,
router determinism, memory persistence, degenerate RAG queries."""
from __future__ import annotations

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import audit, workflow
from app.agent.memory import LongTermMemory
from app.agent.router import route
from app.agent.tools import ToolContext, ToolExecutor
from app.models import Status
from app.rag.pipeline import NO_CONTEXT_ANSWER
from app.rag.service import KnowledgeBase
from app.repository import DuplicateSubmissionError, ReportRepository, fingerprint
from app.validation import validate_intake


# ---- duplicate submission ------------------------------------------------------------
def test_double_submit_creates_one_report(repo, operator, pqc_payload, db_path):
    model, _ = validate_intake(pqc_payload)
    first = repo.create(model, operator)
    with pytest.raises(DuplicateSubmissionError) as exc:
        repo.create(model, operator)
    assert exc.value.existing_ref == first.ref_id and first.ref_id in str(exc.value)
    assert len(repo.list()) == 1
    acts = [e.action for e in audit.list_entries(db_path)]
    assert acts.count("REPORT_CREATED") == 1 and acts.count("DUPLICATE_REJECTED") == 1


def test_duplicate_detection_ignores_case_and_whitespace(repo, operator, pqc_payload):
    repo.create(validate_intake(pqc_payload)[0], operator)
    variant = {**pqc_payload, "description": "  " + pqc_payload["description"].upper() + "  ",
               "product_name": pqc_payload["product_name"].lower()}
    with pytest.raises(DuplicateSubmissionError):
        repo.create(validate_intake(variant)[0], operator)


def test_duplicate_does_not_consume_reference_number(repo, operator, pqc_payload):
    a = repo.create(validate_intake(pqc_payload)[0], operator)
    with pytest.raises(DuplicateSubmissionError):
        repo.create(validate_intake(pqc_payload)[0], operator)
    b = repo.create(validate_intake({**pqc_payload, "batch_number": "LOT-99999"})[0], operator)
    assert int(b.ref_id[-4:]) == int(a.ref_id[-4:]) + 1


def test_concurrent_identical_submits_create_exactly_one(repo, operator, pqc_payload):
    model, _ = validate_intake(pqc_payload)

    def submit(_):
        try:
            return repo.create(model, operator).ref_id
        except DuplicateSubmissionError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(submit, range(12)))
    assert sum(r is not None for r in results) == 1 and len(repo.list()) == 1


def test_fingerprint_is_hash_not_text(repo, operator, pqc_payload, db_path):
    repo.create(validate_intake(pqc_payload)[0], operator)
    conn = sqlite3.connect(db_path)
    (fp,) = conn.execute("SELECT fingerprint FROM reports").fetchone()
    conn.close()
    assert re.fullmatch(r"[0-9a-f]{64}", fp) and fp == fingerprint(validate_intake(pqc_payload)[0])


def test_duplicate_via_agent_tool_is_not_an_argument_error(db_path, operator, ae_payload):
    ex = ToolExecutor(ToolContext(db_path, operator))
    assert ex.call("submit_intake_report", ae_payload).ok
    for _ in range(4):  # valid args rejected by business rule: must never trip the breaker
        res = ex.call("submit_intake_report", ae_payload)
        assert not res.ok and not res.invalid_args and "already saved" in res.message
    assert not ex.breaker.open_signatures


def test_migration_adds_fingerprint_to_old_database(tmp_path, operator, pqc_payload):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript("""
        CREATE TABLE reports (ref_id TEXT PRIMARY KEY, report_type TEXT NOT NULL, product_name TEXT NOT NULL,
          batch_number TEXT NOT NULL, date_received TEXT NOT NULL, reporter_type TEXT NOT NULL,
          description TEXT NOT NULL, event_description TEXT, event_onset_date TEXT, status TEXT NOT NULL,
          closure_pending INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT NOT NULL);
        INSERT INTO reports VALUES ('REF-2026-0001','Product Quality Complaint','Old Product','LOT-11111',
          '2026-01-01','Other','Legacy fictional description text here.',NULL,NULL,'New',0,'t','t','OP-01');
    """)
    conn.close()
    repo = ReportRepository(old)  # runs the migration
    assert repo.get("REF-2026-0001").product_name == "Old Product"
    repo.create(validate_intake(pqc_payload)[0], operator)
    assert len(repo.list()) == 2


# ---- injection, unicode, search -------------------------------------------------------
def test_sql_injection_strings_are_inert(repo, operator, pqc_payload):
    evil = "x'); DROP TABLE reports;-- fictional"
    r = repo.create(validate_intake({**pqc_payload, "product_name": evil[:60], "description": evil + " padding"})[0],
                    operator)
    assert repo.get(r.ref_id).product_name == evil[:60]
    assert repo.list(search="' OR 1=1 --") == []
    assert repo.list(search="%") == [] and repo.list(search="_") == []  # wildcards matched literally
    assert len(repo.list()) == 1


def test_unicode_round_trip(repo, operator, pqc_payload):
    text = "Comprimé décoloré · 錠剤の変色 · таблетка (fictional)"
    r = repo.create(validate_intake({**pqc_payload, "description": text})[0], operator)
    assert repo.get(r.ref_id).description == text


def test_search_by_reference_product_and_batch(repo, operator, pqc_payload, ae_payload):
    a = repo.create(validate_intake(pqc_payload)[0], operator)
    b = repo.create(validate_intake(ae_payload)[0], operator)
    assert [r.ref_id for r in repo.list(search=a.ref_id)] == [a.ref_id]
    assert [r.ref_id for r in repo.list(search="lot-88392")] == [b.ref_id]          # case-insensitive
    assert [r.ref_id for r in repo.list(search="cardiolex")] == [a.ref_id]
    assert [r.ref_id for r in repo.list(search="88392", statuses=[Status.CLOSED])] == []


# ---- concurrency on transitions -----------------------------------------------------------
def test_concurrent_transitions_only_one_wins(make_report, pqc_payload, db_path, operator, repo):
    ref = make_report(pqc_payload).ref_id

    def move(_):
        try:
            workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
            return True
        except workflow.TransitionError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        wins = sum(pool.map(move, range(8)))
    assert wins == 1 and repo.get(ref).status is Status.IN_REVIEW
    assert [e.action for e in audit.list_entries(db_path, ref_id=ref)].count("STATUS_CHANGED") == 1


# ---- determinism & memory ------------------------------------------------------------------
@pytest.mark.parametrize("msg", ["hello", "any reports for LOT-88392?", "move REF-2026-0001 to in review",
                                 "did the drug cause the rash?", "new report"])
def test_router_is_deterministic(msg):
    first = route(msg)
    assert all(route(msg) == first for _ in range(100))


def test_long_term_memory_survives_reopen(make_report, pqc_payload, db_path, operator):
    ref = make_report(pqc_payload).ref_id
    workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    fresh = LongTermMemory(db_path)  # new instance = new session over the same SQLite file
    assert fresh.report(ref).status is Status.IN_REVIEW
    assert [e.action for e in fresh.history(ref)] == ["STATUS_CHANGED", "REPORT_CREATED"]


# ---- degenerate RAG queries ------------------------------------------------------------------
@pytest.mark.parametrize("query", ["the of and is", "???", "a"])
def test_stopword_or_empty_queries_return_no_answer(make_report, pqc_payload, db_path, query):
    make_report(pqc_payload)
    r = KnowledgeBase(db_path, backend="offline").pipeline().search(query)
    assert not r.found and r.answer == NO_CONTEXT_ANSWER
