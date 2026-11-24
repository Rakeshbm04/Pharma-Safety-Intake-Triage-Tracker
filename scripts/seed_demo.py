"""Seed the local database with FICTIONAL demo reports (no real products, people or data).

    python scripts/seed_demo.py            # seed only if the DB is empty
    python scripts/seed_demo.py --reset    # delete the DB file and reseed
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, workflow  # noqa: E402
from app.models import Actor, Role, Status  # noqa: E402
from app.repository import ReportRepository  # noqa: E402
from app.validation import validate_intake  # noqa: E402

OPERATOR = Actor(actor_id="OP-01", role=Role.OPERATOR)
REVIEWER = Actor(actor_id="OP-07", role=Role.REVIEWER)


def d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


AE, PQC = "Suspected Adverse Event", "Product Quality Complaint"

DEMO_REPORTS: list[tuple[dict, list[str]]] = [
    ({"report_type": AE, "product_name": "Zentrovax 50 mg tablets", "batch_number": "LOT-88392",
      "date_received": d(20), "reporter_type": "Healthcare Professional",
      "description": "Fictional pharmacist called to report a reaction after the second tablet of a new pack.",
      "event_description": "Dyspnea and chest tightness lasting about forty minutes after the second dose.",
      "event_onset_date": d(22)}, ["In Review", "CLOSE"]),
    ({"report_type": AE, "product_name": "Zentrovax 50 mg tablets", "batch_number": "LOT-88392",
      "date_received": d(9), "reporter_type": "Patient/Consumer",
      "description": "Fictional consumer emailed describing symptoms that started the same evening.",
      "event_description": "Shortness of breath and itchy welts on the arms within an hour of taking the tablet.",
      "event_onset_date": d(10)}, ["In Review"]),
    ({"report_type": PQC, "product_name": "Zentrovax 50 mg tablets", "batch_number": "LOT-88392",
      "date_received": d(15), "reporter_type": "Sales Representative",
      "description": "Several tablets in two blister strips show yellowing and a speckled surface discoloration."},
     ["In Review"]),
    ({"report_type": PQC, "product_name": "Cardiolex 10 mg film-coated tablets", "batch_number": "LOT-45127",
      "date_received": d(12), "reporter_type": "Healthcare Professional",
      "description": "Chipped tablet edges and crumbling tablets found on opening a new bottle at the pharmacy."},
     []),
    ({"report_type": PQC, "product_name": "Neurolin 2 mL injection vial", "batch_number": "LOT-60218",
      "date_received": d(6), "reporter_type": "Healthcare Professional",
      "description": "Nurse observed floating particles in the solution of one vial; the vial was set aside unused."},
     ["In Review"]),
    ({"report_type": AE, "product_name": "Dermacil 1% cream", "batch_number": "LOT-31904",
      "date_received": d(4), "reporter_type": "Patient/Consumer",
      "description": "Fictional caller reported a skin reaction at the application site after three days of use.",
      "event_description": "Red rash and itching around the application area that spread to the forearm.",
      "event_onset_date": d(5)}, []),
    ({"report_type": PQC, "product_name": "Respiclear 100 mcg inhaler", "batch_number": "LOT-77015",
      "date_received": d(3), "reporter_type": "Other",
      "description": "Inhaler canister leaking from the valve and the outer carton had a broken seal on delivery."},
     []),
    ({"report_type": AE, "product_name": "Gastrozen 20 mg capsules", "batch_number": "LOT-50433",
      "date_received": d(2), "reporter_type": "Healthcare Professional",
      "description": "Fictional physician reported gastrointestinal symptoms in a person who recently started the capsules.",
      "event_description": "Nausea and vomiting on two consecutive mornings, with mild dizziness.",
      "event_onset_date": d(4)}, []),
]


def seed(db_path: Path, reset: bool = False) -> list[str]:
    if reset and db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
    repo = ReportRepository(db_path)
    if repo.list():
        print(f"{db_path} already contains reports; use --reset to reseed.")
        return []
    refs = []
    for payload, steps in DEMO_REPORTS:
        model, errors = validate_intake(payload)
        if errors:
            raise SystemExit(f"Seed data invalid: {errors}")
        report = repo.create(model, OPERATOR, channel="seed")
        for step in steps:
            if step == "CLOSE":
                approval = workflow.request_closure(db_path, report.ref_id, OPERATOR)
                workflow.approve_closure(db_path, approval, REVIEWER)
            else:
                workflow.change_status(db_path, report.ref_id, Status(step), OPERATOR)
        refs.append(report.ref_id)
    print(f"Seeded {len(refs)} fictional reports into {db_path}: {', '.join(refs)}")
    return refs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    ns = parser.parse_args()
    seed(ns.db, ns.reset)
