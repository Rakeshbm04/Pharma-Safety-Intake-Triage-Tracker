"""Report persistence. The UI and agent tools must go through this module (never raw SQL)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from app import audit
from app.audit import AuditAction
from app.db import init_db, read_only, transaction
from app.models import Actor, IntakeReport, Report, ReportType, Status


class ReportNotFoundError(LookupError):
    pass


class DuplicateSubmissionError(ValueError):
    """The same intake content was already saved (e.g. a double-clicked Save button)."""

    def __init__(self, existing_ref: str):
        self.existing_ref = existing_ref
        super().__init__(f"This report was already saved as {existing_ref}; no duplicate was created.")


def _norm(value: object) -> str:
    return " ".join(str(value).split()).casefold() if value is not None else ""


def fingerprint(intake: IntakeReport) -> str:
    """SHA-256 over the normalised intake fields. Only the hash is stored, never extra text."""
    data = intake.model_dump(mode="json")
    canonical = json.dumps({k: _norm(v) for k, v in sorted(data.items())}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _next_ref_id(conn: sqlite3.Connection, year: int) -> str:
    row = conn.execute("SELECT last_seq FROM ref_counters WHERE year = ?", (year,)).fetchone()
    # Never trust the counter alone: if it is missing/behind (e.g. a migrated DB), continue after
    # the highest reference already stored for this year so IDs are never reused.
    top = conn.execute(
        "SELECT MAX(CAST(substr(ref_id, 10) AS INTEGER)) AS m FROM reports WHERE ref_id LIKE ?", (f"REF-{year}-%",)
    ).fetchone()
    seq = max(row["last_seq"] if row else 0, top["m"] or 0) + 1
    if seq > 9999:
        raise OverflowError(f"Reference sequence exhausted for {year}")
    conn.execute(
        "INSERT INTO ref_counters (year, last_seq) VALUES (?, ?)"
        " ON CONFLICT(year) DO UPDATE SET last_seq = excluded.last_seq",
        (year, seq),
    )
    return f"REF-{year}-{seq:04d}"


def _row_to_report(row: sqlite3.Row) -> Report:
    data = dict(row)
    data.pop("fingerprint", None)  # internal dedup hash, not part of the domain model
    data["closure_pending"] = bool(data["closure_pending"])
    return Report.model_validate(data)


class ReportRepository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        init_db(self.db_path)

    # ---- writes -----------------------------------------------------------
    def create(self, intake: IntakeReport, actor: Actor, channel: str = "form") -> Report:
        now = audit.utc_now()
        fp = fingerprint(intake)
        try:
            return self._insert(intake, actor, channel, now, fp)
        except DuplicateSubmissionError as dup:  # txn rolled back; audit the attempt (metadata only)
            with transaction(self.db_path) as conn:
                audit.record(conn, AuditAction.DUPLICATE_REJECTED, actor, ref_id=dup.existing_ref,
                             metadata={"report_type": intake.report_type.value, "channel": channel})
            raise

    def _insert(self, intake: IntakeReport, actor: Actor, channel: str, now: str, fp: str) -> Report:
        with transaction(self.db_path) as conn:  # BEGIN IMMEDIATE: check + insert under one write lock
            dup = conn.execute("SELECT ref_id FROM reports WHERE fingerprint = ?", (fp,)).fetchone()
            if dup is not None:
                raise DuplicateSubmissionError(dup["ref_id"])
            ref_id = _next_ref_id(conn, date.today().year)
            conn.execute(
                "INSERT INTO reports (ref_id, report_type, product_name, batch_number, date_received,"
                " reporter_type, description, event_description, event_onset_date, status,"
                " closure_pending, created_at, updated_at, created_by, fingerprint)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    ref_id,
                    intake.report_type.value,
                    intake.product_name,
                    intake.batch_number,
                    intake.date_received.isoformat(),
                    intake.reporter_type.value,
                    intake.description,
                    intake.event_description,
                    intake.event_onset_date.isoformat() if intake.event_onset_date else None,
                    Status.NEW.value,
                    now,
                    now,
                    actor.actor_id,
                    fp,
                ),
            )
            audit.record(
                conn,
                AuditAction.REPORT_CREATED,
                actor,
                ref_id=ref_id,
                new_status=Status.NEW.value,
                metadata={"report_type": intake.report_type.value, "channel": channel},
            )
        return self.get(ref_id)

    # ---- reads ------------------------------------------------------------
    def get(self, ref_id: str) -> Report:
        with read_only(self.db_path) as conn:
            row = conn.execute("SELECT * FROM reports WHERE ref_id = ?", (ref_id,)).fetchone()
        if row is None:
            raise ReportNotFoundError(f"No report found with reference {ref_id}")
        return _row_to_report(row)

    def list(
        self,
        statuses: Optional[Iterable[Status]] = None,
        report_types: Optional[Iterable[ReportType]] = None,
        search: Optional[str] = None,
    ) -> list[Report]:
        """Filter by status/type and an optional search over reference, product and batch.

        All values are bound parameters; LIKE wildcards in `search` are escaped so they match literally.
        """
        clauses, params = [], []
        if search and search.strip():
            term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(ref_id LIKE ? ESCAPE '\\' OR product_name LIKE ? ESCAPE '\\' OR batch_number LIKE ? ESCAPE '\\')")
            params.extend([f"%{term}%"] * 3)
        if statuses:
            vals = [s.value for s in statuses]
            clauses.append(f"status IN ({','.join('?' * len(vals))})")
            params.extend(vals)
        if report_types:
            vals = [t.value for t in report_types]
            clauses.append(f"report_type IN ({','.join('?' * len(vals))})")
            params.extend(vals)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with read_only(self.db_path) as conn:
            rows = conn.execute(f"SELECT * FROM reports{where} ORDER BY ref_id DESC", params).fetchall()
        return [_row_to_report(r) for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        with read_only(self.db_path) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM reports GROUP BY status").fetchall()
        counts = {s.value: 0 for s in Status}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    def fingerprint(self) -> tuple[int, str]:
        """Cheap change detector used to invalidate the RAG index."""
        with read_only(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS m FROM reports").fetchone()
        return int(row["n"]), str(row["m"])
