"""Type-safe tool layer: strict Pydantic argument schemas, auto-repair, structured errors.

Every call flows through `ToolExecutor.call`:
    auto-repair -> circuit-breaker check -> strict schema validation -> handler
Invalid arguments produce structured `ToolError`s and count towards the circuit breaker.
Closure *approval* is deliberately NOT exposed as a tool: it is a human-only action (HITL).
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import audit, config, workflow
from app.agent.circuit_breaker import CircuitBreaker, CircuitOpenError, call_signature
from app.audit import AuditAction
from app.db import init_db, transaction
from app.models import Actor, ReporterType, ReportType, Status
from app.repository import DuplicateSubmissionError, ReportNotFoundError, ReportRepository
from app.validation import validate_intake

ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
STRICT = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


# ---- argument schemas ------------------------------------------------------
class SubmitIntakeArgs(BaseModel):
    model_config = STRICT
    report_type: ReportType
    product_name: str = Field(min_length=config.PRODUCT_MIN, max_length=config.PRODUCT_MAX)
    batch_number: str = Field(pattern=config.BATCH_PATTERN)
    date_received: str = Field(pattern=ISO_DATE)
    reporter_type: ReporterType
    description: str = Field(min_length=config.DESCRIPTION_MIN, max_length=config.DESCRIPTION_MAX)
    event_description: Optional[str] = None
    event_onset_date: Optional[str] = Field(default=None, pattern=ISO_DATE)


class RefArgs(BaseModel):
    model_config = STRICT
    ref_id: str = Field(pattern=config.REF_PATTERN)


class UpdateStatusArgs(BaseModel):
    model_config = STRICT
    ref_id: str = Field(pattern=config.REF_PATTERN)
    new_status: Status


class SearchArgs(BaseModel):
    model_config = STRICT
    query: str = Field(min_length=3, max_length=500)


# ---- results ---------------------------------------------------------------
@dataclass(frozen=True)
class ToolError:
    tool: str
    field: str
    message: str


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    message: str = ""
    errors: list[ToolError] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    args: dict[str, Any] = field(default_factory=dict)
    invalid_args: bool = False   # True -> counts towards the circuit breaker


@dataclass
class ToolContext:
    db_path: Path
    actor: Actor
    rag_provider: Optional[Callable[[], Any]] = None   # returns a HybridRAGPipeline


# ---- auto-repair -----------------------------------------------------------
ENUM_ALIASES: dict[type, dict[str, str]] = {
    ReportType: {
        "ae": ReportType.ADVERSE_EVENT.value, "adverse event": ReportType.ADVERSE_EVENT.value,
        "suspected ae": ReportType.ADVERSE_EVENT.value, "side effect": ReportType.ADVERSE_EVENT.value,
        "pqc": ReportType.QUALITY_COMPLAINT.value, "quality complaint": ReportType.QUALITY_COMPLAINT.value,
        "product complaint": ReportType.QUALITY_COMPLAINT.value, "complaint": ReportType.QUALITY_COMPLAINT.value,
    },
    ReporterType: {
        "hcp": ReporterType.HCP.value, "doctor": ReporterType.HCP.value, "physician": ReporterType.HCP.value,
        "nurse": ReporterType.HCP.value, "pharmacist": ReporterType.HCP.value,
        "patient": ReporterType.CONSUMER.value, "consumer": ReporterType.CONSUMER.value,
        "caregiver": ReporterType.CONSUMER.value, "sales rep": ReporterType.SALES_REP.value,
        "rep": ReporterType.SALES_REP.value, "sales": ReporterType.SALES_REP.value,
    },
    Status: {
        "review": Status.IN_REVIEW.value, "under review": Status.IN_REVIEW.value, "inreview": Status.IN_REVIEW.value,
        "in-review": Status.IN_REVIEW.value, "close": Status.CLOSED.value, "done": Status.CLOSED.value,
        "open": Status.NEW.value, "reopen": Status.NEW.value, "send back": Status.NEW.value,
    },
}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y",
                "%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y")


def repair_enum(enum_cls: type, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    options = {e.value.casefold(): e.value for e in enum_cls}  # type: ignore[attr-defined]
    key = " ".join(value.strip().casefold().split())
    if key in options:
        return options[key]
    aliases = ENUM_ALIASES.get(enum_cls, {})
    if key in aliases:
        return aliases[key]
    match = difflib.get_close_matches(key, list(options) + list(aliases), n=1, cutoff=0.75)
    if match:
        return options.get(match[0]) or aliases[match[0]]
    return value


def repair_batch(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    m = re.fullmatch(r"\s*(?:lot)?[\s_\-#:.]*(\d{5})\s*", value, re.I)
    return f"LOT-{m.group(1)}" if m else value.strip()


def repair_ref(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    m = re.fullmatch(r"\s*ref[\s_\-#:]*(\d{4})[\s_\-/]*(\d{1,4})\s*", value, re.I)
    return f"REF-{m.group(1)}-{int(m.group(2)):04d}" if m else value.strip()


def repair_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return value
    v = value.strip()
    if v.lower() == "today":
        return date.today().isoformat()
    if v.lower() == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return v


FIELD_REPAIRS: dict[str, Callable[[Any], Any]] = {
    "report_type": lambda v: repair_enum(ReportType, v),
    "reporter_type": lambda v: repair_enum(ReporterType, v),
    "new_status": lambda v: repair_enum(Status, v),
    "batch_number": repair_batch,
    "ref_id": repair_ref,
    "date_received": repair_date,
    "event_onset_date": repair_date,
}


def auto_repair(schema: type[BaseModel], args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fix key typos (fuzzy match to schema fields) and normalise common value formats."""
    known = list(schema.model_fields)
    fixed: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in args.items():
        target = key
        if key not in known:
            match = difflib.get_close_matches(key.lower(), known, n=1, cutoff=0.75)
            if match and match[0] not in args:
                target = match[0]
                notes.append(f"argument '{key}' -> '{target}'")
        if isinstance(value, str) and not value.strip() and target in ("event_description", "event_onset_date"):
            continue
        repair = FIELD_REPAIRS.get(target)
        new_value = repair(value) if repair else value
        if repair and new_value != value:
            notes.append(f"{target}: '{value}' -> '{new_value}'")
        fixed[target] = new_value
    return fixed, notes


def _schema_errors(tool: str, exc: ValidationError) -> list[ToolError]:
    out = []
    for e in exc.errors():
        fld = ".".join(str(p) for p in e["loc"]) or "arguments"
        msg = e["msg"]
        if fld == "batch_number":
            msg = "batch_number must match ^LOT-\\d{5}$ (e.g. LOT-88392)"
        elif fld == "ref_id":
            msg = "ref_id must match ^REF-\\d{4}-\\d{4}$ (e.g. REF-2026-0001)"
        out.append(ToolError(tool, fld, msg))
    return out


def _safe_field_names(schema: type[BaseModel], errors: list[ToolError]) -> list[str]:
    """Audit only *schema* field names; caller-supplied unknown keys could contain personal data."""
    known = set(schema.model_fields) | {"workflow", "arguments", "tool", "index"}
    return sorted({e.field.split(".")[0] if e.field.split(".")[0] in known else "<unknown_field>" for e in errors})


# ---- handlers --------------------------------------------------------------
def _submit(ctx: ToolContext, args: SubmitIntakeArgs) -> ToolResult:
    model, errors = validate_intake(args.model_dump(mode="json", exclude_none=True))
    if errors:
        return ToolResult("submit_intake_report", False, message="Intake failed business validation.",
                          errors=[ToolError("submit_intake_report", e.field, e.message) for e in errors],
                          invalid_args=True)
    report = ReportRepository(ctx.db_path).create(model, ctx.actor, channel="agent")
    return ToolResult("submit_intake_report", True, data=report,
                      message=f"Report {report.ref_id} created with status '{report.status.value}'.")


def _get(ctx: ToolContext, args: RefArgs) -> ToolResult:
    report = ReportRepository(ctx.db_path).get(args.ref_id)
    pending = " (closure approval pending)" if report.closure_pending else ""
    return ToolResult("get_report", True, data=report,
                      message=f"{report.ref_id} · {report.report_type.value} · {report.product_name} · "
                              f"{report.batch_number} · status '{report.status.value}'{pending}.")


def _update(ctx: ToolContext, args: UpdateStatusArgs) -> ToolResult:
    report = ReportRepository(ctx.db_path).get(args.ref_id)
    if args.new_status is Status.CLOSED and report.status is Status.IN_REVIEW:
        approval_id = workflow.request_closure(ctx.db_path, args.ref_id, ctx.actor)
        return ToolResult("update_status", True, data={"approval_id": approval_id, "hitl": True},
                          message=f"Closure of {args.ref_id} requires human approval. Request #{approval_id} "
                                  "submitted; the report is frozen until a Safety Reviewer decides.")
    workflow.change_status(ctx.db_path, args.ref_id, args.new_status, ctx.actor)
    return ToolResult("update_status", True, data={"status": args.new_status.value},
                      message=f"{args.ref_id} moved from '{report.status.value}' to '{args.new_status.value}'.")


def _request_closure(ctx: ToolContext, args: RefArgs) -> ToolResult:
    approval_id = workflow.request_closure(ctx.db_path, args.ref_id, ctx.actor)
    return ToolResult("request_closure", True, data={"approval_id": approval_id, "hitl": True},
                      message=f"Closure request #{approval_id} for {args.ref_id} awaits Safety Reviewer approval.")


def _search(ctx: ToolContext, args: SearchArgs) -> ToolResult:
    if ctx.rag_provider is None:
        return ToolResult("search_knowledge", False, message="Knowledge index is not available.",
                          errors=[ToolError("search_knowledge", "index", "RAG pipeline not configured")])
    result = ctx.rag_provider().search(args.query)
    return ToolResult("search_knowledge", True, data=result, message=result.answer)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: type[BaseModel]
    handler: Callable[[ToolContext, Any], ToolResult]
    description: str


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec("submit_intake_report", SubmitIntakeArgs, _submit, "Create a new fictional intake report."),
        ToolSpec("get_report", RefArgs, _get, "Fetch a report and its current status."),
        ToolSpec("update_status", UpdateStatusArgs, _update, "Move a report through New/In Review; Closed -> HITL request."),
        ToolSpec("request_closure", RefArgs, _request_closure, "Submit an In Review report for human closure approval."),
        ToolSpec("search_knowledge", SearchArgs, _search, "Hybrid RAG lookup over past reports and SOPs."),
    )
}


class ToolExecutor:
    def __init__(self, ctx: ToolContext, breaker: Optional[CircuitBreaker] = None):
        init_db(ctx.db_path)
        self.ctx = ctx
        self.breaker = breaker or CircuitBreaker()

    def _audit(self, action: AuditAction, ref_id: Optional[str], metadata: dict) -> None:
        with transaction(self.ctx.db_path) as conn:
            audit.record(conn, action, self.ctx.actor, ref_id=ref_id, metadata=metadata)

    def call(self, tool: str, raw_args: dict[str, Any]) -> ToolResult:
        spec = TOOLS.get(tool)
        if spec is None:
            close = difflib.get_close_matches(tool, list(TOOLS), n=1, cutoff=0.7)
            if close:
                tool, spec = close[0], TOOLS[close[0]]
            else:
                return ToolResult(tool, False, message=f"Unknown tool '{tool}'.",
                                  errors=[ToolError(tool, "tool", f"Available tools: {', '.join(TOOLS)}")])
        args, repairs = auto_repair(spec.schema, dict(raw_args))
        signature = call_signature(tool, args)
        ref = args.get("ref_id") if isinstance(args.get("ref_id"), str) and re.fullmatch(config.REF_PATTERN, args["ref_id"]) else None
        try:
            self.breaker.before_call(tool, signature)  # raises CircuitOpenError if this signature is open
        except CircuitOpenError as blocked:
            self._audit(AuditAction.TOOL_CALL_BLOCKED, ref,
                        {"tool": tool, "signature": signature, "failure_count": blocked.failures})
            raise
        try:
            # Tool calls are JSON-shaped: strict JSON-mode validation (no silent type coercion).
            parsed = spec.schema.model_validate_json(json.dumps(args, default=str))
        except ValidationError as exc:
            result = ToolResult(tool, False, message="Tool arguments failed schema validation.",
                                errors=_schema_errors(tool, exc), invalid_args=True)
        else:
            try:
                result = spec.handler(self.ctx, parsed)
            except (workflow.WorkflowError, ReportNotFoundError, DuplicateSubmissionError) as exc:
                # Valid arguments, rejected by the workflow: not an argument error.
                result = ToolResult(tool, False, message=str(exc), errors=[ToolError(tool, "workflow", str(exc))])
        result.repairs, result.args = repairs, args

        if not result.invalid_args:
            self.breaker.record_success()
            return result
        try:
            count = self.breaker.record_failure(tool, signature)
        except CircuitOpenError as trip:
            self._audit(AuditAction.CIRCUIT_BREAKER_TRIPPED, ref,
                        {"tool": tool, "signature": signature, "failure_count": trip.failures})
            raise
        self._audit(AuditAction.TOOL_VALIDATION_FAILED, ref,
                    {"tool": tool, "fields": _safe_field_names(spec.schema, result.errors), "failure_count": count})
        return result
