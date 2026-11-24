"""Render docs/workflow.png: runtime control flow + report lifecycle (documentation only).

    pip install matplotlib        # dev/docs dependency, not needed to run the app
    python scripts/make_workflow_diagram.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "workflow.png"

INK, MUTED, LINE = "#1f2328", "#57606a", "#8c959f"
FILL = {"io": "#eef2f7", "gate": "#fdecea", "route": "#fff4d6", "work": "#e8f1fb", "store": "#e9f5ec"}
EDGE = {"io": "#8c959f", "gate": "#c9372c", "route": "#b7791f", "work": "#2f6fb5", "store": "#2e7d4f"}


def box(ax, x, y, w, h, title, sub="", kind="work"):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=FILL[kind], ec=EDGE[kind], lw=1.4))
    ax.text(x, y + (0.13 if sub else 0), title, ha="center", va="center", fontsize=9.5, weight="bold", color=INK)
    if sub:
        ax.text(x, y - 0.2, sub, ha="center", va="center", fontsize=7.6, color=MUTED)


def arrow(ax, a, b, label="", color=LINE, rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=11, lw=1.2, color=color,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2))
    if label:
        ax.text((a[0] + b[0]) / 2 + 0.08, (a[1] + b[1]) / 2, label, fontsize=7.4, color=MUTED, va="center")


def main() -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 8.6), gridspec_kw={"width_ratios": [1.55, 1]})
    for ax in (ax1, ax2):
        ax.set_axis_off()
    ax1.set_xlim(0, 10), ax1.set_ylim(0, 10)
    ax2.set_xlim(0, 6), ax2.set_ylim(0, 10)

    # ---- left: runtime control plane ------------------------------------------------------
    ax1.set_title("Runtime control plane (one user message)", loc="left", fontsize=12, weight="bold", color=INK)
    box(ax1, 5, 9.3, 3.4, 0.7, "User message", "chat / form (fictional data only)", "io")
    box(ax1, 5, 8.0, 4.4, 0.85, "Scope guardrail", "causality · treatment · dosing · seriousness · reportability", "gate")
    box(ax1, 8.85, 8.0, 2.0, 0.7, "Fixed refusal", "no tool, no RAG", "gate")
    box(ax1, 5, 6.6, 4.4, 0.85, "Deterministic semantic router", "rule-based · no LLM", "route")
    xs = [1.25, 3.75, 6.25, 8.75]
    names = [("Direct", "greeting / help"), ("Intake Form", "submit_intake_report"),
             ("Status Workflow", "allowlist command grammar"), ("Hybrid RAG", "search_knowledge")]
    for x, (t, s) in zip(xs, names):
        box(ax1, x, 5.1, 2.3, 0.8, t, s, "route")
    box(ax1, 5, 3.7, 6.6, 0.85, "Tool executor", "auto-repair → circuit breaker (3rd identical invalid call halts) → strict Pydantic", "work")
    box(ax1, 2.6, 2.25, 3.6, 0.85, "Workflow + HITL gate", "New → In Review → [pending] → Closed", "work")
    box(ax1, 7.6, 2.25, 3.6, 0.85, "4-stage hybrid RAG", "BM25 · bi-encoder · RRF k=60 · rerank ≥ 0.40", "work")
    box(ax1, 2.6, 0.8, 3.6, 0.8, "SQLite (long-term memory)", "reports · approvals · append-only audit", "store")
    box(ax1, 7.6, 0.8, 3.6, 0.8, "Extractive, cited answer", "or 'no relevant records'", "io")

    arrow(ax1, (5, 8.95), (5, 8.43))
    arrow(ax1, (7.2, 8.0), (7.85, 8.0), color=EDGE["gate"])
    ax1.text(7.52, 8.17, "out of scope", fontsize=7.4, color=EDGE["gate"], ha="center")
    arrow(ax1, (5, 7.57), (5, 7.03), "in scope")
    for x in xs:
        arrow(ax1, (5, 6.17), (x, 5.5))
    for x in xs[1:3]:
        arrow(ax1, (x, 4.7), (x, 4.13))
    arrow(ax1, (xs[3], 4.7), (7.9, 4.13))
    arrow(ax1, (3.4, 3.27), (2.8, 2.68))
    arrow(ax1, (6.6, 3.27), (7.4, 2.68))
    arrow(ax1, (2.6, 1.82), (2.6, 1.2), "same txn + audit row")
    arrow(ax1, (7.6, 1.82), (7.6, 1.2))
    ax1.text(0.2, 9.85, "Short-term memory: every step is recorded as a ReAct trace (Thought → Action → Observation)",
             fontsize=8, color=MUTED, style="italic")

    # ---- right: lifecycle ---------------------------------------------------------------------
    ax2.set_title("Report lifecycle (one-way)", loc="left", fontsize=12, weight="bold", color=INK)
    box(ax2, 3, 8.8, 3.6, 0.85, "New", "REF-YYYY-NNNN generated · audited", "io")
    box(ax2, 3, 6.7, 3.6, 0.85, "In Review", "operator moves report", "work")
    box(ax2, 3, 4.5, 3.8, 1.0, "Closure pending (frozen)", "no other transition allowed", "gate")
    box(ax2, 3, 2.2, 3.6, 0.85, "Closed", "terminal · cannot be reopened", "store")
    arrow(ax2, (3, 8.37), (3, 7.13), "move to In Review")
    arrow(ax2, (3, 6.27), (3, 5.0), "request closure")
    arrow(ax2, (3, 4.0), (3, 2.63), "Safety Reviewer approves\n(four-eyes: ≠ requester)", EDGE["store"])
    arrow(ax2, (4.9, 4.5), (4.8, 6.4), "reviewer rejects", EDGE["gate"], rad=0.45)
    ax2.text(0.3, 0.9, "Rejected by workflow.py and audited:\nbackward moves · skipping New→Closed · direct close\n"
             "self-approval · wrong role · any change to Closed", fontsize=8, color=MUTED, va="center")

    fig.tight_layout()
    OUT.parent.mkdir(exist_ok=True)
    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
