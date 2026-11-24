> Plan Mode output (Phase II). Generated in Claude Code Plan Mode before any application code was written, then reviewed and **approved by the user** on 2026-11-24. One design decision was confirmed with the user during planning: neural RAG models are the default, with an automatic offline fallback. Copied verbatim from the plan file.

## Approval record

| Field | Value |
|---|---|
| Status | **Approved before any application code was written** (only `CLAUDE.md` existed) |
| Approved by: Project owner | Approval given in Claude Code Plan Mode (ExitPlanMode → "approve") |
| Date: 2026-11-24 | Plan Mode session, before Milestone A |
| Signature | ______________________ *(project owner signs before submission)* |

# Plan: Pharma Safety Intake & Triage Tracker

## Context
This is the capstone build of a **local, training-only** Streamlit app for fictional Adverse Event (AE) and Product Quality Complaint (PQC) intake. It replaces ad-hoc spreadsheets with validated intake, a filterable tracker, a `New -> In Review -> Closed` lifecycle with a human-in-the-loop (HITL) closure gate, and an immutable audit trail. It adds a 4-stage hybrid RAG pipeline (BM25 → bi-encoder → RRF → cross-encoder) over past reports and fictional SOPs, plus a deterministic agent control plane: router, Pydantic-validated tools, dual memory, HITL, and circuit breaker. The workspace is empty apart from `CLAUDE.md` (Phase I, done), which is the binding contract for scope, PII and architecture. Python 3.12 is available and PyPI is reachable.

**Decision (user):** Neural RAG models are the default (`sentence-transformers`: `all-MiniLM-L6-v2` bi-encoder, `cross-encoder/ms-marco-MiniLM-L-6-v2`), loaded from the local cache. They fall back automatically to offline NumPy encoders. Tests force `PHARMA_RAG_BACKEND=offline`.

## Layout
```
CLAUDE.md, README.md, requirements.txt (streamlit, pydantic>=2, numpy, pytest)
requirements-ml.txt (sentence-transformers)            # optional neural extra
streamlit_app.py                                        # UI only
app/config.py models.py validation.py db.py repository.py audit.py workflow.py
app/rag/  chunking.py bm25.py dense.py fusion.py rerank.py corpus.py pipeline.py
app/agent/ tools.py router.py memory.py circuit_breaker.py guardrails.py orchestrator.py
data/sops/*.md          # 6 fictional SOPs (intake, AE handling, PQC handling, batch investigation, escalation, audit)
scripts/seed_demo.py    # fictional demo reports
scripts/demo_walkthrough.py  # produces docs/DEMO_EVIDENCE.md
.claude/settings.json + .claude/hooks/log_edit_event.py   # Phase V
.claude/skills/pharma-intake-review/SKILL.md + checklists/{data_minimization,error_handling,auditability,test_coverage}.md
docs/PLAN.md (this plan), docs/DEMO_EVIDENCE.md, docs/REFLECTION.md (defense answers), docs/SKILL_REVIEW_EVIDENCE.md
tests/ conftest.py + test_models, test_validation, test_repository, test_audit, test_workflow,
       test_circuit_breaker, test_router, test_tools, test_rag_stages, test_rag_pipeline, test_hook
```

## Milestone A: Skeleton and governance (do first so edits get logged)
- Create the `.claude/settings.json` PostToolUse hook, matcher `Edit|Write|MultiEdit|NotebookEdit`, running `python .claude/hooks/log_edit_event.py`. The script reads the hook JSON from stdin and appends `{ts_utc, session_id, tool_name, file_path(repo-relative), event:"file_edit", content_bytes}` to `logs/agent_edit_audit.jsonl`. It **never** writes old or new content, descriptions or prompts. It exits 0 even if the payload is malformed so it never blocks a tool call. (The hook may only activate after the settings reload; I'll verify by checking the log.)
- Add `config.py` (paths, `DB_PATH`, RAG constants: `BM25_K1=1.5`, `BM25_B=0.75`, `CHUNK_TOKENS=512`, `CHUNK_OVERLAP=64`, `RETRIEVER_TOP_N=50`, `RRF_K=60`, `RERANK_THRESHOLD=0.40`, `FINAL_TOP_K=5`, `MIN_TOP_K=3`, `CB_MAX_CONSECUTIVE_FAILURES=2`), requirements, README stub, and a Streamlit app shell with tabs.

## Milestone B: Intake form and schemas (`models.py`, `validation.py`)
- Enums: `ReportType {Suspected Adverse Event, Product Quality Complaint}`, `Status {New, In Review, Closed}`, `ReporterType {Healthcare Professional, Patient/Consumer, Sales Representative, Other}`. No patient-identifier fields exist anywhere.
- `IntakeReport` (Pydantic v2, `extra="forbid"`) has product_name (2–100 chars), batch_number `^LOT-\d{5}$`, date_received (not in the future), reporter_type, description (20–2000 chars), and event_description + event_onset_date. The event fields are **required for AE** and must be empty for PQC. Onset must not be later than date_received.
- `validation.py`: `validate_intake(dict) -> (model|None, list[FieldError])` maps Pydantic errors to friendly, context-sensitive messages (e.g. "Event onset date is required for Suspected Adverse Event reports"). `detect_pii(text)` uses regexes for email, phone, SSN/ID-like numbers, DOB phrases, and "patient name/initials:" patterns, and rejects free text that contains them.
- The form shows AE extension fields only when type = AE, lists errors per field with `st.error`, and preserves inputs on failure.

## Milestone C: Validation and persistence (`db.py`, `repository.py`, `audit.py`)
- SQLite tables: `reports`, `ref_counters(year, last_seq)`, `audit_log(id, ts_utc, ref_id, action, actor_id, actor_role, old_status, new_status, metadata_json)`, `closure_approvals(id, ref_id, requested_by, requested_at, decision, decided_by, decided_at, reason_code)`.
- **Immutability:** `BEFORE UPDATE/DELETE` triggers on `audit_log` call `RAISE(ABORT, 'audit log is append-only')`.
- The reference ID `REF-{YYYY}-{seq:04d}` is allocated from `ref_counters` inside the same `BEGIN IMMEDIATE` transaction as the insert and its audit row, giving unique, gap-free refs per year.
- Every mutation (create, status change, closure request, approve, reject) writes exactly one audit row in the same transaction. `metadata_json` holds only allowed keys (whitelisted: report_type, fields_changed names, approval_id), never free text.
- Actor is a pseudonymous operator ID (`OP-01`) plus role, chosen in the sidebar. No names or emails.

## Milestone D: Dashboard and lifecycle (`workflow.py`, UI)
- State machine: `New->In Review`, `In Review->New` (send back), `In Review->Closed` **only via HITL**.
- `request_closure(ref, actor)` creates a pending approval and **freezes** the report: every other transition is rejected while a request is pending.
- `approve_closure(approval_id, reviewer)` requires role `Safety Reviewer` and a reviewer different from the requester (four-eyes). It then sets Closed and audits `CLOSURE_APPROVED`. `reject_closure` unfreezes the report and leaves it In Review. Closed is terminal.
- Dashboard: `st.dataframe` with multiselect filters for status and type, metric tiles (counts per status), a detail expander, and transition buttons. There is a "Pending Approvals" panel for reviewers and an "Audit Trail" tab (filter by ref ID) that is read-only.

## Milestone E: Hybrid RAG (`app/rag/`)
- **Corpus:** SOP markdown files plus DB reports rendered as `[ref] type | product | batch | status | description | event`. Each chunk keeps `source_id`, `source_type`, and `title`. The index is rebuilt when the report count or max updated_at changes (cached in `st.cache_resource`).
- **Chunking:** a 512-token window with 64 overlap, using the model tokenizer when neural and a regex word tokenizer when offline.
- **Stage 1, BM25:** own implementation (Okapi, k1/b from config) with a tokenizer that keeps `LOT-88392` and `REF-2026-0001` as single tokens. Returns top-50.
- **Stage 2, Dense:** `NeuralBiEncoder` (MiniLM, normalized embeddings) or `OfflineEncoder`, which uses medical-synonym expansion (dyspnea↔difficulty breathing/shortness of breath, urticaria↔hives/rash, pyrexia↔fever, emesis↔vomiting, …), TF-IDF, then NumPy SVD (LSA), with cosine similarity. Returns top-50.
- **Stage 3, RRF:** `score = Σ 1/(60 + rank)` with no score normalization, and per-stage ranks retained for transparency.
- **Stage 4, Rerank:** neural CrossEncoder logits → sigmoid → [0,1], or an offline scorer (synonym-aware query-term coverage + bigram/phrase match + exact-ID boost → [0,1]). Candidates below 0.40 are dropped, and up to the top 5 are returned.
- **Answer:** extractive and cited. The app builds the grounded generation prompt (shown in the UI for transparency) and composes the answer only from the top sentences of retained chunks, with `[source_id]` citations. If nothing passes the threshold it returns "No sufficiently relevant records found", so it never invents content. A non-clinical disclaimer is always appended.
- `pipeline.search(query) -> RAGResult` exposes all four stage outputs so the UI shows each stage in expanders.

## Milestone F: Agent control plane (`app/agent/`)
- **tools.py:** a registry of `submit_intake_report`, `get_report`, `update_status`, `request_closure`, `approve_closure`, `search_knowledge`. Each has a strict Pydantic args model. **Auto-repair** runs before validation: `lot 88392`/`lot88392` → `LOT-88392`, case-insensitive and difflib-fuzzy enum matching (`"in reveiw"` → `In Review`), and common date formats → ISO. Repairs are recorded. Anything that still fails returns a structured `ToolError{tool, field, message}`.
- **circuit_breaker.py:** the signature is `tool_name + sha256(canonical args JSON)`. It counts consecutive invalid calls per signature. A third consecutive identical invalid call (more than 2) trips the breaker, raises `CircuitOpenError`, halts the orchestrator run, and writes a `CIRCUIT_BREAKER_TRIPPED` audit row. A success or a different signature resets the count, and there is a manual reset.
- **memory.py:** `ShortTermMemory` holds a bounded ReAct trace (Thought/Action/Observation steps) in session state. `LongTermMemory` is a facade over repository and audit (SQLite).
- **router.py (deterministic, no LLM):** the scope guardrail runs first (causality, treatment, dosing advice, reportability, seriousness → `SCOPE_REFUSAL`). Then rules map to: `DIRECT_RESPONSE` (greetings/help), `STATUS_WORKFLOW` (REF id + status/close/review verbs), `INTAKE_FORM` (new/submit/log a report; parses `field: value` lines into tool args, otherwise points to the form), and `HYBRID_RAG` (default for lookups, SOP/LOT/history questions).
- **orchestrator.py:** `handle(message, actor)` routes, records the ReAct trace, executes the tool through validation → breaker → tool, and returns `AgentResponse{intent, text, trace, rag_result?}`. Closing via chat creates a pending HITL request only and never closes directly.
- UI "Safety Knowledge Assistant" tab: a chat, with expanders for route, ReAct trace and RAG stages.

## Milestone G: Skill, docs, evidence
- `SKILL.md` (frontmatter name/description, a step procedure that runs checklists and pytest and reports findings by severity) plus the 4 checklists. Invoke the skill on the codebase and save the findings and fixes to `docs/SKILL_REVIEW_EVIDENCE.md`.
- `scripts/seed_demo.py`: about 8 fictional reports (e.g. Zentrovax 50 mg LOT-88392 dyspnea AE, Cardiolex tablet discoloration PQC).
- `scripts/demo_walkthrough.py` uses a temp or demo DB to create 2 reports, move one New→In Review→request closure→approve, run a multi-stage RAG query ("patients reporting difficulty breathing with LOT-88392"), and print the audit trail into `docs/DEMO_EVIDENCE.md`. It also includes an excerpt of the hook log.
- `docs/REFLECTION.md` answers the 5 defense questions.

## Tests (pytest, offline backend, temp DB per test)
Schema validation (valid AE/PQC, each invalid field, AE-only fields, date ordering, PII rejection) · repository round-trip, unique sequential refs, one audit row per action, audit UPDATE/DELETE blocked by trigger · workflow allowed/forbidden transitions, frozen while pending, four-eyes, role check · circuit breaker trips on the 3rd identical invalid call, resets on success or a different signature · auto-repair cases · router intents incl. guardrail refusals · BM25 exact LOT match ranks first, k1/b effect · dense synonym match (difficulty breathing → dyspnea doc) · RRF formula values · reranker threshold filtering and top-k cap · full pipeline returns cited results and an empty answer for irrelevant queries · hook script writes metadata only (no content keys).

## Verification
1. `pip install -r requirements.txt` (+ `requirements-ml.txt`), then `python -m pytest -q`, which should be all green.
2. `python scripts/seed_demo.py`, then `streamlit run streamlit_app.py`. Smoke-check intake errors, the dashboard filters, the HITL close, a RAG query with stage expanders, and the audit tab (headless check via `streamlit run` + HTTP 200 and AppTest where feasible).
3. `python scripts/demo_walkthrough.py`, which generates `docs/DEMO_EVIDENCE.md`.
4. Confirm `logs/agent_edit_audit.jsonl` has entries for the edits and no content fields.

## Deviations during implementation

- **Lifecycle made strictly one-way** (post-build comparison against a reference implementation). The plan allowed `In Review → New` ("send back"). The spec says "New → In Review → Closed", so backward transitions were removed. They are now rejected and audited with reason `BACKWARD_TRANSITION`, and a 3×3 transition-matrix test pins the behaviour.

- **Duplicate-submission protection.** This was not in the plan. A SHA-256 fingerprint of the normalised intake fields has a UNIQUE index, so a double-clicked Save cannot create two reports. The duplicate is rejected and audited as `DUPLICATE_REJECTED`. Only the hash is stored.

- **Traceability and offline evidence.** These were added after the build: `docs/ACCEPTANCE.md` (requirement → code → test, checked by a test), a runtime network guard for the whole test session, and governance-document checks.

- **Status commands and guardrail redesigned** after three rounds of the review skill. See `docs/SKILL_REVIEW_EVIDENCE.md`.

- `approve_closure` is **not** exposed as an agent tool. Approval is a human-only action available in the UI, which makes the HITL gate stronger. The agent's `update_status`/`request_closure` tools can only *request* closure.

- The neural cross-encoder (MS MARCO, web-search domain) is **calibrated** by blending its score 50/50 with the domain-aware joint scorer (`RERANK_NEURAL_WEIGHT`). Without this it under-scored short records and kept a wrong-lot report (see docs/DEMO_EVIDENCE.md).

- Added `.streamlit/config.toml` to bind to localhost and disable Streamlit telemetry. The first launch showed both were on by default.

- Added Streamlit `AppTest` UI smoke tests (`tests/test_ui.py`).
