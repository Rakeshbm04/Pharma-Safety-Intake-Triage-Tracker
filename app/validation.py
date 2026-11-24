"""Mandatory-field validation, PII screening and user-facing error messages.

`validate_intake` collects *all* problems in one pass so the operator sees every
missing/invalid field at once, with messages tailored to the selected report type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from pydantic import ValidationError

from app import config
from app.models import IntakeReport, ReporterType, ReportType

FIELD_LABELS = {
    "report_type": "Report type",
    "product_name": "Product name",
    "batch_number": "Batch/Lot number",
    "date_received": "Date received",
    "reporter_type": "Reporter type",
    "description": "Description",
    "event_description": "Event description",
    "event_onset_date": "Event onset date",
}

# --- PII screening ---------------------------------------------------------
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone number": re.compile(r"(?<![\w-])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?![\w-])"),
    "national ID / SSN-like number": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "date of birth": re.compile(r"\b(?:d\.?o\.?b\.?|date of birth|born on)\b", re.I),
    "patient name or initials": re.compile(
        r"\b(?:patient|pt)\s*(?:name|initials)\b|\bname\s*[:=]|\binitials\s*[:=]", re.I
    ),
    "medical record number": re.compile(r"\b(?:mrn|medical record(?: number| no\.?)?)\b", re.I),
}
FREE_TEXT_FIELDS = ("product_name", "description", "event_description")


def detect_pii(text: Optional[str]) -> list[str]:
    """Return the kinds of personal data detected in `text` (empty list = clean)."""
    if not text:
        return []
    # Batch / reference identifiers are allowed and must not be mistaken for phone numbers.
    scrubbed = re.sub(r"\b(?:LOT-\d{5}|REF-\d{4}-\d{4})\b", " ", text)
    return [kind for kind, pat in PII_PATTERNS.items() if pat.search(scrubbed)]


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.field, self.field)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_intake(data: dict[str, Any]) -> tuple[Optional[IntakeReport], list[FieldError]]:
    """Validate a raw intake dict. Returns (model, []) on success or (None, errors)."""
    errors: list[FieldError] = []
    unknown = set(data) - set(FIELD_LABELS)
    for key in sorted(unknown):
        errors.append(FieldError(key, f"Field '{key}' is not part of the intake schema and cannot be stored."))

    # Report type drives which fields are mandatory, so resolve it first.
    rtype: Optional[ReportType] = None
    raw_type = data.get("report_type")
    if _blank(raw_type):
        errors.append(FieldError("report_type", "Report type is required: choose 'Suspected Adverse Event' or 'Product Quality Complaint'."))
    else:
        try:
            rtype = ReportType(raw_type)
        except ValueError:
            errors.append(FieldError("report_type", f"'{raw_type}' is not a valid report type. Use 'Suspected Adverse Event' or 'Product Quality Complaint'."))
    type_label = rtype.value if rtype else "this report"

    product = data.get("product_name")
    if _blank(product):
        errors.append(FieldError("product_name", f"Product name is required for {type_label}."))
    elif not (config.PRODUCT_MIN <= len(str(product).strip()) <= config.PRODUCT_MAX):
        errors.append(FieldError("product_name", f"Product name must be {config.PRODUCT_MIN}-{config.PRODUCT_MAX} characters."))

    batch = data.get("batch_number")
    if _blank(batch):
        errors.append(FieldError("batch_number", f"Batch/Lot number is required for {type_label} (format LOT-#####, e.g. LOT-88392)."))
    elif not re.fullmatch(config.BATCH_PATTERN, str(batch).strip()):
        errors.append(FieldError("batch_number", f"Batch/Lot number '{batch}' is invalid. It must match LOT-##### (exactly 5 digits, e.g. LOT-88392)."))

    received = _parse_date(data.get("date_received"))
    if _blank(data.get("date_received")):
        errors.append(FieldError("date_received", "Date received is required."))
    elif received is None:
        errors.append(FieldError("date_received", "Date received must be a valid date (YYYY-MM-DD)."))
    elif received > date.today():
        errors.append(FieldError("date_received", "Date received cannot be in the future."))

    reporter = data.get("reporter_type")
    if _blank(reporter):
        errors.append(FieldError("reporter_type", "Reporter type is required (e.g. Healthcare Professional, Patient/Consumer)."))
    else:
        try:
            ReporterType(reporter)
        except ValueError:
            allowed = ", ".join(r.value for r in ReporterType)
            errors.append(FieldError("reporter_type", f"'{reporter}' is not a valid reporter type. Allowed: {allowed}."))

    desc = data.get("description")
    if _blank(desc):
        what = "the complaint (defect observed, packaging, appearance)" if rtype is ReportType.QUALITY_COMPLAINT else "the report"
        errors.append(FieldError("description", f"Description is required: describe {what}."))
    elif not (config.DESCRIPTION_MIN <= len(str(desc).strip()) <= config.DESCRIPTION_MAX):
        errors.append(FieldError("description", f"Description must be {config.DESCRIPTION_MIN}-{config.DESCRIPTION_MAX} characters (currently {len(str(desc).strip())})."))

    ev_desc = data.get("event_description")
    onset_raw = data.get("event_onset_date")
    onset = _parse_date(onset_raw)
    if rtype is ReportType.ADVERSE_EVENT:
        if _blank(ev_desc):
            errors.append(FieldError("event_description", "Event description is required for Suspected Adverse Event reports."))
        elif len(str(ev_desc).strip()) < config.EVENT_DESC_MIN:
            errors.append(FieldError("event_description", f"Event description must be at least {config.EVENT_DESC_MIN} characters."))
        if _blank(onset_raw):
            errors.append(FieldError("event_onset_date", "Event onset date is required for Suspected Adverse Event reports."))
        elif onset is None:
            errors.append(FieldError("event_onset_date", "Event onset date must be a valid date (YYYY-MM-DD)."))
        elif received is not None and onset > received:
            errors.append(FieldError("event_onset_date", "Event onset date cannot be later than the date the report was received."))
    elif rtype is ReportType.QUALITY_COMPLAINT:
        if not _blank(ev_desc) or not _blank(onset_raw):
            errors.append(FieldError("event_description", "Event description/onset date apply only to Suspected Adverse Event reports; clear them for a Product Quality Complaint."))

    for fld in FREE_TEXT_FIELDS:
        kinds = detect_pii(data.get(fld) if isinstance(data.get(fld), str) else None)
        if kinds:
            errors.append(FieldError(fld, f"{FIELD_LABELS[fld]} appears to contain personal data ({', '.join(kinds)}). Remove it: only fictional, de-identified information is permitted."))

    if errors:
        return None, errors
    try:
        return IntakeReport.model_validate({k: v for k, v in data.items() if not _blank(v)}), []
    except ValidationError as exc:  # defensive: should be unreachable after checks above
        return None, [FieldError(str(e["loc"][0]) if e["loc"] else "report", e["msg"]) for e in exc.errors()]
