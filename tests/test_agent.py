"""Agent control plane: tool validation + auto-repair, circuit breaker, router, memory, orchestrator."""
from __future__ import annotations

import pytest

from app import audit
from app.agent.circuit_breaker import CircuitBreaker, CircuitOpenError, call_signature
from app.agent.guardrails import SCOPE_REFUSAL, check_scope
from app.agent.memory import ShortTermMemory
from app.agent.orchestrator import Orchestrator, parse_status_target
from app.agent.router import Intent, route
from app.agent.tools import (SubmitIntakeArgs, ToolContext, ToolExecutor, UpdateStatusArgs, auto_repair,
                             repair_batch, repair_date, repair_ref)
from app.models import Status
from app.rag.service import KnowledgeBase


@pytest.fixture
def executor(db_path, operator):
    return ToolExecutor(ToolContext(db_path, operator))


@pytest.fixture
def orch(db_path, operator):
    kb = KnowledgeBase(db_path, backend="offline")
    return Orchestrator(db_path, operator, rag_provider=kb.pipeline)


# ---- Step 1: type-safe tool validation + auto-repair -------------------------
@pytest.mark.parametrize("raw,expected", [("lot 88392", "LOT-88392"), ("lot88392", "LOT-88392"),
                                          ("LOT_88392", "LOT-88392"), ("88392", "LOT-88392"),
                                          ("LOT-8839", "LOT-8839")])
def test_repair_batch(raw, expected):
    assert repair_batch(raw) == expected


def test_repair_ref_and_date():
    assert repair_ref("ref 2026 1") == "REF-2026-0001"
    assert repair_ref("REF-2026-0012") == "REF-2026-0012"
    assert repair_date("20/11/2026") == "2026-11-20"
    assert repair_date("Nov 20 2026") == "2026-11-20"


def test_auto_repair_fixes_key_typos_and_enums():
    args, notes = auto_repair(UpdateStatusArgs, {"ref_idd": "ref-2026-3", "new_stauts": "in reveiw"})
    assert args == {"ref_id": "REF-2026-0003", "new_status": "In Review"}
    assert len(notes) == 4


def test_strict_schema_rejects_bad_batch():
    with pytest.raises(Exception):
        SubmitIntakeArgs.model_validate_json('{"report_type":"Product Quality Complaint","product_name":"X1",'
                                             '"batch_number":"BATCH-1","date_received":"2026-01-01",'
                                             '"reporter_type":"Other","description":"' + "d" * 25 + '"}')


def test_tool_submit_with_repairs(executor, ae_payload, repo):
    raw = {**ae_payload, "batch_number": "lot 88392", "report_type": "ae", "reporter_type": "pharmacist"}
    res = executor.call("submit_intake_report", raw)
    assert res.ok, res.errors
    assert any("batch_number" in r for r in res.repairs)
    assert repo.get(res.data.ref_id).batch_number == "LOT-88392"


def test_tool_structured_error(executor):
    res = executor.call("update_status", {"ref_id": "XYZ", "new_status": "Archived"})
    assert not res.ok and res.invalid_args
    assert {e.field for e in res.errors} == {"ref_id", "new_status"}
    assert any("REF-" in e.message for e in res.errors)


def test_tool_name_typo_repaired(executor):
    res = executor.call("get_reprot", {"ref_id": "REF-2026-0001"})
    assert res.tool == "get_report"


def test_update_status_to_closed_creates_hitl_request(executor, make_report, pqc_payload, db_path, repo):
    ref = make_report(pqc_payload).ref_id
    assert executor.call("update_status", {"ref_id": ref, "new_status": "In Review"}).ok
    res = executor.call("update_status", {"ref_id": ref, "new_status": "closed"})
    assert res.ok and res.data["hitl"]
    r = repo.get(ref)
    assert r.status is Status.IN_REVIEW and r.closure_pending  # NOT closed: human approval required


def test_workflow_error_is_not_an_argument_error(executor, make_report, pqc_payload):
    ref = make_report(pqc_payload).ref_id
    for _ in range(4):  # valid args, forbidden transition: never trips the breaker
        res = executor.call("update_status", {"ref_id": ref, "new_status": "Closed"})
        assert not res.ok and not res.invalid_args
    assert not executor.breaker.open_signatures


# ---- Step 5: circuit breaker -------------------------------------------------
def test_breaker_trips_on_third_identical_invalid_call(executor, db_path):
    bad = {"ref_id": "BAD", "new_status": "In Review"}
    assert executor.call("update_status", bad).errors
    assert executor.call("update_status", bad).errors
    with pytest.raises(CircuitOpenError):
        executor.call("update_status", bad)
    with pytest.raises(CircuitOpenError):  # stays open
        executor.call("update_status", bad)
    acts = [e.action for e in audit.list_entries(db_path)]
    assert acts.count("TOOL_VALIDATION_FAILED") == 2 and acts.count("CIRCUIT_BREAKER_TRIPPED") == 1


def test_breaker_resets_on_success_and_different_signature():
    cb = CircuitBreaker()
    a, b = call_signature("t", {"x": 1}), call_signature("t", {"x": 2})
    cb.record_failure("t", a)
    cb.record_failure("t", a)
    cb.record_success()
    cb.record_failure("t", a)
    cb.record_failure("t", a)          # streak restarted, still 2
    cb.record_failure("t", b)          # different signature resets
    cb.record_failure("t", a)
    cb.record_failure("t", a)
    with pytest.raises(CircuitOpenError):
        cb.record_failure("t", a)
    cb.reset()
    cb.before_call("t", a)  # no raise after manual reset


def test_signature_is_order_independent():
    assert call_signature("t", {"a": 1, "b": 2}) == call_signature("t", {"b": 2, "a": 1})


def test_orchestrator_halts_on_breaker(orch):
    msg = "move REF-2026-0999 to Archivedd"
    for _ in range(2):
        assert not orch.handle(msg).halted
    resp = orch.handle(msg)
    assert resp.halted and "Circuit breaker OPEN" in resp.text


# ---- Step 3: deterministic router + guardrails -------------------------------
@pytest.mark.parametrize("msg,intent", [
    ("hello", Intent.DIRECT_RESPONSE),
    ("thanks!", Intent.DIRECT_RESPONSE),
    ("any reports of difficulty breathing for LOT-88392?", Intent.HYBRID_RAG),
    ("what does the SOP say about particles in vials", Intent.HYBRID_RAG),
    ("move REF-2026-0001 to In Review", Intent.STATUS_WORKFLOW),
    ("what is the status of ref 2026 3", Intent.STATUS_WORKFLOW),
    ("I need to log a new adverse event", Intent.INTAKE_FORM),
    ("product: X\nbatch: LOT-11111\ndescription: something", Intent.INTAKE_FORM),
    ("did Zentrovax cause the dyspnea in REF-2026-0001?", Intent.OUT_OF_SCOPE),
    ("what treatment should the patient take for hives", Intent.OUT_OF_SCOPE),
    ("is REF-2026-0001 reportable to the FDA?", Intent.OUT_OF_SCOPE),
    ("should we increase the dose", Intent.OUT_OF_SCOPE),
    ("should the patient stop taking Zentrovax?", Intent.OUT_OF_SCOPE),
    ("is it safe to continue using the cream", Intent.OUT_OF_SCOPE),
    ("can she keep taking it after the rash", Intent.OUT_OF_SCOPE),
])
def test_router(msg, intent):
    assert route(msg).intent is intent


def test_root_cause_is_in_scope():
    assert check_scope("open a root cause investigation for LOT-45127") is None


def test_guardrail_ignores_recorded_intake_values():
    msg = "new report\ntype: AE\nevent: rash, treated at home with cream\nproduct: Dermacil"
    assert route(msg).intent is Intent.INTAKE_FORM


def test_out_of_scope_calls_no_tools(orch):
    resp = orch.handle("Was the event caused by the drug?")
    assert resp.text == SCOPE_REFUSAL and resp.tool_results == []


def test_parse_status_target():
    assert parse_status_target("please close REF-2026-0001") == "Closed"
    assert parse_status_target("send REF-2026-0001 back") == "New"
    assert parse_status_target("send it back REF-2026-0001") is None  # verb must act directly on the REF id
    assert parse_status_target("move REF-2026-0001 to in reveiw") == "in reveiw"
    assert parse_status_target("status of REF-2026-0001") is None


# ---- Step 2: dual memory + end-to-end orchestration ---------------------------
def test_react_trace_and_long_term_memory(orch, make_report, pqc_payload):
    ref = make_report(pqc_payload).ref_id
    resp = orch.handle(f"move {ref} to in reveiw")
    assert resp.intent is Intent.STATUS_WORKFLOW and "In Review" in resp.text
    kinds = [s.kind for s in resp.trace]
    assert kinds[0] == "Thought" and "Action" in kinds and "Observation" in kinds and kinds[-1] == "Final"
    assert any("auto-repaired" in s.content for s in resp.trace)
    assert orch.long_term.report(ref).status is Status.IN_REVIEW
    assert [e.action for e in orch.long_term.history(ref)][0] == "STATUS_CHANGED"


def test_chat_intake_and_rag(orch, ae_payload):
    msg = ("new report\ntype: AE\nproduct: Zentrovax 50 mg\nbatch: lot 88392\n"
           f"date received: {ae_payload['date_received']}\nreporter: HCP\n"
           "description: Fictional caller reported a reaction after the second tablet.\n"
           f"event: Shortness of breath lasting about an hour\nonset: {ae_payload['event_onset_date']}")
    resp = orch.handle(msg)
    assert resp.text.startswith("✅"), resp.text
    rag = orch.handle("reports of difficulty breathing with LOT-88392")
    assert rag.intent is Intent.HYBRID_RAG and rag.rag_result.found
    assert rag.rag_result.citations[0].startswith("REF-")


def test_chat_intake_errors_are_explicit(orch):
    resp = orch.handle("new report\ntype: AE\nproduct: Zentrovax\nbatch: LOT-1\nreporter: HCP")
    assert resp.text.startswith("❌") and "batch_number" in resp.text


def test_short_term_memory_bounded():
    mem = ShortTermMemory(max_steps=3)
    mem.new_run()
    for i in range(5):
        mem.add("Thought", str(i))
    assert [s.content for s in mem.all()] == ["2", "3", "4"]
