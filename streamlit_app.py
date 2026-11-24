"""Pharma Safety Intake & Triage Tracker: Streamlit UI (presentation layer only).

All business logic lives in `app/`: validation, repository, workflow, audit, RAG and agent.
Run:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from app import audit, config, workflow
from app.agent.circuit_breaker import CircuitBreaker
from app.agent.memory import ShortTermMemory
from app.agent.orchestrator import Orchestrator
from app.models import Actor, ReporterType, ReportType, Role, Status
from app.rag.service import KnowledgeBase
from app.repository import DuplicateSubmissionError, ReportRepository
from app.validation import FIELD_LABELS, validate_intake

st.set_page_config(page_title="Pharma Safety Intake & Triage Tracker", page_icon="🧪", layout="wide")

DB_PATH = config.DB_PATH
EDIT_LOG = config.ROOT_DIR / "logs" / "agent_edit_audit.jsonl"


# ---- shared resources ---------------------------------------------------------
@st.cache_resource(show_spinner="Building hybrid RAG index (BM25 + bi-encoder)…")
def get_knowledge_base(db_path: str, backend: str) -> KnowledgeBase:
    return KnowledgeBase(db_path, backend=backend)


def repo() -> ReportRepository:
    return ReportRepository(DB_PATH)


def flash(kind: str, msg: str) -> None:
    st.session_state.setdefault("flash", []).append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("flash", []):
        getattr(st, kind)(msg)


ss = st.session_state
ss.setdefault("short_term", ShortTermMemory())
ss.setdefault("breaker", CircuitBreaker())
ss.setdefault("chat", [])

# ---- sidebar: pseudonymous actor ----------------------------------------------
with st.sidebar:
    st.header("🧪 Safety Intake Tracker")
    st.caption("Training prototype · **fictional data only** · runs fully locally")
    actor_id = st.text_input("Operator code", value="OP-01", key="operator_code",
                             help="Pseudonymous code only (format OP-##). Never enter a real name or email.")
    role = st.radio("Role", [r.value for r in Role], index=0, key="role")
    if not re.fullmatch(r"OP-\d{2,4}", actor_id.strip()):
        st.error("Operator code must look like OP-01 (pseudonymous code, no names).")
        st.stop()
    ss["actor_id"] = actor_id.strip()
    ACTOR = Actor(actor_id=ss["actor_id"], role=Role(role))

    st.divider()
    kb = get_knowledge_base(str(DB_PATH), config.RAG_BACKEND)
    st.caption(f"RAG backend: `{kb.pipeline().backend_detail}`")
    cb: CircuitBreaker = ss["breaker"]
    if cb.open_signatures:
        st.error(f"Circuit breaker OPEN ({len(cb.open_signatures)} signature(s) blocked)")
        if st.button("Reset circuit breaker"):
            cb.reset()
            st.rerun()
    else:
        st.caption("Circuit breaker: closed ✅")
    st.divider()
    st.info(config.DISCLAIMER, icon="⚠️")

st.title("Pharma Safety Intake & Triage Tracker")
show_flash()

tab_intake, tab_dash, tab_appr, tab_agent, tab_audit = st.tabs(
    ["📝 New Intake", "📊 Dashboard", "✅ Closure Approvals", "🔎 Knowledge Assistant", "🧾 Audit Trail"]
)

# ---- 1. intake form -----------------------------------------------------------
with tab_intake:
    st.subheader("New report intake")
    st.caption("A unique reference (REF-YYYY-NNNN) is generated automatically on save. "
               "Do not enter patient names, initials, dates of birth, contact details or medical history.")
    rtype = st.radio("Report type *", [t.value for t in ReportType], horizontal=True, key="intake_type")
    is_ae = rtype == ReportType.ADVERSE_EVENT.value
    with st.form("intake_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        product = c1.text_input("Product name *", key="f_product", placeholder="e.g. Zentrovax 50 mg tablets")
        batch = c2.text_input("Batch/Lot number *", key="f_batch", placeholder="LOT-88392", help="Format LOT-##### (5 digits)")
        received = c1.date_input("Date received *", value=date.today(), max_value=date.today())
        reporter = c2.selectbox("Reporter type *", [r.value for r in ReporterType], index=None, key="f_reporter",
                                placeholder="Select reporter type…")
        description = st.text_area("Description *", height=110, key="f_description",
                                   placeholder="What was reported? (fictional, de-identified)")
        ev_desc, onset = None, None
        if is_ae:
            st.markdown("**Adverse event details** (required for Suspected Adverse Event)")
            ev_desc = st.text_area("Event description *", height=90, key="f_event", placeholder="Event as described by the reporter")
            onset = st.date_input("Event onset date *", value=None, max_value=date.today())
        submitted = st.form_submit_button("Save report", type="primary")

    if submitted:
        payload = {
            "report_type": rtype, "product_name": product, "batch_number": batch,
            "date_received": received.isoformat() if received else None, "reporter_type": reporter,
            "description": description,
        }
        if is_ae:
            payload["event_description"] = ev_desc
            payload["event_onset_date"] = onset.isoformat() if onset else None
        model, errors = validate_intake(payload)
        if errors:
            st.error(f"The report was **not saved**: {len(errors)} field(s) need attention.")
            for e in errors:
                st.markdown(f"- **{e.label}**: {e.message}")
        else:
            try:
                report = repo().create(model, ACTOR, channel="form")
                st.success(f"Report **{report.ref_id}** saved with status **{report.status.value}**.")
            except DuplicateSubmissionError as dup:
                st.warning(f"{dup} Open it from the Dashboard if you need to review it.")

# ---- 2. dashboard -------------------------------------------------------------
with tab_dash:
    counts = repo().counts_by_status()
    m = st.columns(4)
    m[0].metric("Total", sum(counts.values()))
    for col, s in zip(m[1:], Status):
        col.metric(s.value, counts[s.value])

    f1, f2, f3 = st.columns([2, 2, 2])
    status_filter = f1.multiselect("Filter by status", [s.value for s in Status])
    type_filter = f2.multiselect("Filter by report type", [t.value for t in ReportType])
    search = f3.text_input("Search reference / product / batch", key="dash_search",
                           placeholder="e.g. REF-2026-0001 or LOT-88392")
    reports = repo().list(statuses=[Status(s) for s in status_filter] or None,
                          report_types=[ReportType(t) for t in type_filter] or None,
                          search=search or None)
    if not reports:
        st.info("No reports match the current filters.")
    else:
        df = pd.DataFrame([{
            "Reference": r.ref_id, "Type": r.report_type.value, "Product": r.product_name,
            "Batch/Lot": r.batch_number, "Received": r.date_received.isoformat(),
            "Reporter": r.reporter_type.value, "Status": r.status.value,
            "Closure pending": "⏳" if r.closure_pending else "", "Updated (UTC)": r.updated_at[:19],
        } for r in reports])
        st.dataframe(df, hide_index=True, width="stretch")

        st.markdown("#### Report details & status workflow")
        ref = st.selectbox("Select report", [r.ref_id for r in reports])
        r = next(x for x in reports if x.ref_id == ref)
        d1, d2 = st.columns([3, 2])
        with d1:
            st.markdown(f"**{r.ref_id}** · {r.report_type.value} · status **{r.status.value}**")
            st.markdown(f"**Product:** {r.product_name} · **Batch:** `{r.batch_number}` · "
                        f"**Received:** {r.date_received} · **Reporter:** {r.reporter_type.value}")
            st.markdown(f"**Description:** {r.description}")
            if r.event_description:
                st.markdown(f"**Event:** {r.event_description} · **Onset:** {r.event_onset_date}")
        with d2:
            if r.closure_pending:
                st.warning("Frozen: a closure request is awaiting Safety Reviewer approval.")
            elif r.status is Status.CLOSED:
                st.success("Closed (final status).")
            for target in workflow.allowed_next(r.status, r.closure_pending):
                label = {Status.IN_REVIEW: "▶ Move to In Review",
                         Status.CLOSED: "🔒 Request closure (HITL approval)"}[target]
                if st.button(label, key=f"tr-{r.ref_id}-{target.value}"):
                    try:
                        if target is Status.CLOSED:
                            aid = workflow.request_closure(DB_PATH, r.ref_id, ACTOR)
                            flash("info", f"Closure request #{aid} for {r.ref_id} submitted; awaiting reviewer approval.")
                        else:
                            workflow.change_status(DB_PATH, r.ref_id, target, ACTOR)
                            flash("success", f"{r.ref_id} moved to {target.value}.")
                    except workflow.WorkflowError as exc:
                        flash("error", str(exc))
                    st.rerun()

# ---- 3. HITL approvals --------------------------------------------------------
with tab_appr:
    st.subheader("Human-in-the-loop closure approvals")
    st.caption("A report closes only after explicit approval by a Safety Reviewer who did not request the closure.")
    pending = workflow.list_approvals(DB_PATH, pending_only=True)
    if not pending:
        st.info("No pending closure requests.")
    if ACTOR.role is not Role.REVIEWER and pending:
        st.warning("Switch the sidebar role to **Safety Reviewer** to approve or reject requests.")
    for a in pending:
        rep = repo().get(a.ref_id)
        with st.container(border=True):
            st.markdown(f"**Request #{a.id}** · {a.ref_id} · {rep.report_type.value} · {rep.product_name} "
                        f"(`{rep.batch_number}`) · requested by `{a.requested_by}` at {a.requested_at[:19]} UTC")
            confirm = st.checkbox("I confirm the intake record is complete and can be handed over "
                                  "(not a clinical or regulatory decision).", key=f"cf-{a.id}",
                                  disabled=ACTOR.role is not Role.REVIEWER)
            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("Approve closure", key=f"ap-{a.id}", type="primary",
                         disabled=ACTOR.role is not Role.REVIEWER or not confirm):
                try:
                    workflow.approve_closure(DB_PATH, a.id, ACTOR)
                    flash("success", f"{a.ref_id} closed (approval #{a.id} recorded in the audit trail).")
                except workflow.WorkflowError as exc:
                    flash("error", str(exc))
                st.rerun()
            if b2.button("Reject", key=f"rj-{a.id}", disabled=ACTOR.role is not Role.REVIEWER):
                try:
                    workflow.reject_closure(DB_PATH, a.id, ACTOR)
                    flash("info", f"Closure of {a.ref_id} rejected; report remains In Review.")
                except workflow.WorkflowError as exc:
                    flash("error", str(exc))
                st.rerun()
    with st.expander("Decision history"):
        hist = [x for x in workflow.list_approvals(DB_PATH, pending_only=False) if x.decision != "PENDING"]
        if hist:
            st.dataframe(pd.DataFrame([x.__dict__ for x in hist]), hide_index=True, width="stretch")
        else:
            st.caption("No decisions yet.")


# ---- 4. agent / RAG -----------------------------------------------------------
def render_rag(rag) -> None:
    st.caption(f"Backend: {rag.backend} · BM25 k1={config.BM25_K1}, b={config.BM25_B} · "
               f"RRF k={config.RRF_K} · rerank threshold {config.RERANK_THRESHOLD} · top-{config.FINAL_TOP_K}")

    def table(hits, extra_cols=()):
        rows = []
        for h in hits:
            row = {"rank": h.rank, "source": h.source_id, "chunk": h.chunk_id, "score": h.score, "title": h.title}
            for c in extra_cols:
                row[c] = h.extra.get(c)
            rows.append(row)
        return pd.DataFrame(rows)

    s1, s2, s3, s4 = st.tabs([f"1 · BM25 ({len(rag.bm25)})", f"2 · Bi-encoder ({len(rag.dense)})",
                              f"3 · RRF fusion ({len(rag.fused)})", f"4 · Cross-encoder ({len(rag.final)} kept)"])
    s1.dataframe(table(rag.bm25[:15]), hide_index=True, width="stretch")
    s2.dataframe(table(rag.dense[:15]), hide_index=True, width="stretch")
    fused = table(rag.fused[:15], ("ranks",))
    if not fused.empty:
        fused["ranks"] = fused["ranks"].astype(str)
    s3.dataframe(fused, hide_index=True, width="stretch")
    s4.dataframe(table(rag.reranked[:15], ("kept",)), hide_index=True, width="stretch")
    with st.expander("Grounded generation prompt (context = kept chunks only)"):
        st.code(rag.prompt, language="text")


with tab_agent:
    st.subheader("Safety Knowledge Assistant")
    st.caption("Deterministic router → validated tools / 4-stage hybrid RAG → extractive, cited answers. "
               "Try: *reports of difficulty breathing for LOT-88392* · *what is the status of REF-2026-0002* · "
               "*move REF-2026-0004 to in reveiw* · *how do I close a report?*")
    for msg in ss["chat"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
            if msg.get("meta"):
                meta = msg["meta"]
                with st.expander(f"Control plane · route **{meta['intent']}** · {len(meta['trace'])} ReAct steps"):
                    for step in meta["trace"]:
                        st.markdown(f"**{step.kind}:** {step.content}")
                if meta.get("rag") is not None:
                    with st.expander("Hybrid RAG pipeline stages"):
                        render_rag(meta["rag"])

    prompt = st.chat_input("Ask about past reports or SOPs, update a status, or log an intake…")
    if prompt:
        orch = Orchestrator(DB_PATH, ACTOR, rag_provider=kb.pipeline,
                            short_term=ss["short_term"], breaker=ss["breaker"])
        resp = orch.handle(prompt)
        ss["chat"].append({"role": "user", "text": prompt})
        ss["chat"].append({"role": "assistant", "text": resp.text,
                           "meta": {"intent": resp.intent.value, "trace": resp.trace, "rag": resp.rag_result}})
        st.rerun()
    if ss["chat"] and st.button("Clear conversation (short-term memory)"):
        ss["chat"].clear()
        ss["short_term"].clear()
        st.rerun()

# ---- 5. audit trail -----------------------------------------------------------
with tab_audit:
    st.subheader("Audit trail (append-only)")
    st.caption("Metadata only: no descriptions or personal data. Rows cannot be edited or deleted (enforced by DB triggers).")
    ref_filter = st.text_input("Filter by reference", placeholder="REF-2026-0001").strip().upper()
    entries = audit.list_entries(DB_PATH, ref_id=ref_filter or None, limit=1000)
    if entries:
        st.dataframe(pd.DataFrame([{
            "#": e.id, "Timestamp (UTC)": e.ts_utc[:23], "Reference": e.ref_id, "Action": e.action,
            "Actor": e.actor_id, "Role": e.actor_role, "Old status": e.old_status, "New status": e.new_status,
            "Metadata": json.dumps(e.metadata),
        } for e in entries]), hide_index=True, width="stretch")
    else:
        st.info("No audit entries yet.")
    with st.expander("Development governance log (PostToolUse hook · logs/agent_edit_audit.jsonl)"):
        if EDIT_LOG.exists():
            lines = EDIT_LOG.read_text(encoding="utf-8").splitlines()[-200:]
            st.dataframe(pd.DataFrame([json.loads(l) for l in lines if l.strip()]), hide_index=True,
                         width="stretch")
        else:
            st.caption("No hook events recorded yet.")
