"""SQLite connection management and schema migration."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS ref_counters (
    year      INTEGER PRIMARY KEY,
    last_seq  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    ref_id            TEXT PRIMARY KEY,
    report_type       TEXT NOT NULL CHECK (report_type IN ('Suspected Adverse Event','Product Quality Complaint')),
    product_name      TEXT NOT NULL,
    batch_number      TEXT NOT NULL,
    date_received     TEXT NOT NULL,
    reporter_type     TEXT NOT NULL,
    description       TEXT NOT NULL,
    event_description TEXT,
    event_onset_date  TEXT,
    status            TEXT NOT NULL CHECK (status IN ('New','In Review','Closed')),
    closure_pending   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    created_by        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type);

CREATE TABLE IF NOT EXISTS closure_approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id        TEXT NOT NULL REFERENCES reports(ref_id),
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    decision      TEXT NOT NULL DEFAULT 'PENDING' CHECK (decision IN ('PENDING','APPROVED','REJECTED')),
    decided_by    TEXT,
    decided_at    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT NOT NULL,
    ref_id        TEXT,
    action        TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    actor_role    TEXT NOT NULL,
    old_status    TEXT,
    new_status    TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ref ON audit_log(ref_id);

-- Immutability: the audit trail is append-only.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent forward migrations for databases created by earlier versions."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
    if "fingerprint" not in cols:  # v2: duplicate-submission protection (SHA-256 hash, never text)
        conn.execute("ALTER TABLE reports ADD COLUMN fingerprint TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_fingerprint ON reports(fingerprint)")


def init_db(db_path: Path | str) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a connection and run the body inside BEGIN IMMEDIATE ... COMMIT (rollback on error)."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


@contextmanager
def read_only(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
