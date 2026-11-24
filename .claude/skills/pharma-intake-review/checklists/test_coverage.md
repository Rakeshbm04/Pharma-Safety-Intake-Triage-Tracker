# Checklist: Test Coverage

Every item must map to at least one passing test (`python -m pytest -q`).

| ID | Area | Required tests | File |
|----|------|----------------|------|
| TC-1 | Schema validation | valid AE + PQC; each mandatory field missing; batch regex; AE-only fields; date ordering; future date; enum errors; extra fields; PII | `tests/test_validation.py` |
| TC-2 | Storage integrity | round-trip; ref format & sequence; uniqueness under concurrency; filters + search; rollback on failure; no PII columns; duplicate submission (incl. concurrent, hash-only, no ref consumed); migration of old DB; SQL-injection / unicode | `tests/test_storage.py`, `tests/test_edge_cases.py` |
| TC-3 | Audit | one row per action; metadata-only; append-only triggers; whitelist; rejected actions audited (transition, closure, duplicate, blocked call) | `tests/test_storage.py`, `tests/test_workflow.py`, `tests/test_review_regressions.py`, `tests/test_edge_cases.py` |
| TC-4 | Workflow / HITL | full 3×3 transition matrix (one-way; backward rejected); HITL required; freeze; role check; four-eyes; reject; terminal Closed; concurrent transitions (one wins) | `tests/test_workflow.py`, `tests/test_edge_cases.py` |
| TC-5 | Tool validation | strict schema; auto-repair of batch/ref/date/enum/key typos; structured errors | `tests/test_agent.py` |
| TC-6 | Circuit breaker | trips on 3rd identical invalid call; stays open; resets on success / different signature / manual; orchestrator halts; audited | `tests/test_agent.py` |
| TC-7 | Router & guardrails | each intent; refusal AND allow corpus (causality, treatment, dosing, reportability, safety; PQC + SOP-meta + records-search allowed); questions never transition | `tests/test_agent.py`, `tests/test_guardrail_corpus.py`, `tests/test_review_regressions.py` |
| TC-8 | Memory | ReAct trace shape; bounded short-term memory; long-term history | `tests/test_agent.py` |
| TC-9 | RAG stages | tokenizer IDs; synonyms; 512/64 windows; BM25 exact match, k1/b effects; dense synonym match; RRF formula; rerank threshold + top-k; ID penalty | `tests/test_rag.py` |
| TC-10 | RAG pipeline | end-to-end citations; extractive answer; negative query; index refresh; neural backend (marker `neural`) | `tests/test_rag.py` |
| TC-11 | Governance hook | registration; metadata-only; ignores non-edit tools and bad input; deterministic | `tests/test_hook.py` |
| TC-12 | UI smoke | renders; empty submit errors; valid submit; duplicate warning; dashboard table, filters and search; invalid operator code | `tests/test_ui.py` |
| TC-13 | Offline & governance | runtime network guard (connect + DNS); no network/LLM imports or URLs; local-only Streamlit config; RAG constants only in config; CLAUDE.md / PLAN / skill / reflection / demo / ACCEPTANCE checks (cited tests must exist) | `tests/test_offline_and_governance.py` |

Tests must use a temp DB (`tmp_path`) and the offline RAG backend (set in `tests/conftest.py`).
