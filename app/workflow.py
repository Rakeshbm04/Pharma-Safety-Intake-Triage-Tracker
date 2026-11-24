r"""Status lifecycle state machine and Human-in-the-Loop (HITL) closure gate.

    New --> In Review --> (closure requested, state frozen) --[human reviewer approves]--> Closed
                                                            \--[reviewer rejects]--> In Review

The lifecycle is strictly one-way (spec: "New -> In Review -> Closed"): no backward transitions,
no skipping, and Closed is terminal. Status changes are workflow bookkeeping only; they are never
clinical or regulatory decisions.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app import audit
from app.audit import AuditAction
from app.db import read_only, transaction
from app.models import Actor, Role, Status

LIFECYCLE_ORDER: tuple[Status, ...] = (Status.NEW, Status.IN_REVIEW, Status.CLOSED)
DIRECT_TRANSITIONS: frozenset[tuple[Status, Status]] = frozenset({(Status.NEW, Status.IN_REVIEW)})
HITL_TRANSITIONS: frozenset[tuple[Status, Status]] = frozenset({(Status.IN_REVIEW, Status.CLOSED)})


class WorkflowError(ValueError):
    """Base class for rejected workflow operations (message is user-facing)."""

    def __init__(self, message: str, reason_code: str = "REJECTED"):
        super().__init__(message)
        self.reason_code = reason_code


class TransitionError(WorkflowError):
    pass


class HITLRequiredError(WorkflowError):
    pass


class ApprovalError(WorkflowError):
    pass


@dataclass(frozen=True)
class ClosureApproval:
    id: int
    ref_id: str
    requested_by: str
    requested_at: str
    decision: str
    decided_by: Optional[str]
    decided_at: Optional[str]


def allowed_next(status: Status, closure_pending: bool) -> list[Status]:
    """Statuses reachable from `status` (Closed listed only as a HITL request target)."""
    if closure_pending or status is Status.CLOSED:
        return []
    targets = [b for (a, b) in DIRECT_TRANSITIONS if a is status]
    targets += [b for (a, b) in HITL_TRANSITIONS if a is status]
    return sorted(targets, key=lambda s: list(Status).index(s))


def _load(conn: sqlite3.Connection, ref_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT status, closure_pending FROM reports WHERE ref_id = ?", (ref_id,)).fetchone()
    if row is None:
        raise TransitionError(f"No report found with reference {ref_id}.")
    return row


def _reject(db_path: Path, ref_id: Optional[str], actor: Actor, old: Optional[str], new: Optional[str], reason: str,
            action: AuditAction = AuditAction.TRANSITION_REJECTED, approval_id: Optional[int] = None) -> None:
    """Record a rejected attempt (separate transaction, so it survives the raised error)."""
    meta: dict = {"reason_code": reason}
    if approval_id is not None:
        meta["approval_id"] = approval_id
    with transaction(db_path) as conn:
        audit.record(conn, action, actor, ref_id=ref_id, old_status=old, new_status=new, metadata=meta)


def change_status(db_path: Path | str, ref_id: str, target: Status, actor: Actor) -> Status:
    db_path = Path(db_path)
    with read_only(db_path) as conn:
        row = conn.execute("SELECT status, closure_pending FROM reports WHERE ref_id = ?", (ref_id,)).fetchone()
    if row is None:
        raise TransitionError(f"No report found with reference {ref_id}.")
    current = Status(row["status"])

    if row["closure_pending"]:
        _reject(db_path, ref_id, actor, current.value, target.value, "FROZEN_PENDING_APPROVAL")
        raise TransitionError(f"{ref_id} is frozen: a closure approval is pending human review.")
    if (current, target) in HITL_TRANSITIONS:
        _reject(db_path, ref_id, actor, current.value, target.value, "HITL_APPROVAL_REQUIRED")
        raise HITLRequiredError(
            f"Closing {ref_id} requires human reviewer approval. Submit a closure request instead."
        )
    if (current, target) not in DIRECT_TRANSITIONS:
        backward = LIFECYCLE_ORDER.index(target) < LIFECYCLE_ORDER.index(current)
        reason = "BACKWARD_TRANSITION" if backward else "INVALID_TRANSITION"
        _reject(db_path, ref_id, actor, current.value, target.value, reason)
        detail = " The lifecycle is one-way (New → In Review → Closed)." if backward else ""
        raise TransitionError(f"Transition '{current.value}' -> '{target.value}' is not allowed for {ref_id}.{detail}",
                              reason)

    with transaction(db_path) as conn:
        row = _load(conn, ref_id)  # re-check inside the write lock
        if Status(row["status"]) is not current or row["closure_pending"]:
            raise TransitionError(f"{ref_id} was modified concurrently; reload and try again.")
        conn.execute("UPDATE reports SET status = ?, updated_at = ? WHERE ref_id = ?",
                     (target.value, audit.utc_now(), ref_id))
        audit.record(conn, AuditAction.STATUS_CHANGED, actor, ref_id=ref_id,
                     old_status=current.value, new_status=target.value)
    return target


def request_closure(db_path: Path | str, ref_id: str, actor: Actor) -> int:
    """Freeze the report and open a pending HITL approval. Returns the approval id."""
    db_path = Path(db_path)
    try:
        return _request_closure(db_path, ref_id, actor)
    except WorkflowError as exc:  # the transaction rolled back; audit the rejected attempt separately
        _reject(db_path, ref_id, actor, None, Status.CLOSED.value, exc.reason_code,
                action=AuditAction.CLOSURE_ACTION_REJECTED)
        raise


def _request_closure(db_path: Path, ref_id: str, actor: Actor) -> int:
    with transaction(db_path) as conn:
        row = _load(conn, ref_id)
        current = Status(row["status"])
        if row["closure_pending"]:
            raise ApprovalError(f"A closure request for {ref_id} is already pending.", "ALREADY_PENDING")
        if current is not Status.IN_REVIEW:
            raise TransitionError(f"Only 'In Review' reports can be submitted for closure ({ref_id} is '{current.value}').",
                                  "NOT_IN_REVIEW")
        now = audit.utc_now()
        cur = conn.execute(
            "INSERT INTO closure_approvals (ref_id, requested_by, requested_at) VALUES (?, ?, ?)",
            (ref_id, actor.actor_id, now),
        )
        approval_id = int(cur.lastrowid)
        conn.execute("UPDATE reports SET closure_pending = 1, updated_at = ? WHERE ref_id = ?", (now, ref_id))
        audit.record(conn, AuditAction.CLOSURE_REQUESTED, actor, ref_id=ref_id,
                     old_status=current.value, new_status=current.value,
                     metadata={"approval_id": approval_id})
    return approval_id


def _decide(db_path: Path | str, approval_id: int, reviewer: Actor, approve: bool) -> ClosureApproval:
    db_path = Path(db_path)
    try:
        return _decide_tx(db_path, approval_id, reviewer, approve)
    except WorkflowError as exc:  # audit failed approvals (wrong role, four-eyes, already decided)
        with read_only(db_path) as conn:
            row = conn.execute("SELECT ref_id FROM closure_approvals WHERE id = ?", (approval_id,)).fetchone()
        _reject(db_path, row["ref_id"] if row else None, reviewer, None, None, exc.reason_code,
                action=AuditAction.CLOSURE_ACTION_REJECTED, approval_id=approval_id)
        raise


def _decide_tx(db_path: Path, approval_id: int, reviewer: Actor, approve: bool) -> ClosureApproval:
    if reviewer.role is not Role.REVIEWER:
        raise ApprovalError("Only a Safety Reviewer can approve or reject a closure request.", "ROLE_NOT_REVIEWER")
    with transaction(db_path) as conn:
        appr = conn.execute("SELECT * FROM closure_approvals WHERE id = ?", (approval_id,)).fetchone()
        if appr is None:
            raise ApprovalError(f"Closure request #{approval_id} does not exist.", "UNKNOWN_APPROVAL")
        if appr["decision"] != "PENDING":
            raise ApprovalError(f"Closure request #{approval_id} was already {appr['decision'].lower()}.", "ALREADY_DECIDED")
        if appr["requested_by"] == reviewer.actor_id:
            raise ApprovalError("Four-eyes rule: the reviewer must be different from the person who requested closure.",
                                "FOUR_EYES_VIOLATION")
        ref_id = appr["ref_id"]
        now = audit.utc_now()
        decision = "APPROVED" if approve else "REJECTED"
        conn.execute("UPDATE closure_approvals SET decision = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     (decision, reviewer.actor_id, now, approval_id))
        if approve:
            conn.execute("UPDATE reports SET status = ?, closure_pending = 0, updated_at = ? WHERE ref_id = ?",
                         (Status.CLOSED.value, now, ref_id))
            audit.record(conn, AuditAction.CLOSURE_APPROVED, reviewer, ref_id=ref_id,
                         old_status=Status.IN_REVIEW.value, new_status=Status.CLOSED.value,
                         metadata={"approval_id": approval_id})
        else:
            conn.execute("UPDATE reports SET closure_pending = 0, updated_at = ? WHERE ref_id = ?", (now, ref_id))
            audit.record(conn, AuditAction.CLOSURE_REJECTED, reviewer, ref_id=ref_id,
                         old_status=Status.IN_REVIEW.value, new_status=Status.IN_REVIEW.value,
                         metadata={"approval_id": approval_id})
        row = conn.execute("SELECT * FROM closure_approvals WHERE id = ?", (approval_id,)).fetchone()
    return ClosureApproval(**dict(row))


def approve_closure(db_path: Path | str, approval_id: int, reviewer: Actor) -> ClosureApproval:
    return _decide(db_path, approval_id, reviewer, approve=True)


def reject_closure(db_path: Path | str, approval_id: int, reviewer: Actor) -> ClosureApproval:
    return _decide(db_path, approval_id, reviewer, approve=False)


def list_approvals(db_path: Path | str, pending_only: bool = True) -> list[ClosureApproval]:
    sql = "SELECT * FROM closure_approvals"
    if pending_only:
        sql += " WHERE decision = 'PENDING'"
    with read_only(db_path) as conn:
        rows = conn.execute(sql + " ORDER BY id").fetchall()
    return [ClosureApproval(**dict(r)) for r in rows]
