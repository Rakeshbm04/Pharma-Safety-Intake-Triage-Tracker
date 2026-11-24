"""Storage integrity: round-trip, unique references, audit immutability and data minimisation."""
from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app import audit
from app.audit import AuditAction, sanitize_metadata
from app.models import ReportType, Status
from app.repository import ReportNotFoundError
from app.validation import validate_intake


def test_round_trip(make_report, repo, ae_payload):
    created = make_report(ae_payload)
    loaded = repo.get(created.ref_id)
    assert loaded == created
    assert loaded.status is Status.NEW
    assert loaded.event_description == ae_payload["event_description"]


def test_reference_format_and_sequence(make_report, ae_payload, pqc_payload):
    a, b = make_report(ae_payload), make_report(pqc_payload)
    year = date.today().year
    assert re.fullmatch(rf"REF-{year}-\d{{4}}", a.ref_id)
    assert a.ref_id == f"REF-{year}-0001" and b.ref_id == f"REF-{year}-0002"


def test_references_unique_under_concurrency(repo, operator, pqc_payload):
    models = [validate_intake({**pqc_payload, "batch_number": f"LOT-{20000 + i}"})[0] for i in range(24)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        refs = list(pool.map(lambda m: repo.create(m, operator).ref_id, models))
    assert len(set(refs)) == 24


def test_get_missing_raises(repo):
    with pytest.raises(ReportNotFoundError):
        repo.get("REF-2026-9999")


def test_filtering(make_report, repo, ae_payload, pqc_payload):
    make_report(ae_payload)
    make_report(pqc_payload)
    assert len(repo.list(report_types=[ReportType.ADVERSE_EVENT])) == 1
    assert len(repo.list(statuses=[Status.NEW])) == 2
    assert repo.list(statuses=[Status.CLOSED]) == []
    assert repo.counts_by_status() == {"New": 2, "In Review": 0, "Closed": 0}


def test_create_writes_exactly_one_metadata_only_audit_row(make_report, db_path, ae_payload):
    r = make_report(ae_payload)
    entries = audit.list_entries(db_path, ref_id=r.ref_id)
    assert len(entries) == 1
    e = entries[0]
    assert e.action == AuditAction.REPORT_CREATED.value and e.new_status == "New" and e.actor_id == "OP-01"
    raw = json.dumps(e.__dict__)
    for text in (ae_payload["description"], ae_payload["event_description"], ae_payload["product_name"]):
        assert text not in raw


def test_audit_log_is_append_only(make_report, db_path, pqc_payload):
    make_report(pqc_payload)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET action = 'TAMPERED'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log")
    conn.close()


def test_failed_insert_rolls_back_audit(repo, operator, pqc_payload, db_path, monkeypatch):
    model, _ = validate_intake(pqc_payload)

    def boom(*a, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        repo.create(model, operator)
    monkeypatch.undo()
    assert repo.list() == [] and audit.list_entries(db_path) == []


def test_metadata_whitelist_drops_free_text():
    clean = sanitize_metadata({"report_type": "Product Quality Complaint", "description": "secret text",
                               "approval_id": 3, "fields": ["batch_number"], "patient": "x"})
    assert clean == {"report_type": "Product Quality Complaint", "approval_id": 3, "fields": ["batch_number"]}


def test_schema_has_no_pii_columns(db_path, repo):
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(reports)")}
    conn.close()
    forbidden = {"patient_name", "initials", "dob", "date_of_birth", "email", "phone", "address", "mrn"}
    assert not cols & forbidden
