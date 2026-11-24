# CLAUDE.md — Pharma Safety Intake & Triage Tracker

Persistent system contract for any AI coding agent working in this repository.
Read this file fully before planning or editing code.

## 1. What this project is

A **local, training-only** Streamlit prototype that lets intake operators:

- record fictional *Suspected Adverse Event* (AE) and *Product Quality Complaint* (PQC) reports,
- enforce mandatory-field validation with explicit, context-sensitive errors,
- track reports on a filterable dashboard,
- move reports through the lifecycle `New -> In Review -> Closed` (Closed requires human approval),
- keep an append-only local audit trail,
- look up historical reports and fictional SOPs through a 4-stage hybrid RAG pipeline,
- drive all of the above through a deterministic agent control plane (router, validated tools, memory, HITL gate, circuit breaker).

## 2. Hard scope boundaries (NEVER violate)

The application and its code must **never**:

1. Assess causality between a product and an event.
2. Recommend, suggest or discuss medical treatment.
3. Determine regulatory reportability, seriousness classification for submission, or expedited-reporting timelines.
4. Present itself as a replacement for an enterprise pharmacovigilance system.
5. Make any clinical or regulatory decision. Status changes are workflow bookkeeping only, and closing requires an explicit human reviewer.

If a user query asks for any of the above, the router must return the standard refusal (`app/agent/guardrails.py::SCOPE_REFUSAL`) instead of calling RAG or a tool.

## 3. Data handling rules

- **Synthetic / fictional data only.** Seed data, tests and fixtures must use obviously fictional products (e.g. `Zentrovax 50 mg`) and batch numbers in the `LOT-#####` format.
- **No PII.** Never add fields for patient name, initials, date of birth, address, phone, email, MRN, or medical history. Reporter is captured only as a *type* (`Healthcare Professional`, `Patient/Consumer`, `Sales Representative`, `Other`).
- Free-text fields pass through `app/validation.py::detect_pii` and are rejected if they contain email addresses, phone numbers or ID-like patterns.
- **Audit logs record metadata only**: reference ID, action, actor role/ID, old and new status, timestamp. Never copy descriptions or event text into audit rows or hook logs.
- No network calls at runtime: no cloud APIs, remote databases or telemetry. Optional neural models must load from the local cache only, and the app must work fully with the built-in offline fallback encoders.

## 4. Architecture (keep layers separate)

```
app/
  config.py          # paths, constants, RAG parameters (single source of truth)
  models.py          # Pydantic schemas + enums (ReportType, Status, ReporterType)
  validation.py      # field-level validation, PII detection, friendly error messages
  db.py              # SQLite connection + schema migrations
  repository.py      # CRUD for reports; status transitions; never bypass this from UI
  audit.py           # append-only audit trail (SQLite table + no UPDATE/DELETE path)
  workflow.py        # status state machine + HITL approval gate
  rag/               # bm25.py, dense.py, fusion.py, rerank.py, chunking.py, pipeline.py
  agent/             # router.py, tools.py, memory.py, circuit_breaker.py, guardrails.py, orchestrator.py
streamlit_app.py     # UI only: no business logic, calls repository/workflow/agent
data/sops/           # fictional SOP markdown files used as RAG corpus
tests/               # pytest suite
```

Rules:
- The UI never writes SQL. All persistence goes through `repository.py` / `audit.py`.
- Every state-changing operation writes exactly one audit row in the **same transaction**.
- Status transitions are validated only in `workflow.py`. The lifecycle is strictly one-way. Allowed: `New->In Review` and `In Review->Closed` (HITL approval required). Backward transitions, skipping (`New->Closed`) and any change to a Closed report are rejected and audited.
- RAG parameters (`BM25_K1=1.2–1.5`, `BM25_B=0.75`, chunk 512/overlap 64, top-50 per retriever, `RRF_K=60`, rerank threshold `0.40`, final top 3–5) live in `config.py`. Do not hard-code them elsewhere.
- RAG answers are **extractive and cited**: return retrieved chunks with source IDs. Never generate content that does not appear in a retrieved chunk.

## 5. Coding guidelines

- Python 3.10+ with type hints everywhere and `from __future__ import annotations`.
- Pydantic v2 for all schemas; use `ConfigDict(strict=True, extra="forbid")` for tool argument models.
- Small pure functions; no global mutable state except Streamlit `st.session_state`.
- Use parameterized SQL only. Never use string-formatted SQL.
- Timestamps are UTC ISO-8601 (`datetime.now(timezone.utc).isoformat()`).
- Errors shown to users must be explicit and actionable, e.g. "Batch/Lot number must match LOT-##### (e.g. LOT-88392)". Never show raw stack traces in the UI.
- Keep dependencies minimal (see `requirements.txt`). Heavy ML libraries (`sentence-transformers`) are **optional** extras.

## 6. Testing conventions

- Framework: `pytest`. Run with `python -m pytest -q`.
- Each test uses an isolated temp SQLite DB (`tmp_path` fixture in `tests/conftest.py`). Never touch `data/pharma_intake.db` in tests.
- Tests must run offline and deterministically using the fallback encoders (`PHARMA_RAG_BACKEND=offline`).
- Required coverage areas: schema validation (valid + every invalid field), storage integrity (round-trip, unique refs, audit row per action), workflow transitions (allowed + forbidden + HITL), circuit breaker (trips after >2 identical invalid calls, resets on success), router intents, each RAG stage plus the full pipeline.
- Add or update tests in the same change as the code they cover.

## 7. Workflow for agents

1. For non-trivial changes, plan first (Plan Mode) and list affected modules.
2. Build incrementally and run `python -m pytest -q` after each milestone.
3. Before finishing, run the `pharma-intake-review` skill (`.claude/skills/pharma-intake-review/SKILL.md`).
4. File edits are logged by the PostToolUse hook to `logs/agent_edit_audit.jsonl` (metadata only). Do not disable it.
