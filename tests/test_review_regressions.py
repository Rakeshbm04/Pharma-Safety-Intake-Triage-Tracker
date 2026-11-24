"""Regression tests for findings raised by the pharma-intake-review skill (see docs/SKILL_REVIEW_EVIDENCE.md)."""
from __future__ import annotations

import json

import pytest

from app import audit, workflow
from app.agent.circuit_breaker import CircuitBreaker, CircuitOpenError, call_signature
from app.agent.orchestrator import Orchestrator, parse_status_target
from app.agent.router import Intent, route
from app.agent.tools import ToolContext, ToolExecutor
from app.models import Actor, Role, Status
from app.rag.service import KnowledgeBase


@pytest.fixture
def orch(db_path, operator):
    return Orchestrator(db_path, operator, rag_provider=KnowledgeBase(db_path, backend="offline").pipeline)


# ---- Finding 1 (BLOCKER): field-prefix guardrail bypass -----------------------
@pytest.mark.parametrize("msg", [
    "event: is Zentrovax causing the rash?",
    "description: what treatment should the patient get for the hives?",
    "product: Zentrovax\nevent: should she stop taking it?",
])
def test_field_prefix_cannot_bypass_guardrail(msg):
    assert route(msg).intent is Intent.OUT_OF_SCOPE


def test_real_intake_values_still_allowed():
    msg = "type: AE\nproduct: Dermacil\nbatch: LOT-31904\nevent: rash treated at home, caused itching"
    assert route(msg).intent is Intent.INTAKE_FORM


# ---- Finding 2 (MAJOR): paraphrases ----------------------------------------------
@pytest.mark.parametrize("msg", [
    "Did Zentrovax lead to the rash?", "Is the rash due to Zentrovax?", "Is Zentrovax responsible for the rash?",
    "Should the dose be lowered?", "How should the rash be managed?", "Is this event serious?",
    "Does this case need to go to the FDA?", "Must we notify the regulator within 15 days?",
    # re-review round 2
    "Could Zentrovax have provoked the hives?", "Was the drug behind the breathing problem?",
    "Is there a connection between Zentrovax and the rash?", "What should the patient do about the rash?",
    "Should she see a doctor about the hives?", "What dose is safe for the patient?", "Would an antihistamine help?",
    "Should the patient go to hospital?", "Does this meet the criteria for a SUSAR?",
    "Do we have to file a MedWatch for this?", "Is the event life-threatening?",
    "What's the deadline for submitting this to the EMA?", "according to the SOP, is REF-2026-0001 reportable?",
])
def test_guardrail_paraphrases(msg):
    assert route(msg).intent is Intent.OUT_OF_SCOPE


@pytest.mark.parametrize("msg", [
    "show reports from the last 30 days", "what does the SOP say about root cause investigation",
    "any reports of difficulty breathing for LOT-88392?", "how is a report closed",
    "how should a product quality complaint be handled", "how are complaints managed",
    "what happens when a report is due for review",
    # re-review round 2 (N1): quality wording, workflow how-tos, SOP meta-questions
    "any complaints about damaged packaging due to shipping?", "tablets cracked because of humidity in LOT-45127",
    "reports linked to LOT-88392", "which complaints resulted in a quality referral?",
    "how do I handle the closure request?", "how should we handle the closure request for REF-2026-0001",
    "how do I manage the audit trail?", "what does SOP-002 say about seriousness?",
    "what does the SOP say about the 15 day timeline?", "what is the procedure when a caller asks about treatment?",
    "what does the SOP say about callers asking for medical advice?", "open a root cause investigation for LOT-45127",
])
def test_guardrail_no_false_positives(msg):
    assert route(msg).intent is not Intent.OUT_OF_SCOPE


# ---- Finding 3 (MAJOR): questions must never change state ----------------------------
@pytest.mark.parametrize("msg", [
    "is REF-2026-0001 still in review?", "what is the closure status of REF-2026-0001",
    "is REF-2026-0002 closed?", "has REF-2026-0001 been moved to in review", "status of REF-2026-0001",
    "Update me on REF-2026-0001 closure", "Start by showing REF-2026-0001 closure details",
    "Set REF-2026-0001 aside, is it closed", "Take a look at REF-2026-0001 closure",
])
def test_questions_are_lookups(msg):
    assert parse_status_target(msg) is None


@pytest.mark.parametrize("msg,target", [
    ("move REF-2026-0001 to In Review", "In Review"), ("close REF-2026-0001", "Closed"),
    ("please close REF-2026-0001", "Closed"), ("can you move REF-2026-0001 to in review?", "In Review"),
    ("send REF-2026-0001 back to new", "New"), ("send REF-2026-0001 back", "New"),
    ("Request closure for REF-2026-0001", "Closed"), ("submit REF-2026-0001 for closure", "Closed"),
    ("update REF-2026-0001 to in review", "In Review"),
])
def test_imperatives_are_transitions(msg, target):
    assert (parse_status_target(msg) or "").casefold() == target.casefold()  # case repaired by tool layer
    assert route(msg).intent is Intent.STATUS_WORKFLOW


def test_no_control_characters_in_source():
    from tests.conftest import ROOT
    bad = [p.name for p in (ROOT / "app").rglob("*.py") if any(ord(c) < 9 for c in p.read_text(encoding="utf-8"))]
    assert bad == []


def test_question_does_not_mutate_or_audit(orch, make_report, pqc_payload, db_path, operator):
    ref = make_report(pqc_payload).ref_id
    orch.handle(f"is {ref} still in review?")
    orch.handle(f"is {ref} closed?")
    workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    orch.handle(f"what is the closure status of {ref}")
    r = orch.long_term.report(ref)
    assert r.status is Status.IN_REVIEW and not r.closure_pending
    assert [e.action for e in audit.list_entries(db_path, ref_id=ref)] == ["STATUS_CHANGED", "REPORT_CREATED"]


# ---- Finding 4 (MAJOR): caller key names must not reach the audit log ---------------------
def test_unknown_key_names_not_audited(db_path, operator):
    ex = ToolExecutor(ToolContext(db_path, operator))
    res = ex.call("get_report", {"ref_id": "REF-2026-0001", "jane.doe@example.com": "x"})
    assert not res.ok
    dump = json.dumps([e.metadata for e in audit.list_entries(db_path)])
    assert "jane.doe" not in dump and "<unknown_field>" in dump


# ---- Finding 5 (MAJOR): rejected operations are audited ---------------------------------------
def _last(db_path):
    return audit.list_entries(db_path, limit=1)[0]


def test_hitl_bypass_attempt_audited(make_report, pqc_payload, db_path, operator):
    ref = make_report(pqc_payload).ref_id
    workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    with pytest.raises(workflow.HITLRequiredError):
        workflow.change_status(db_path, ref, Status.CLOSED, operator)
    e = _last(db_path)
    assert e.action == "TRANSITION_REJECTED" and e.metadata["reason_code"] == "HITL_APPROVAL_REQUIRED"


def test_invalid_closure_request_audited(make_report, pqc_payload, db_path, operator):
    ref = make_report(pqc_payload).ref_id
    with pytest.raises(workflow.TransitionError):
        workflow.request_closure(db_path, ref, operator)
    e = _last(db_path)
    assert e.action == "CLOSURE_ACTION_REJECTED" and e.metadata["reason_code"] == "NOT_IN_REVIEW" and e.ref_id == ref


@pytest.mark.parametrize("who,code", [
    (Actor(actor_id="OP-01", role=Role.REVIEWER), "FOUR_EYES_VIOLATION"),
    (Actor(actor_id="OP-02", role=Role.OPERATOR), "ROLE_NOT_REVIEWER"),
])
def test_failed_approvals_audited(make_report, pqc_payload, db_path, operator, who, code):
    ref = make_report(pqc_payload).ref_id
    workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    aid = workflow.request_closure(db_path, ref, operator)
    with pytest.raises(workflow.ApprovalError):
        workflow.approve_closure(db_path, aid, who)
    e = _last(db_path)
    assert (e.action, e.ref_id, e.actor_id, e.metadata) == (
        "CLOSURE_ACTION_REJECTED", ref, who.actor_id, {"reason_code": code, "approval_id": aid})


def test_blocked_call_audited(db_path, operator):
    ex = ToolExecutor(ToolContext(db_path, operator))
    bad = {"ref_id": "BAD", "new_status": "x"}
    for _ in range(2):
        ex.call("update_status", bad)
    for _ in range(2):
        with pytest.raises(CircuitOpenError):
            ex.call("update_status", bad)
    assert _last(db_path).action == "TOOL_CALL_BLOCKED"


# ---- Finding 7 (MINOR): per-signature failure count in the blocked message --------------------
def test_blocked_message_reports_signature_count():
    cb = CircuitBreaker()
    sig = call_signature("t", {"a": 1})
    for _ in range(2):
        cb.record_failure("t", sig)
    with pytest.raises(CircuitOpenError):
        cb.record_failure("t", sig)
    cb.record_success()
    with pytest.raises(CircuitOpenError) as info:
        cb.before_call("t", sig)
    assert info.value.failures == 3
