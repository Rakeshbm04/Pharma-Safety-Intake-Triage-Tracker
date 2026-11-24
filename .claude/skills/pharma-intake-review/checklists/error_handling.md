# Checklist: Error Handling & Validation

| ID | Check | How to verify |
|----|-------|---------------|
| EH-1 | Every mandatory field produces an explicit, field-named message when missing. | `validate_intake`; `test_each_core_field_is_mandatory` |
| EH-2 | Messages are context-sensitive (AE vs PQC wording; format hints such as `LOT-#####`). | `test_ae_requires_event_fields_with_context_message` |
| EH-3 | All errors are collected in one pass (operator sees every problem at once). | `test_empty_payload_reports_every_mandatory_field` |
| EH-4 | Cross-field rules enforced: onset ≤ received, received ≤ today, AE-only fields absent for PQC. | validation tests |
| EH-5 | Workflow violations raise typed `WorkflowError` subclasses with user-facing text, and the UI catches them (no stack traces). | `app/workflow.py`; `streamlit_app.py` `except workflow.WorkflowError` |
| EH-6 | Tool calls return structured `ToolError{tool, field, message}`; auto-repair notes are surfaced. | `app/agent/tools.py`; `test_tool_structured_error` |
| EH-7 | Argument errors vs. business-rule rejections are distinguished (only the former count towards the circuit breaker). | `ToolResult.invalid_args`; `test_workflow_error_is_not_an_argument_error` |
| EH-8 | Circuit breaker halts the run after > 2 identical invalid calls and is auditable and resettable. | `circuit_breaker.py`; breaker tests |
| EH-9 | DB writes are transactional; failure leaves no partial report or orphan audit row. | `db.transaction`; `test_failed_insert_rolls_back_audit` |
| EH-10 | `client.showErrorDetails = false` so raw exceptions are not shown to operators. | `.streamlit/config.toml` |
| EH-11 | RAG with no relevant context returns the explicit "no answer" message, never a guess. | `NO_CONTEXT_ANSWER`; `test_irrelevant_query_returns_no_answer` |
