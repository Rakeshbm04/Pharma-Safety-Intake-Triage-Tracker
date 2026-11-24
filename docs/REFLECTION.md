# Capstone Defense & Technical Reflection

Every answer below points to a concrete artefact in this repository.

---

## 1. Architectural boundary: what belongs in CLAUDE.md and what belongs in skills?

**CLAUDE.md holds rules that must apply to every interaction**, because it is loaded into every agent session automatically. That includes the non-negotiable scope limits (no causality, treatment, reportability or clinical decisions), the PII prohibition, the "no network at runtime" rule, the layer map (UI never writes SQL, transitions only in `workflow.py`), coding conventions and how to run tests. These rules are short, stable and always relevant. If they were left out of a session, the agent could cause harm (for example by adding a `patient_name` column).

**Skills hold procedures that are only needed for a particular task.** The `pharma-intake-review` skill contains four detailed checklists (43 items), a static-check script and a report template. Loading all of that into every session would waste context and dilute the always-on rules. A skill is loaded on demand when its description matches the task ("review this change"), so the detail costs nothing the rest of the time.

The rule of thumb used here is: **CLAUDE.md says *what must always be true*; a skill says *how to check it* for a specific workflow.** For example, CLAUDE.md states "audit logs record metadata only". Checklist item `DM-6` explains how to verify that (`sanitize_metadata` whitelist plus `test_create_writes_exactly_one_metadata_only_audit_row`). Putting both in one place would make CLAUDE.md long and procedural, or make the skill the only place the rule exists.

## 2. Planning efficiency: what did Plan Mode clarify before coding?

Plan Mode ([docs/PLAN.md](PLAN.md)) forced these decisions to be written down *before* any code existed:

- **Data flow and layering.** Intake dict → `validate_intake` → `IntakeReport` → `ReportRepository.create` (ref allocation, insert and audit row in **one** `BEGIN IMMEDIATE` transaction). Fixing this early meant atomicity was built in rather than retro-fitted, and it is now tested (`test_failed_insert_rolls_back_audit`, `test_references_unique_under_concurrency`).
- **Validation edge cases**, listed explicitly in the plan:
  - AE-only fields must be *absent* for a PQC.
  - Onset date must not be later than the received date; the received date must not be in the future.
  - Validation must collect *all* errors in one pass, so it cannot simply stop at the first failing check.
  - Free text must be screened for PII.
- **The HITL state model.** The plan defined "frozen while pending", the reviewer role and the four-eyes rule up front, which led to a separate `closure_approvals` table and a `closure_pending` flag rather than a fourth status.
- **Backend risk.** Planning surfaced that neural models need a download and conflict with "no network at runtime". The resulting decision, confirmed with the user, was to fetch the models once, load them local-only and keep an automatic offline fallback. Tests always use the offline backend, so the suite is deterministic.

Plan Mode also left a record against which implementation drift is visible. The four deviations (for example, calibrating the cross-encoder after demo evidence showed a wrong-lot result) are logged at the end of the plan, not hidden.

## 3. Governance control: why are hooks deterministic and model reasoning not?

The PostToolUse hook ([.claude/hooks/log_edit_event.py](../.claude/hooks/log_edit_event.py)) is executed **by the Claude Code harness, not by the model**. Whenever an `Edit|Write|MultiEdit|NotebookEdit` tool call completes, the harness runs the script and passes it the event JSON on stdin. This makes it deterministic in several ways:

- **It always runs.** The model cannot forget, skip or be talked out of logging. A prompt-based instruction such as "please log your edits" depends on the model's compliance and attention.
- **Same input gives the same output.** The record is a pure function of the payload: a fixed whitelist of keys (`ts_utc, event, session_id, tool_name, file_path, content_bytes, success`). `test_hook_is_deterministic` checks this.
- **Privacy is enforced in code.** Content, `old_string`, `new_string` and prompts are never read into the record, and `test_hook_logs_metadata_only` proves secret text never reaches the log. A model asked to "log without sensitive data" might still paraphrase a description.
- **It fails safe and is testable.** Malformed input exits 0 and logs nothing, so the hook can never block work. Its behaviour is covered by six unit tests, whereas model-driven evaluation can only be sampled, not proven.

The review-skill rounds ([SKILL_REVIEW_EVIDENCE.md](SKILL_REVIEW_EVIDENCE.md)) showed both sides of this trade-off:
- **Deterministic controls can be proven, but only for the inputs you thought of.** An independent reviewer found a guardrail bypass and a question that changed report state, despite 121 green tests.
- **Patching phrase by phrase did not converge.** What worked was changing the *policy*:
  - an **allowlist grammar** for state-changing commands, so anything unrecognised is a read-only lookup;
  - a **default-deny** rule for clinical questions that are not phrased as records searches.

  Every finding then became a permanent regression test (a 128-case corpus).
- **Model-driven review and deterministic controls complement each other.** The model is good at *finding* inputs nobody anticipated. Code is good at *guaranteeing* the behaviour once a case is known.

The same philosophy is used inside the application. The semantic router, guardrails, circuit breaker and HITL gate are all rule-based code. They make no LLM calls, so their behaviour can be unit-tested exhaustively.

## 4. Multi-stage RAG precision: how do the four stages reduce hallucination?

Each stage fixes a specific failure mode of the others. The demo query *"difficulty breathing on batch LOT-88392"* ([docs/DEMO_EVIDENCE.md](DEMO_EVIDENCE.md)) shows all four at work:

| Stage | What it contributes | Failure it prevents |
|---|---|---|
| **BM25** (k1 = 1.5, b = 0.75) | Exact tokens: `LOT-88392` is kept as one token, so batch and reference lookups are precise. | Dense models blur identifiers; `LOT-88392` and `LOT-45127` look alike to an embedding. |
| **Bi-encoder** (MiniLM, 512/64 chunks, cosine) | Meaning: "difficulty breathing" matches records that say *dyspnea* or *shortness of breath*. | BM25 misses synonyms and lay language entirely. |
| **RRF** (k = 60) | Combines both rankings using ranks only, so no score normalisation is needed and no single retriever dominates. | BM25 scores (0–7) and cosine similarities (0–1) are on incomparable scales. |
| **Cross-encoder** (≥ 0.40, top-5) | Joint scoring of each query–passage pair, then a hard relevance cut-off. | Recall-oriented candidates that are only loosely related reach the prompt. |

In the demo, the reranker kept exactly the three breathing reports on LOT-88392. Those three use three different phrasings, which the bi-encoder bridged. It dropped a discoloration complaint on the same lot and a report on a different lot.

**An honest qualification: retrieval alone cannot *eliminate* hallucination; it only makes the context precise.** The guarantee in this project comes from two further design choices:

1. **Extractive answering.** The answer consists only of sentences copied verbatim from kept chunks, each with a `[source_id]` citation. `test_answer_is_extractive` asserts that every excerpt appears in the retrieved text.
2. **Abstention.** If nothing clears the 0.40 threshold, the system says so (`NO_CONTEXT_ANSWER`) instead of answering. See the "weather in Paris" negative control.

Two lessons from building it:

- **Off-the-shelf rerankers are domain-shifted.** The MS MARCO cross-encoder is trained on web search. It under-scored short safety records (0.18 for a clearly relevant vial complaint) and could not know that a different lot number matters. Blending it with a domain-aware scorer that knows medical synonyms and enforces identifier consistency fixed both issues. In production this should be replaced by a reranker fine-tuned and calibrated on in-domain labelled pairs.
- **The remaining risk is retrieval misses** (false negatives), not fabrication. Coverage must therefore be monitored with a labelled evaluation set (recall@k, precision@k) before any real use.

## 5. Production readiness: what must change before production?

This prototype is intentionally a single-user, local training tool. Production use in a GxP pharmacovigilance context would require at least the following.

**Security and identity**
- Real authentication (SSO/OIDC + MFA) and role-based authorisation enforced server-side. Today the operator code and role are self-selected in the sidebar.
- TLS, a hardened reverse proxy, session management, CSRF/XSRF protection and rate limiting.
- Encryption at rest (database and backups) with keys in an HSM or KMS, plus secrets management.
- Dependency scanning, an SBOM, signed builds, and model provenance and integrity checks for the ML weights.

**Compliance (e.g. 21 CFR Part 11 / EU GMP Annex 11, GVP Module VI, GDPR)**
- Computerised system validation (URS/FRS, IQ/OQ/PQ, traceability matrix) and formal change control.
- Electronic signatures bound to identity for closure approval (re-authentication at signing, with the meaning of the signature recorded).
- An audit trail with tamper *evidence* (hash-chained or WORM storage, synchronised trusted time) instead of SQLite triggers, which a DBA could drop.
- Retention, archival, legal hold and privacy impact assessment. Real data would contain personal and health data, so it needs lawful basis, minimisation and pseudonymisation, and data subject rights handling.
- Integration with the validated safety database (E2B(R3)) and quality systems. Regulatory timelines and case processing must stay in those systems; this tool would be an intake front-end only.

**Infrastructure and operations**
- Replace SQLite with a managed RDBMS (PostgreSQL) supporting HA, point-in-time recovery, tested backups and DR targets (RPO/RTO).
- Containerised deployment, environment separation (dev/val/prod), infrastructure as code and monitoring/alerting. Operational logs must keep the same metadata-only discipline.
- A RAG index service with incremental re-indexing, a vector store with access control (users must only retrieve reports they may see), and an evaluation harness with drift monitoring.
- If an LLM is added for generation: a privately hosted model or one under a DPA, prompt-injection defences on retrieved content, grounding and citation verification, and continued extractive fallback and abstention.

**Process**
- Independent QA and security review (penetration test), user acceptance testing with SOP-trained staff, and training records.
- Keep the deterministic governance controls (hooks, router, HITL, circuit breaker) and extend the hook pattern into CI so that every change is reviewed and recorded.
