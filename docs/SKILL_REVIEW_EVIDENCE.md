# Evidence: `pharma-intake-review` skill invocation

**Skill:** [.claude/skills/pharma-intake-review/SKILL.md](../.claude/skills/pharma-intake-review/SKILL.md), with four checklists (43 items) and `scripts/static_checks.py`.
**Date:** 2026-11-24. **Scope:** full review of `app/`, `streamlit_app.py`, `scripts/`, `tests/`, `.claude/hooks/`, `.claude/settings.json` and `.streamlit/config.toml`.

**How it was invoked.** Claude Code discovers project skills at session start. Because the skill was created during the build session, it was executed by a fresh, independent reviewer agent. That agent was instructed to load `SKILL.md` and follow its procedure verbatim. It was given the code, not the author's conclusions, so its review was not anchored on the implementer's view. In later sessions, run `/pharma-intake-review`, or ask Claude to "review this project".

The skill procedure was followed in full: deterministic checks → checklist walk → scope check → severity-ranked report → fix BLOCKER/MAJOR with tests → re-verify.

---

## Round 1: initial review → verdict **REJECT**

Deterministic checks: pytest **121 passed** · static_checks **PASS** (7/7).

| # | Severity | Item | Finding (confirmed by reproduction) |
|---|---|---|---|
| 1 | **BLOCKER** | §2 / TC-7 | **Guardrail bypass.** The router removed `field: value` lines before the scope check, even for non-intake messages. `event: is Zentrovax causing the rash?` reached RAG. |
| 2 | MAJOR | §2 / TC-7 | Paraphrases evaded the regex rules: "lead to", "due to", "should the dose be lowered", "is this event serious", "within 15 days" (the `15[- ]day\b` pattern could not match "days"). |
| 3 | MAJOR | AU-5/6 | **A read-only question changed state.** "is REF-… still in review?" moved New→In Review, and "what is the closure status of REF-…" created a closure request and froze the report. |
| 4 | MAJOR | DM-6 | Caller-supplied argument names were written to audit metadata. A key `jane.doe@example.com` appeared in `fields`. |
| 5 | MAJOR | AU-4 | Rejected operations were not audited: direct-close attempts, invalid closure requests, wrong-role and four-eyes approval attempts, and breaker-blocked calls. |
| 6 | MINOR | DM-8 | ReAct-trace masking ran before key auto-repair, so `descripton` (typo) text was shown. |
| 7 | MINOR | EH-8 | A blocked-call message reported the global failure streak instead of the signature's count. |
| 8 | MINOR | AU-6 | The four-eyes rule relies on self-declared operator codes (there is no authentication). |

Clean areas: no partial-state transactions, no status writes outside `workflow.py`, no agent tool able to approve closure, hook is metadata-only, and RAG answers are verbatim and cited.

### Fixes

| # | Fix | Pinned by |
|---|---|---|
| 1 | Field lines are removed only when the message is a real intake; otherwise the whole text is scope-checked. | `test_field_prefix_cannot_bypass_guardrail` |
| 2 | Guardrail widened in round 1, then redesigned in round 3 (below). | `tests/test_guardrail_corpus.py` |
| 3 | Imperative-only parsing in round 1, then an allowlist grammar in round 3 (below). | `test_question_does_not_mutate_or_audit`, corpus |
| 4 | `_safe_field_names` maps unknown keys to `<unknown_field>`. | `test_unknown_key_names_not_audited` |
| 5 | `TRANSITION_REJECTED` (HITL_APPROVAL_REQUIRED), `CLOSURE_ACTION_REJECTED` (NOT_IN_REVIEW, ALREADY_PENDING, ROLE_NOT_REVIEWER, FOUR_EYES_VIOLATION, ALREADY_DECIDED, UNKNOWN_APPROVAL) and `TOOL_CALL_BLOCKED` are now audited in separate transactions. | `test_*_audited` (5 tests) |
| 6 | Masking by loose key match (`desc/event/narr/text/note`). | trace tests |
| 7 | `open_signatures: dict[signature → failures]`. | `test_blocked_message_reports_signature_count` |
| 8 | **Accepted limitation.** Documented in README "Known limitations" and REFLECTION §5 (needs SSO and server-side roles). | n/a |

## Round 2: re-verification → **APPROVE WITH FIXES**

- **Fixed:** 1, 4, 5, 6 and 7.
- **Still partial:** 2 and 3. Twelve fresh paraphrases still reached RAG, and "Update me on REF-… closure" froze a report.
- **New defect N1 (MAJOR).** The widened regexes refused legitimate lookups, for example "damaged packaging **due to** shipping", "reports **linked to** LOT-88392" and "what does SOP-002 say about seriousness?".

Lesson: adding phrases one at a time does not converge. Each widening created false refusals, and each narrowing let new paraphrases through.

## Round 3: re-verification → **APPROVE WITH FIXES**; structural redesign applied

The reviewer showed that the SOP-meta exemption disabled every check, and that status parsing still keyed on words found anywhere in the message. Both were redesigned at the policy level rather than patched.

**Guardrail (`app/agent/guardrails.py`) is now layered and intent-based:**
1. **Always refused, even inside SOP questions:** case-level causality, seriousness or reportability *decision questions*; dosing changes; personal medical advice; product-safety and market decisions.
2. **Refused outside SOP meta-questions:** regulatory-notification wording and therapy vocabulary.
3. **Default-deny:** any *question* that mentions a clinical term is refused unless it is phrased as a **records search** ("reports/cases/complaints of …"). A records search can only return verbatim recorded text through the extractive RAG.

Product-quality vocabulary is excluded from "clinical", so PQC causal wording ("tablets cracked because of humidity", "what caused the packaging defect") remains in scope.

**Status commands (`app/agent/orchestrator.py`) use an allowlist grammar.** The *whole* message must match a canonical command template, such as `move REF-x to <status>`, `request closure for REF-x` or `send REF-x back`. Anything else is a read-only lookup, so ambiguity fails safe. This also restored the five round-3 commands that had stopped working ("Set status of REF-x to In Review", "start the review of REF-x", …).

**Adversarial corpus:** [tests/test_guardrail_corpus.py](../tests/test_guardrail_corpus.py) pins every string from all three rounds in both directions:
- 54 must-refuse
- 31 must-allow
- 18 lookup phrasings that must never transition
- 21 commands that must work
- 4 end-to-end "no mutation, no audit row" checks on a temp DB

**Final deterministic checks:** pytest **315 passed / 0 failed** · static_checks **PASS**.

One judgement call differs from the reviewer: *"is it serious enough to move to in review?"* stays **refused**. Judging seriousness is out of scope under CLAUDE.md §2, even when it is framed as a workflow question.

## Known residual risk

A rule-based guardrail cannot be exhaustive. The mitigations are:
- the default-deny rule for clinical questions;
- an extractive-only RAG that never produces assessments or advice, only cited recorded text;
- the HITL gate, which means no agent path can close a report;
- the corpus test suite, which turns every new finding into a permanent regression test.

In production, a validated classifier should be evaluated against this corpus and layered with the rules, not used to replace them.

## Process note

During round-3 fixes, the reviewer-driven workflow also caught an implementation slip: a shell heredoc turned `\b` into literal backspace characters, so `\bme\b` matched inside "move". The bug was found because the tests failed. A permanent guard, `test_no_control_characters_in_source`, now prevents it from recurring.
