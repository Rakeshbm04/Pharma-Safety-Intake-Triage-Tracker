"""Deterministic semantic router: rule-based intent classification BEFORE any tool / RAG call.

Precedence (first match wins):
  0. OUT_OF_SCOPE     - guardrail: causality / treatment / reportability / clinical decisions
  1. DIRECT_RESPONSE  - greetings, thanks, help
  2. STATUS_WORKFLOW  - a REF-YYYY-NNNN id plus a status/lookup verb
  3. INTAKE_FORM      - request to log / submit a new report, or `field: value` intake lines
  4. HYBRID_RAG       - everything else (historical report / SOP / batch lookups)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.agent.guardrails import check_scope


class Intent(str, Enum):
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    DIRECT_RESPONSE = "DIRECT_RESPONSE"
    STATUS_WORKFLOW = "STATUS_WORKFLOW"
    INTAKE_FORM = "INTAKE_FORM"
    HYBRID_RAG = "HYBRID_RAG"


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    rule: str
    detail: Optional[str] = None


GREETING_RE = re.compile(r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening)|thanks|thank you|cheers|help|what can you do)\b", re.I)
REF_RE = re.compile(r"\bref[\s_\-#:]*\d{4}[\s_\-/]*\d{1,4}\b", re.I)
STATUS_VERB_RE = re.compile(
    r"\b(status|move|moving|set|change|update|mark|close|closing|closed|closure|request|submit|review|reopen|send|"
    r"progress|advance|transition|approve|details?|show|get|look ?up|what is|where is)\b", re.I)
INTAKE_RE = re.compile(
    r"\b(new (report|intake|case|complaint|adverse event)|submit (a |an |the )?(new )?(report|intake|complaint)|"
    r"log (a |an )?(new )?(report|complaint|adverse event|ae|pqc)|file (a |an )?(report|complaint)|"
    r"create (a |an )?(new )?(report|intake)|record (a |an )?(new )?(report|complaint|adverse event)|start (an )?intake)\b",
    re.I)
FIELD_LINE_RE = re.compile(r"^\s*([a-z _/]+?)\s*[:=]\s*\S", re.I | re.M)
INTAKE_KEYS = {"type", "report type", "product", "product name", "batch", "lot", "batch number", "batch/lot",
               "date received", "received", "reporter", "reporter type", "description", "event",
               "event description", "onset", "event onset", "event onset date"}


def _intake_field_count(message: str) -> int:
    return sum(1 for k in FIELD_LINE_RE.findall(message) if k.strip().lower() in INTAKE_KEYS)


def route(message: str) -> RouteDecision:
    text = message.strip()
    if not text:
        return RouteDecision(Intent.DIRECT_RESPONSE, "empty")

    field_count = _intake_field_count(text)
    is_intake = bool(INTAKE_RE.search(text)) or field_count >= 3

    # For a genuine intake, the guardrail inspects the *request* lines only: free-text values in
    # `field: value` lines are recorded facts ("event: rash, treated at home"), not requests.
    # For anything else the WHOLE message is checked, so "event: did X cause it?" cannot bypass it.
    scope_text = text
    if is_intake:
        scope_text = "\n".join(
            line for line in text.splitlines()
            if not (FIELD_LINE_RE.match(line) and FIELD_LINE_RE.match(line).group(1).strip().lower() in INTAKE_KEYS)
        )
    violated = check_scope(scope_text)
    if violated:
        return RouteDecision(Intent.OUT_OF_SCOPE, "guardrail", violated)

    if GREETING_RE.match(text) and len(text.split()) <= 6:
        return RouteDecision(Intent.DIRECT_RESPONSE, "greeting")

    if is_intake:
        return RouteDecision(Intent.INTAKE_FORM, "intake_request", f"{field_count} field lines")

    ref = REF_RE.search(text)
    if ref and STATUS_VERB_RE.search(text):
        return RouteDecision(Intent.STATUS_WORKFLOW, "ref_with_status_verb", ref.group(0))

    return RouteDecision(Intent.HYBRID_RAG, "default_knowledge_lookup")
