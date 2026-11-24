# Manual UI Test Guide

A click-by-click script for checking the Streamlit app by hand. Each test lists the inputs and the **expected** result. Tick ✅ or ❌ as you go.

> All data is **fictional**. Never type real names, emails, phone numbers, dates of birth or patient details, even in tests designed to fail.

## 0. Start fresh

```bash
python scripts/seed_demo.py --reset     # 8 fictional reports, REF-2026-0001 … 0008
streamlit run streamlit_app.py          # opens http://localhost:8501
```

After seeding, the reports look like this (years follow the current date):

| Ref | Type | Product | Batch | Status |
|---|---|---|---|---|
| REF-2026-0001 | Suspected Adverse Event | Zentrovax 50 mg tablets | LOT-88392 | Closed |
| REF-2026-0002 | Suspected Adverse Event | Zentrovax 50 mg tablets | LOT-88392 | In Review |
| REF-2026-0003 | Product Quality Complaint | Zentrovax 50 mg tablets | LOT-88392 | In Review |
| REF-2026-0004 | Product Quality Complaint | Cardiolex 10 mg film-coated tablets | LOT-45127 | New |
| REF-2026-0005 | Product Quality Complaint | Neurolin 2 mL injection vial | LOT-60218 | In Review |
| REF-2026-0006 | Suspected Adverse Event | Dermacil 1% cream | LOT-31904 | New |
| REF-2026-0007 | Product Quality Complaint | Respiclear 100 mcg inhaler | LOT-77015 | New |
| REF-2026-0008 | Suspected Adverse Event | Gastrozen 20 mg capsules | LOT-50433 | New |

The **sidebar** controls the actor:

| Persona | Operator code | Role |
|---|---|---|
| Operator | `OP-01` | Intake Operator |
| Reviewer | `OP-07` | Safety Reviewer |

The sidebar should also show `RAG backend: neural (…)`, or `offline (…)` if the ML extras are not installed, and `Circuit breaker: closed ✅`.

---

## 1. Sidebar identity

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 1.1 | Set the operator code to `Jane Doe` | Red error "Operator code must look like OP-01…"; the rest of the app does not render | |
| 1.2 | Set it back to `OP-01` | The app renders normally | |

## 2. Intake: validation errors

Go to the **📝 New Intake** tab.

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 2.1 | Leave *Suspected Adverse Event* selected and click **Save report** with every field empty | "The report was **not saved**: N field(s) need attention", with one bullet each for Product name, Batch/Lot number, Reporter type, Description, Event description and Event onset date. The AE bullets mention "Suspected Adverse Event". | |
| 2.2 | Product `Zentrovax 50 mg tablets`, batch `LOT-123`, then Save | The batch error says "must match LOT-##### (exactly 5 digits, e.g. LOT-88392)" | |
| 2.3 | Batch `LOT-88392`, description `Caller jane@example.com reported a reaction.` | The description is rejected as containing "personal data (email address)" | |
| 2.4 | Set the event onset date **after** the date received | "Event onset date cannot be later than the date the report was received." | |
| 2.5 | Switch the type to *Product Quality Complaint* | The Adverse-event fields disappear (they are AE-only) | |

## 3. Intake: two valid fictional reports

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 3.1 | **PQC:** product `Cardiolex 10 mg film-coated tablets`, batch `LOT-45999`, reporter *Sales Representative*, description `Blister strip torn and two tablets chipped (fictional).` → Save | Green "Report **REF-2026-0009** saved with status **New**". Note it as **REF-A**. | |
| 3.2 | Click **Save report** again without changing anything | Yellow warning "This report was already saved as REF-A; no duplicate was created." | |
| 3.3 | **AE:** product `Zentrovax 50 mg tablets`, batch `LOT-88392`, reporter *Healthcare Professional*, description `Fictional nurse called about a reaction after a dose.`, event `Difficulty breathing for about twenty minutes.`, onset = yesterday → Save | Green "Report **REF-2026-0010** saved…". Note it as **REF-B**. | |

## 4. Dashboard: table, filters, search

Go to the **📊 Dashboard** tab.

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 4.1 | Look at the metric tiles | Total 10. The New / In Review / Closed counts match the table. | |
| 4.2 | Filter status = *New* | Only New reports are listed | |
| 4.3 | Also filter type = *Product Quality Complaint* | Only New PQCs are listed (the filters combine) | |
| 4.4 | Clear the filters and search `lot-88392` | REF-0001, 0002, 0003 and REF-B are listed (search is case-insensitive) | |
| 4.5 | Search `' OR 1=1 --` | "No reports match the current filters." (the input is treated as text, not SQL) | |

## 5. Lifecycle and HITL closure

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 5.1 | Clear the search, select **REF-A** | Details are shown; the only button is **▶ Move to In Review** (there is no direct close from New) | |
| 5.2 | Click **▶ Move to In Review** | Green "REF-A moved to In Review"; the button now reads **🔒 Request closure (HITL approval)** | |
| 5.3 | Click **🔒 Request closure** | Blue "Closure request #N … submitted"; a "Frozen" warning appears and there are no transition buttons | |
| 5.4 | Go to **✅ Closure Approvals** as `OP-01` / Intake Operator | Warning "Switch the sidebar role to Safety Reviewer"; the buttons are disabled | |
| 5.5 | Change the sidebar role to *Safety Reviewer* but keep `OP-01`, tick the confirmation and click **Approve closure** | Red "Four-eyes rule: the reviewer must be different from the person who requested closure." | |
| 5.6 | Change the sidebar to `OP-07` / Safety Reviewer, tick the confirmation and click **Approve closure** | Green "REF-A closed (approval #N recorded in the audit trail)" | |
| 5.7 | Back in the Dashboard, select REF-A | "Closed (final status)" with no buttons | |

## 6. Knowledge Assistant (router, RAG, guardrails, breaker)

Go to the **🔎 Knowledge Assistant** tab, as `OP-01` / Intake Operator.

| # | Message | Expected | ✅/❌ |
|---|---|---|---|
| 6.1 | `hello` | Help text. The control-plane expander shows route **DIRECT_RESPONSE**. | |
| 6.2 | `reports of shortness of breath for LOT-88392` | Excerpts cited `[REF-…]` for the breathing reports on LOT-88392, matched across "shortness of breath", "dyspnea" and "difficulty breathing", and the disclaimer. The **Hybrid RAG pipeline stages** expander shows four tabs (BM25, bi-encoder, RRF, cross-encoder) and the grounded prompt. | |
| 6.3 | `What is the weather in Paris tomorrow?` | "No sufficiently relevant records or SOP passages were found…" | |
| 6.4 | `what is the status of ref 2026 4` | Shows REF-2026-0004 with status New. The trace shows auto-repair `'ref 2026 4' -> 'REF-2026-0004'`. | |
| 6.5 | `is REF-2026-0004 still in review?` | A lookup only. The Dashboard still shows REF-0004 as **New**. | |
| 6.6 | `move REF-2026-0004 to in reveiw` | "moved from 'New' to 'In Review'". The trace shows the repair `'in reveiw' -> 'In Review'`. | |
| 6.7 | `send REF-2026-0004 back to new` | ❌ "Transition 'In Review' -> 'New' is not allowed… The lifecycle is one-way" | |
| 6.8 | `close REF-2026-0004` | "Closure … requires human approval. Request #N submitted…" The report is **not** closed (check the Approvals tab). | |
| 6.9 | `Did Zentrovax cause the rash? Should she stop taking it?` | The fixed refusal. Route **OUT_OF_SCOPE**, and **no** tool in the trace. | |
| 6.10 | `what does SOP-002 say about seriousness?` | Allowed: SOP excerpts are returned (an SOP meta-question is not a case decision) | |
| 6.11 | `move REF-2026-0999 to Archivedd`, sent **three times** | The first two return a validation error. The third shows "⛔ Circuit breaker OPEN…", and the sidebar shows the breaker open with a **Reset** button. | |
| 6.12 | Click **Reset circuit breaker** | The sidebar returns to "Circuit breaker: closed ✅" | |

## 7. Audit trail

Go to the **🧾 Audit Trail** tab.

| # | Action | Expected | ✅/❌ |
|---|---|---|---|
| 7.1 | Filter by REF-A | In order: REPORT_CREATED, DUPLICATE_REJECTED, STATUS_CHANGED, CLOSURE_REQUESTED, CLOSURE_ACTION_REJECTED (FOUR_EYES_VIOLATION), CLOSURE_APPROVED (by OP-07 / Safety Reviewer) | |
| 7.2 | Clear the filter | TRANSITION_REJECTED (BACKWARD_TRANSITION), TOOL_VALIDATION_FAILED and CIRCUIT_BREAKER_TRIPPED rows from section 6 are listed | |
| 7.3 | Read the Metadata column | Only codes and IDs, **never** description or event text | |
| 7.4 | Open the "Development governance log" expander | Hook events with `tool_name`, `file_path` and `content_bytes`, but no file content | |

## 8. Local-only checks

| # | Check | Expected | ✅/❌ |
|---|---|---|---|
| 8.1 | Look at the terminal when Streamlit starts | Only `URL: http://localhost:8501`. No "Network URL" or "External URL", and no usage-statistics notice. | |
| 8.2 | Trigger any error | A friendly message, never a raw Python traceback (`showErrorDetails = false`) | |

---

**Result:** ___ / 40 passed · Tester code: ______ · Date: ______
