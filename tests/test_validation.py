"""Schema + mandatory-field validation (incl. PII screening)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.models import IntakeReport, ReportType
from app.validation import detect_pii, validate_intake
from tests.conftest import days_ago


def fields(errors):
    return {e.field for e in errors}


def test_valid_ae_and_pqc(ae_payload, pqc_payload):
    ae, errs = validate_intake(ae_payload)
    assert not errs and ae.report_type is ReportType.ADVERSE_EVENT
    pqc, errs = validate_intake(pqc_payload)
    assert not errs and pqc.event_description is None


def test_empty_payload_reports_every_mandatory_field():
    model, errors = validate_intake({})
    assert model is None
    assert {"report_type", "product_name", "batch_number", "date_received", "reporter_type", "description"} <= fields(errors)


@pytest.mark.parametrize("field", ["product_name", "batch_number", "date_received", "reporter_type", "description"])
def test_each_core_field_is_mandatory(pqc_payload, field):
    pqc_payload[field] = ""
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {field}
    assert all(e.message for e in errors)


@pytest.mark.parametrize("batch", ["LOT-8839", "LOT-883921", "lot-88392", "88392", "LOT_88392", "LOT-8839A"])
def test_batch_format_enforced(pqc_payload, batch):
    pqc_payload["batch_number"] = batch
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {"batch_number"}
    assert "LOT-#####" in errors[0].message


def test_ae_requires_event_fields_with_context_message(ae_payload):
    ae_payload["event_description"] = ""
    ae_payload["event_onset_date"] = None
    _, errors = validate_intake(ae_payload)
    assert fields(errors) == {"event_description", "event_onset_date"}
    assert all("Suspected Adverse Event" in e.message for e in errors)


def test_pqc_rejects_event_fields(pqc_payload):
    pqc_payload["event_description"] = "Some event text here"
    _, errors = validate_intake(pqc_payload)
    assert "event_description" in fields(errors)
    assert "only to Suspected Adverse Event" in errors[0].message


def test_onset_after_received_rejected(ae_payload):
    ae_payload["event_onset_date"] = ae_payload["date_received"]
    assert validate_intake(ae_payload)[1] == []          # same day is fine
    ae_payload["event_onset_date"] = days_ago(0)
    ae_payload["date_received"] = days_ago(1)
    _, errors = validate_intake(ae_payload)
    assert fields(errors) == {"event_onset_date"}


def test_future_received_date_rejected(pqc_payload):
    pqc_payload["date_received"] = (date.today() + timedelta(days=1)).isoformat()
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {"date_received"}


def test_invalid_enum_and_unknown_field(pqc_payload):
    pqc_payload["reporter_type"] = "Doctor Smith"
    pqc_payload["patient_name"] = "x"
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {"reporter_type", "patient_name"}


def test_description_length(pqc_payload):
    pqc_payload["description"] = "too short"
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {"description"}


@pytest.mark.parametrize("text,kind", [
    ("Contact me at jane.doe@example.com", "email address"),
    ("Call back on 555-123-4567 please", "phone number"),
    ("SSN 123-45-6789 provided", "national ID / SSN-like number"),
    ("Patient DOB was given by caller", "date of birth"),
    ("Patient name: withheld", "patient name or initials"),
    ("MRN noted by nurse", "medical record number"),
])
def test_pii_detected(text, kind):
    assert kind in detect_pii(text)


def test_pii_not_triggered_by_identifiers():
    assert detect_pii("Batch LOT-88392 linked to REF-2026-0001, 50 mg twice daily for 3 days") == []


def test_pii_in_description_blocks_save(pqc_payload):
    pqc_payload["description"] = "Reporter email is someone@example.org and tablets were chipped."
    _, errors = validate_intake(pqc_payload)
    assert fields(errors) == {"description"}
    assert "personal data" in errors[0].message


def test_model_forbids_extra_fields(pqc_payload):
    with pytest.raises(ValidationError):
        IntakeReport.model_validate({**pqc_payload, "patient_initials": "AB"})
