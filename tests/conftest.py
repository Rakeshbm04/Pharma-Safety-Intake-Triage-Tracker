from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ["PHARMA_RAG_BACKEND"] = "offline"   # deterministic, no models required
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ---- runtime offline guard -------------------------------------------------------------
# For the whole test session, any socket connection to a non-loopback address raises.
# This proves at runtime (not just by grepping imports) that the app makes no network calls.
import socket  # noqa: E402

LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
_real_connect, _real_connect_ex = socket.socket.connect, socket.socket.connect_ex


def _host(address) -> str:
    return str(address[0]) if isinstance(address, tuple) else str(address)


def _guarded(real):
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(address) not in LOOPBACK:
            raise AssertionError(f"network access blocked during tests: {_host(address)}")
        return real(self, address)
    return connect


socket.socket.connect = _guarded(_real_connect)
socket.socket.connect_ex = _guarded(_real_connect_ex)

# DNS lookups are network traffic too: only loopback names / literal addresses may be resolved.
_real_getaddrinfo = socket.getaddrinfo
_LITERAL_IP = __import__("re").compile(r"^[\d.]+$|^[0-9a-fA-F:]+$")


def _guarded_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name is not None and name not in LOOPBACK and not _LITERAL_IP.match(str(name)) and name != socket.gethostname():
        raise AssertionError(f"network access blocked during tests: DNS lookup of {name}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _guarded_getaddrinfo

from app.models import Actor, Role  # noqa: E402
from app.repository import ReportRepository  # noqa: E402
from app.validation import validate_intake  # noqa: E402


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def repo(db_path: Path) -> ReportRepository:
    return ReportRepository(db_path)


@pytest.fixture
def operator() -> Actor:
    return Actor(actor_id="OP-01", role=Role.OPERATOR)


@pytest.fixture
def reviewer() -> Actor:
    return Actor(actor_id="OP-07", role=Role.REVIEWER)


@pytest.fixture
def ae_payload() -> dict:
    return {
        "report_type": "Suspected Adverse Event",
        "product_name": "Zentrovax 50 mg tablets",
        "batch_number": "LOT-88392",
        "date_received": days_ago(2),
        "reporter_type": "Healthcare Professional",
        "description": "Fictional pharmacist reported a reaction after the second tablet.",
        "event_description": "Shortness of breath for about one hour.",
        "event_onset_date": days_ago(3),
    }


@pytest.fixture
def pqc_payload() -> dict:
    return {
        "report_type": "Product Quality Complaint",
        "product_name": "Cardiolex 10 mg tablets",
        "batch_number": "LOT-45127",
        "date_received": days_ago(1),
        "reporter_type": "Sales Representative",
        "description": "Chipped tablet edges found in a newly opened bottle.",
    }


@pytest.fixture
def make_report(repo, operator):
    def _make(payload: dict):
        model, errors = validate_intake(payload)
        assert not errors, errors
        return repo.create(model, operator)
    return _make
