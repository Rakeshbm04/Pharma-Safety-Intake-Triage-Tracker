"""Agent control plane: route -> (validated tool | RAG | direct reply), with a ReAct trace.

The orchestrator is deterministic: no LLM chooses tools. The semantic router selects the
path, simple parsers extract arguments, and every tool call goes through `ToolExecutor`
(auto-repair -> circuit breaker -> strict schema -> handler).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.agent.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.agent.guardrails import SCOPE_REFUSAL
from app.agent.memory import LongTermMemory, ShortTermMemory, TraceStep
from app.agent.router import REF_RE, Intent, RouteDecision, route
from app.agent.tools import ToolContext, ToolExecutor, ToolResult
from app.models import Actor

HELP_TEXT = (
    "Hello! I'm the Safety Intake Assistant (training prototype, fictional data only). I can:\n"
    "- **Look up** past reports and SOPs, e.g. *\"reports of difficulty breathing for LOT-88392\"*\n"
    "- **Check or update status**, e.g. *\"move REF-2026-0001 to In Review\"* or *\"close REF-2026-0001\"* "
    "(closing needs Safety Reviewer approval)\n"
    "- **Log a new report**, using the Intake Form tab or `field: value` lines in chat\n\n"
    "I never assess causality, recommend treatment, or decide reportability."
)

INTAKE_TEMPLATE = (
    "To log a report, use the **New Intake** tab, or send `field: value` lines like this:\n"
    "```\nreport type: Suspected Adverse Event\nproduct: Zentrovax 50 mg\nbatch: LOT-88392\n"
    "date received: 2026-11-20\nreporter: Healthcare Professional\n"
    "description: Fictional caller reported a reaction after the second tablet.\n"
    "event: Shortness of breath lasting about one hour\nonset: 2026-11-19\n```"
)

INTAKE_KEY_MAP = {
    "type": "report_type", "report type": "report_type",
    "product": "product_name", "product name": "product_name",
    "batch": "batch_number", "lot": "batch_number", "batch number": "batch_number", "batch/lot": "batch_number",
    "date received": "date_received", "received": "date_received",
    "reporter": "reporter_type", "reporter type": "reporter_type",
    "description": "description",
    "event": "event_description", "event description": "event_description",
    "onset": "event_onset_date", "event onset": "event_onset_date", "event onset date": "event_onset_date",
}


@dataclass
class AgentResponse:
    intent: Intent
    text: str
    route: RouteDecision
    trace: list[TraceStep] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    rag_result: Any = None
    halted: bool = False


def parse_intake_fields(message: str) -> dict[str, str]:
    args: dict[str, str] = {}
    for line in message.splitlines():
        m = re.match(r"^\s*([a-z _/]+?)\s*[:=]\s*(.+?)\s*$", line, re.I)
        if m and m.group(1).strip().lower() in INTAKE_KEY_MAP:
            args[INTAKE_KEY_MAP[m.group(1).strip().lower()]] = m.group(2)
    return args


POLITE_PREFIX_RE = re.compile(r"^\s*(please|pls|kindly|can you|could you|would you|will you)\b[\s,]*", re.I)
REF_TOKEN = r"ref[\s_\-#:]*\d{4}[\s_\-/]*\d{1,4}"
_R = rf"(?:the\s+)?(?:report\s+|case\s+)?(?:status\s+of\s+)?(?:the\s+)?(?:report\s+)?{REF_TOKEN}"
_STATUS_PHRASE = r"['\"]?(?P<target>[a-z][a-z\-]*(?:\s[a-z\-]+){0,2})['\"]?"

# ALLOWLIST GRAMMAR: the WHOLE message (after a polite prefix / trailing punctuation) must match one
# of these command templates. Anything else is treated as a read-only lookup, so an unrecognised or
# ambiguous phrasing can never change state ("fail safe").
COMMAND_TEMPLATES: list[tuple[re.Pattern[str], Optional[str]]] = [
    # explicit target: "move REF-x to In Review", "set the status of REF-x to new", "mark REF-x as in reveiw"
    (re.compile(rf"(?:move|set|change|update|mark|put|transition|advance|progress)\s+{_R}\s+(?:to|as|into|under|back to)\s+{_STATUS_PHRASE}", re.I), None),
    (re.compile(rf"{REF_TOKEN}\s*[:\-]\s*(?:move|set|change|mark)\s+(?:it\s+)?(?:to|as|into)\s+{_STATUS_PHRASE}", re.I), None),
    # closure (always routed to the HITL request, never a direct close)
    (re.compile(rf"(?:close|request\s+closure\s+(?:for|of)|request\s+to\s+close|raise\s+a\s+closure\s+request\s+for)\s+{_R}", re.I), "Closed"),
    (re.compile(rf"submit\s+{_R}\s+for\s+(?:closure|closing|approval)", re.I), "Closed"),
    # send back / reopen
    (re.compile(rf"(?:send\s+{_R}\s+back(?:\s+to\s+new)?|return\s+{_R}\s+to\s+new|reopen\s+{_R}|re-open\s+{_R})", re.I), "New"),
    # start review
    (re.compile(rf"(?:start|begin)\s+(?:the\s+)?review\s+(?:of|for|on)\s+{_R}|submit\s+{_R}\s+for\s+review", re.I), "In Review"),
]


def parse_status_target(message: str) -> Optional[str]:
    """Return the raw target status phrase (repaired later by the tool layer), or None for a lookup.

    Safety rule: only a message that is *entirely* an explicit command can change state. Questions
    ("is REF-2026-0001 still in review?") and anything outside the command grammar are lookups.
    """
    text = POLITE_PREFIX_RE.sub("", message.strip()).strip().rstrip(" ?.!").strip()
    for pattern, fixed_target in COMMAND_TEMPLATES:
        m = pattern.fullmatch(text)
        if m:
            return fixed_target or m.group("target")
    return None


class Orchestrator:
    def __init__(self, db_path: Path | str, actor: Actor,
                 rag_provider: Optional[Callable[[], Any]] = None,
                 short_term: Optional[ShortTermMemory] = None,
                 breaker: Optional[CircuitBreaker] = None):
        self.db_path = Path(db_path)
        self.actor = actor
        self.short_term = short_term or ShortTermMemory()
        self.long_term = LongTermMemory(self.db_path)
        self.executor = ToolExecutor(ToolContext(self.db_path, actor, rag_provider), breaker)

    @property
    def breaker(self) -> CircuitBreaker:
        return self.executor.breaker

    def _tool(self, name: str, args: dict[str, Any], resp: AgentResponse) -> ToolResult:
        # Mask free text by loose key match (also catches typo'd keys before auto-repair).
        shown = {k: (f"<{len(str(v))} chars>" if re.search(r"desc|event|narr|text|note", k, re.I) else v)
                 for k, v in args.items()}
        self.short_term.add("Action", f"{name}({shown})")
        result = self.executor.call(name, args)
        resp.tool_results.append(result)
        obs = result.message
        if result.repairs:
            obs += f" | auto-repaired: {'; '.join(result.repairs)}"
        if result.errors:
            obs += " | errors: " + "; ".join(f"{e.field}: {e.message}" for e in result.errors)
        self.short_term.add("Observation", obs)
        return result

    def handle(self, message: str) -> AgentResponse:
        self.short_term.new_run()
        decision = route(message)
        self.short_term.add("Thought", f"Router -> {decision.intent.value} (rule: {decision.rule}"
                                       f"{', ' + decision.detail if decision.detail else ''})")
        resp = AgentResponse(decision.intent, "", decision)
        try:
            resp.text = self._dispatch(message, decision, resp)
        except CircuitOpenError as exc:
            resp.halted = True
            resp.text = f"⛔ {exc}"
            self.short_term.add("Observation", "Circuit breaker tripped; run halted.")
        self.short_term.add("Final", resp.text[:300])
        resp.trace = self.short_term.current_run()
        return resp

    def _dispatch(self, message: str, decision: RouteDecision, resp: AgentResponse) -> str:
        if decision.intent is Intent.OUT_OF_SCOPE:
            self.short_term.add("Thought", f"Scope guardrail '{decision.detail}' violated: refuse, no tools called.")
            return SCOPE_REFUSAL
        if decision.intent is Intent.DIRECT_RESPONSE:
            return HELP_TEXT
        if decision.intent is Intent.INTAKE_FORM:
            return self._intake(message, resp)
        if decision.intent is Intent.STATUS_WORKFLOW:
            return self._status(message, resp)
        return self._rag(message, resp)

    def _intake(self, message: str, resp: AgentResponse) -> str:
        args = parse_intake_fields(message)
        if len(args) < 3:
            self.short_term.add("Thought", "No structured intake fields found: direct user to the form.")
            return INTAKE_TEMPLATE
        result = self._tool("submit_intake_report", args, resp)
        if result.ok:
            return f"✅ {result.message}"
        lines = "\n".join(f"- **{e.field}**: {e.message}" for e in result.errors)
        return f"❌ The report was not saved. Please fix:\n{lines}"

    def _status(self, message: str, resp: AgentResponse) -> str:
        ref_raw = REF_RE.search(message).group(0)  # router guarantees a match
        target = parse_status_target(message)
        if target is None:
            result = self._tool("get_report", {"ref_id": ref_raw}, resp)
        else:
            self.short_term.add("Thought", f"Requested transition target: '{target}'.")
            result = self._tool("update_status", {"ref_id": ref_raw, "new_status": target}, resp)
        if result.ok:
            return result.message
        detail = "; ".join(e.message for e in result.errors) or result.message
        return f"❌ {detail}"

    def _rag(self, message: str, resp: AgentResponse) -> str:
        result = self._tool("search_knowledge", {"query": message}, resp)
        if not result.ok:
            return f"❌ {result.message} " + "; ".join(e.message for e in result.errors)
        resp.rag_result = result.data
        rag = result.data
        self.short_term.add("Thought", f"RAG kept {len(rag.final)} of {len(rag.fused)} fused candidates "
                                       f"(threshold filter); citations: {', '.join(rag.citations) or 'none'}.")
        return rag.answer
