# Checklist: Auditability & Workflow Integrity

| ID | Check | How to verify |
|----|-------|---------------|
| AU-1 | Every state-changing operation writes exactly one audit row **in the same transaction**. | `repository.create`, `workflow.*` use `transaction()` + `audit.record(conn, …)` |
| AU-2 | Audit table is append-only: UPDATE/DELETE blocked by DB triggers. | `db.SCHEMA` triggers; `test_audit_log_is_append_only` |
| AU-3 | Audit rows carry UTC ISO-8601 timestamp, ref, action, actor code, role, old/new status. | `audit_log` columns |
| AU-4 | Rejected transitions and tool validation failures / breaker trips are audited too. | `TRANSITION_REJECTED`, `TOOL_VALIDATION_FAILED`, `CIRCUIT_BREAKER_TRIPPED` |
| AU-5 | Transitions are enforced only in `workflow.py`; UI and tools never update `status` directly. | `grep "UPDATE reports" app/` → only in `workflow.py` |
| AU-6 | `In Review → Closed` only via HITL: request freezes the record; Safety Reviewer approval; four-eyes rule. | workflow tests |
| AU-7 | Agent tools cannot approve closure (human-only action). | `TOOLS` registry has no approve tool |
| AU-8 | Reference IDs are unique and allocated inside the insert transaction. | `_next_ref_id`; concurrency test |
| AU-9 | PostToolUse hook is registered and logs every Edit/Write deterministically. | `.claude/settings.json`; `tests/test_hook.py` |
| AU-10 | RAG answers cite `[source_id]` for every excerpt and include the non-clinical disclaimer. | `compose_extractive_answer`; `test_pipeline_end_to_end` |
