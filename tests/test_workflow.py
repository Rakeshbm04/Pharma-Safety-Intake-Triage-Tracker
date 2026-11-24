"""Status lifecycle and Human-in-the-Loop closure gate."""
from __future__ import annotations

import pytest

from app import audit, workflow
from app.models import Actor, Role, Status


@pytest.fixture
def in_review(make_report, db_path, operator, pqc_payload):
    r = make_report(pqc_payload)
    workflow.change_status(db_path, r.ref_id, Status.IN_REVIEW, operator)
    return r.ref_id


def actions(db_path, ref):
    return [e.action for e in reversed(audit.list_entries(db_path, ref_id=ref))]


def test_allowed_transitions(make_report, repo, db_path, operator, pqc_payload):
    ref = make_report(pqc_payload).ref_id
    workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    assert repo.get(ref).status is Status.IN_REVIEW
    assert actions(db_path, ref) == ["REPORT_CREATED", "STATUS_CHANGED"]


def _reach(status, make_report, pqc_payload, db_path, operator, reviewer, n):
    ref = make_report({**pqc_payload, "batch_number": f"LOT-{10000 + n}"}).ref_id
    if status in (Status.IN_REVIEW, Status.CLOSED):
        workflow.change_status(db_path, ref, Status.IN_REVIEW, operator)
    if status is Status.CLOSED:
        workflow.approve_closure(db_path, workflow.request_closure(db_path, ref, operator), reviewer)
    return ref


# The full 3x3 matrix: only New->In Review is a direct transition; In Review->Closed needs HITL;
# every other pair (incl. backward moves and self-transitions) is rejected and audited.
@pytest.mark.parametrize("start", list(Status))
@pytest.mark.parametrize("target", list(Status))
def test_transition_matrix(start, target, make_report, repo, db_path, operator, reviewer, pqc_payload):
    n = list(Status).index(start) * 3 + list(Status).index(target)
    ref = _reach(start, make_report, pqc_payload, db_path, operator, reviewer, n)
    if (start, target) == (Status.NEW, Status.IN_REVIEW):
        workflow.change_status(db_path, ref, target, operator)
        assert repo.get(ref).status is target
        return
    expected = workflow.HITLRequiredError if (start, target) == (Status.IN_REVIEW, Status.CLOSED) else workflow.TransitionError
    with pytest.raises(expected):
        workflow.change_status(db_path, ref, target, operator)
    assert repo.get(ref).status is start
    last = audit.list_entries(db_path, ref_id=ref, limit=1)[0]
    assert last.action == "TRANSITION_REJECTED"


def test_backward_transition_message_and_reason(in_review, db_path, operator):
    with pytest.raises(workflow.TransitionError, match="one-way"):
        workflow.change_status(db_path, in_review, Status.NEW, operator)
    assert audit.list_entries(db_path, ref_id=in_review, limit=1)[0].metadata == {"reason_code": "BACKWARD_TRANSITION"}


def test_new_to_closed_forbidden_and_audited(make_report, repo, db_path, operator, pqc_payload):
    ref = make_report(pqc_payload).ref_id
    with pytest.raises(workflow.TransitionError):
        workflow.change_status(db_path, ref, Status.CLOSED, operator)
    assert repo.get(ref).status is Status.NEW
    assert actions(db_path, ref)[-1] == "TRANSITION_REJECTED"


def test_direct_close_requires_hitl(in_review, db_path, operator, repo):
    with pytest.raises(workflow.HITLRequiredError):
        workflow.change_status(db_path, in_review, Status.CLOSED, operator)
    assert repo.get(in_review).status is Status.IN_REVIEW


def test_full_hitl_closure(in_review, db_path, operator, reviewer, repo):
    aid = workflow.request_closure(db_path, in_review, operator)
    assert repo.get(in_review).closure_pending
    decided = workflow.approve_closure(db_path, aid, reviewer)
    assert decided.decision == "APPROVED" and decided.decided_by == "OP-07"
    r = repo.get(in_review)
    assert r.status is Status.CLOSED and not r.closure_pending
    assert actions(db_path, in_review)[-2:] == ["CLOSURE_REQUESTED", "CLOSURE_APPROVED"]
    approved = audit.list_entries(db_path, ref_id=in_review)[0]
    assert approved.actor_id == "OP-07" and approved.actor_role == "Safety Reviewer"


def test_state_frozen_while_pending(in_review, db_path, operator):
    workflow.request_closure(db_path, in_review, operator)
    with pytest.raises(workflow.TransitionError, match="frozen"):
        workflow.change_status(db_path, in_review, Status.NEW, operator)
    with pytest.raises(workflow.ApprovalError):
        workflow.request_closure(db_path, in_review, operator)
    assert workflow.allowed_next(Status.IN_REVIEW, closure_pending=True) == []


def test_only_reviewer_can_approve(in_review, db_path, operator):
    aid = workflow.request_closure(db_path, in_review, operator)
    with pytest.raises(workflow.ApprovalError, match="Safety Reviewer"):
        workflow.approve_closure(db_path, aid, Actor(actor_id="OP-02", role=Role.OPERATOR))


def test_four_eyes_rule(in_review, db_path):
    requester = Actor(actor_id="OP-07", role=Role.REVIEWER)
    aid = workflow.request_closure(db_path, in_review, requester)
    with pytest.raises(workflow.ApprovalError, match="Four-eyes"):
        workflow.approve_closure(db_path, aid, requester)


def test_reject_unfreezes(in_review, db_path, operator, reviewer, repo):
    aid = workflow.request_closure(db_path, in_review, operator)
    workflow.reject_closure(db_path, aid, reviewer)
    r = repo.get(in_review)
    assert r.status is Status.IN_REVIEW and not r.closure_pending
    with pytest.raises(workflow.ApprovalError, match="already rejected"):
        workflow.approve_closure(db_path, aid, reviewer)
    assert workflow.request_closure(db_path, in_review, operator) != aid  # unfrozen: a new request is possible


def test_closed_is_terminal(in_review, db_path, operator, reviewer):
    workflow.approve_closure(db_path, workflow.request_closure(db_path, in_review, operator), reviewer)
    assert workflow.allowed_next(Status.CLOSED, False) == []
    for target in (Status.NEW, Status.IN_REVIEW):
        with pytest.raises(workflow.TransitionError):
            workflow.change_status(db_path, in_review, target, operator)


def test_request_closure_requires_in_review(make_report, db_path, operator, pqc_payload):
    ref = make_report(pqc_payload).ref_id
    with pytest.raises(workflow.TransitionError, match="In Review"):
        workflow.request_closure(db_path, ref, operator)
