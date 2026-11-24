---
name: pharma-intake-review
description: Review changes to the Pharma Safety Intake & Triage Tracker against its data-minimisation, error-handling, auditability and test-coverage checklists. Use before finishing any change to app/, streamlit_app.py, scripts/ or tests/, or when asked to review this project.
---

# Pharma Intake Review Skill

A focused, repeatable code review for this repository. It complements `CLAUDE.md`
(always-on rules and architecture) with **task-specific checklists** that only need to be
loaded when reviewing.

## When to use
- Before declaring a milestone or change complete.
- When asked to "review", "audit" or "check compliance" of this project.
- After touching validation, persistence, workflow, audit, RAG or agent code.

## Procedure
1. **Scope the review.** List changed files (`git diff --name-only` if a repo exists, otherwise
   the files touched in this session or the whole `app/` package for a full review).
2. **Run the deterministic checks first** (evidence beats opinion):
   ```bash
   python -m pytest -q
   python .claude/skills/pharma-intake-review/scripts/static_checks.py
   ```
   `static_checks.py` greps for forbidden PII columns/fields, string-formatted SQL, audit
   UPDATE/DELETE paths, raw tracebacks in the UI, network calls and hard-coded RAG constants.
3. **Walk each checklist** and mark every item PASS / FAIL / N/A with a file:line reference:
   - [checklists/data_minimization.md](checklists/data_minimization.md)
   - [checklists/error_handling.md](checklists/error_handling.md)
   - [checklists/auditability.md](checklists/auditability.md)
   - [checklists/test_coverage.md](checklists/test_coverage.md)
4. **Check scope boundaries** from `CLAUDE.md` §2: no causality, treatment, reportability or
   clinical decisions anywhere (UI copy, RAG answers, router responses).
5. **Report findings** using the template below, ordered by severity:
   - **BLOCKER**: violates a CLAUDE.md hard rule (PII, scope, audit integrity, network).
   - **MAJOR**: incorrect behaviour or missing mandatory test.
   - **MINOR**: maintainability, clarity, non-critical UX.
6. **Fix or hand back.** Fix BLOCKER/MAJOR items (with a test) before finishing; record the
   review in `docs/SKILL_REVIEW_EVIDENCE.md`.

## Report template
```
## pharma-intake-review: <date> · scope: <files>
Deterministic checks: pytest <N passed / M failed> · static_checks <PASS/FAIL>
| # | Severity | Checklist item | Location | Finding | Action |
|---|----------|----------------|----------|---------|--------|
Verdict: APPROVE | APPROVE WITH FIXES | REJECT
```
