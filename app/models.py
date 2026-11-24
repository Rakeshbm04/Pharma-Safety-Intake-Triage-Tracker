"""Pydantic schemas and enums. No patient-identifier fields exist by design."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import config


class ReportType(str, Enum):
    ADVERSE_EVENT = "Suspected Adverse Event"
    QUALITY_COMPLAINT = "Product Quality Complaint"


class Status(str, Enum):
    NEW = "New"
    IN_REVIEW = "In Review"
    CLOSED = "Closed"


class ReporterType(str, Enum):
    HCP = "Healthcare Professional"
    CONSUMER = "Patient/Consumer"
    SALES_REP = "Sales Representative"
    OTHER = "Other"


class Role(str, Enum):
    OPERATOR = "Intake Operator"
    REVIEWER = "Safety Reviewer"


class Actor(BaseModel):
    """Pseudonymous actor: an operator code and a role only. No names or emails."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str = Field(pattern=r"^OP-\d{2,4}$")
    role: Role


class IntakeReport(BaseModel):
    """Validated intake payload (before a reference ID is assigned)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    report_type: ReportType
    product_name: str = Field(min_length=config.PRODUCT_MIN, max_length=config.PRODUCT_MAX)
    batch_number: str = Field(pattern=config.BATCH_PATTERN)
    date_received: date
    reporter_type: ReporterType
    description: str = Field(min_length=config.DESCRIPTION_MIN, max_length=config.DESCRIPTION_MAX)
    event_description: Optional[str] = Field(default=None, max_length=config.EVENT_DESC_MAX)
    event_onset_date: Optional[date] = None

    @field_validator("event_description", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("date_received")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date received cannot be in the future")
        return v

    @model_validator(mode="after")
    def _type_specific_rules(self) -> "IntakeReport":
        if self.report_type is ReportType.ADVERSE_EVENT:
            if not self.event_description:
                raise ValueError("event_description: required for Suspected Adverse Event")
            if len(self.event_description) < config.EVENT_DESC_MIN:
                raise ValueError("event_description: too short")
            if self.event_onset_date is None:
                raise ValueError("event_onset_date: required for Suspected Adverse Event")
            if self.event_onset_date > self.date_received:
                raise ValueError("event_onset_date: after date received")
        else:
            if self.event_description or self.event_onset_date:
                raise ValueError("event_fields: not applicable to Product Quality Complaint")
        return self


class Report(IntakeReport):
    """A persisted report."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(pattern=config.REF_PATTERN)
    status: Status
    created_at: str
    updated_at: str
    created_by: str
    closure_pending: bool = False

    @field_validator("date_received")
    @classmethod
    def _not_future(cls, v: date) -> date:  # persisted rows are trusted; skip "today" check
        return v
