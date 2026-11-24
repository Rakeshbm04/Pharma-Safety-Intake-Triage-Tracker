"""Append-only audit trail.

Only metadata is recorded: reference ID, action, pseudonymous actor, status change
and a small whitelist of non-sensitive metadata keys. Descriptions and event text
are never written here.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from app.db import read_only
from app.models import Actor


class AuditAction(str, Enum):
    REPORT_CREATED = "REPORT_CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    CLOSURE_REQUESTED = "CLOSURE_REQUESTED"
    CLOSURE_APPROVED = "CLOSURE_APPROVED"
    CLOSURE_REJECTED = "CLOSURE_REJECTED"
    TRANSITION_REJECTED = "TRANSITION_REJECTED"
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"
    TOOL_VALIDATION_FAILED = "TOOL_VALIDATION_FAILED"
    TOOL_CALL_BLOCKED = "TOOL_CALL_BLOCKED"
    DUPLICATE_REJECTED = "DUPLICATE_REJECTED"
    CLOSURE_ACTION_REJECTED = "CLOSURE_ACTION_REJECTED"


# Whitelisted metadata keys -> allowed value types. Anything else is dropped.
ALLOWED_METADATA: dict[str, tuple[type, ...]] = {
    "report_type": (str,),
    "approval_id": (int,),
    "tool": (str,),
    "fields": (list,),
    "reason_code": (str,),
    "signature": (str,),
    "failure_count": (int,),
    "channel": (str,),
}
MAX_METADATA_STR = 64


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts_utc: str
    ref_id: Optional[str]
    action: str
    actor_id: str
    actor_role: str
    old_status: Optional[str]
    new_status: Optional[str]
    metadata: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_metadata(metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        types = ALLOWED_METADATA.get(key)
        if types is None or not isinstance(value, types):
            continue
        if isinstance(value, str):
            value = value[:MAX_METADATA_STR]
        if isinstance(value, list):  # list of field *names* only
            value = [str(v)[:MAX_METADATA_STR] for v in value][:20]
        clean[key] = value
    return clean


def record(
    conn: sqlite3.Connection,
    action: AuditAction,
    actor: Actor,
    ref_id: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    """Insert one audit row using the caller's connection (i.e. inside its transaction)."""
    cur = conn.execute(
        "INSERT INTO audit_log (ts_utc, ref_id, action, actor_id, actor_role, old_status, new_status, metadata_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            utc_now(),
            ref_id,
            action.value,
            actor.actor_id,
            actor.role.value,
            old_status,
            new_status,
            json.dumps(sanitize_metadata(metadata), sort_keys=True),
        ),
    )
    return int(cur.lastrowid)


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        ts_utc=row["ts_utc"],
        ref_id=row["ref_id"],
        action=row["action"],
        actor_id=row["actor_id"],
        actor_role=row["actor_role"],
        old_status=row["old_status"],
        new_status=row["new_status"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def list_entries(db_path: Path | str, ref_id: Optional[str] = None, limit: int = 500) -> list[AuditEntry]:
    with read_only(db_path) as conn:
        if ref_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE ref_id = ? ORDER BY id DESC LIMIT ?", (ref_id, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_entry(r) for r in rows]
