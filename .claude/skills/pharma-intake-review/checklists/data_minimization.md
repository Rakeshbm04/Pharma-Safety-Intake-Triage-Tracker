# Checklist: Data Minimisation & Privacy

| ID | Check | How to verify |
|----|-------|---------------|
| DM-1 | No schema field, DB column or form input for patient name, initials, DOB, address, phone, email, MRN or medical history. | `app/models.py`, `app/db.py` SCHEMA, `streamlit_app.py` form; `tests/test_storage.py::test_schema_has_no_pii_columns` |
| DM-2 | Reporter captured only as a `ReporterType` enum value. | `ReporterType` in `app/models.py` |
| DM-3 | Operator identity is a pseudonymous code (`OP-##`) + role; no names/emails. | `Actor` model, sidebar validation |
| DM-4 | Free text (product, description, event) is screened by `detect_pii` and rejected on match. | `app/validation.py`; PII tests |
| DM-5 | `extra="forbid"` on intake and tool argument models (unknown fields cannot smuggle data in). | `IntakeReport`, `STRICT` config in `app/agent/tools.py` |
| DM-6 | Audit rows contain metadata only; `sanitize_metadata` whitelist drops free text. | `app/audit.py`; `test_create_writes_exactly_one_metadata_only_audit_row` |
| DM-7 | Hook log contains no file content / old_string / new_string / prompts. | `.claude/hooks/log_edit_event.py`; `tests/test_hook.py` |
| DM-8 | ReAct trace masks description fields (length only) when showing tool args. | `Orchestrator._tool` |
| DM-9 | Seed/test data is obviously fictional (`LOT-#####`, invented product names). | `scripts/seed_demo.py`, `tests/conftest.py` |
| DM-10 | No network egress at runtime (no HTTP clients; HF offline flags; Streamlit telemetry disabled; bound to localhost). | `static_checks.py`, `.streamlit/config.toml`, `app/rag/dense.py` |
