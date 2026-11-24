"""Dual-memory engine.

* ShortTermMemory - volatile ReAct trace (Thought -> Action -> Observation) for the current
                    session; bounded, never persisted.
* LongTermMemory  - durable facade over SQLite: report state, status transitions, audit history.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from app import audit, config, workflow
from app.audit import AuditEntry
from app.models import Report
from app.repository import ReportRepository

StepKind = Literal["Thought", "Action", "Observation", "Final"]


@dataclass(frozen=True)
class TraceStep:
    run_id: int
    kind: StepKind
    content: str
    ts_utc: str


class ShortTermMemory:
    def __init__(self, max_steps: int = config.SHORT_TERM_MEMORY_MAX_STEPS):
        self._steps: deque[TraceStep] = deque(maxlen=max_steps)
        self.run_id = 0

    def new_run(self) -> int:
        self.run_id += 1
        return self.run_id

    def add(self, kind: StepKind, content: str) -> TraceStep:
        step = TraceStep(self.run_id, kind, content, datetime.now(timezone.utc).isoformat())
        self._steps.append(step)
        return step

    def current_run(self) -> list[TraceStep]:
        return [s for s in self._steps if s.run_id == self.run_id]

    def all(self) -> list[TraceStep]:
        return list(self._steps)

    def clear(self) -> None:
        self._steps.clear()


class LongTermMemory:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.repo = ReportRepository(self.db_path)

    def report(self, ref_id: str) -> Report:
        return self.repo.get(ref_id)

    def history(self, ref_id: Optional[str] = None, limit: int = 200) -> list[AuditEntry]:
        return audit.list_entries(self.db_path, ref_id=ref_id, limit=limit)

    def pending_approvals(self) -> list[workflow.ClosureApproval]:
        return workflow.list_approvals(self.db_path, pending_only=True)
