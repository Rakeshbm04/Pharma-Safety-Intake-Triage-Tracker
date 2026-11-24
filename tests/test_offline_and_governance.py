"""Offline guarantees, code hygiene and governance-document checks (Phases I, II, IV, V; defense)."""
from __future__ import annotations

import ast
import json
import re
import socket

import pytest

from app import config
from tests.conftest import ROOT

APP = ROOT / "app"
SOURCES = [*APP.rglob("*.py"), ROOT / "streamlit_app.py"]


# ---- offline ------------------------------------------------------------------------------
def test_offline_guard_blocks_external_connections():
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="network access blocked"):
            sock.connect(("93.184.216.34", 80))  # fictional external address; never reached
    finally:
        sock.close()


def test_offline_guard_blocks_dns_lookups():
    with pytest.raises(AssertionError, match="DNS lookup"):
        socket.getaddrinfo("huggingface.co", 443)
    assert socket.getaddrinfo("localhost", 8501)  # loopback still allowed (AppTest, local server)


FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(import|from)\s+(requests|httpx|aiohttp|urllib\.request|urllib3|http\.client|smtplib|ftplib|socket|"
    r"openai|anthropic|boto3|google\.cloud|azure|psycopg2|pymysql|pymongo|sentry_sdk)\b", re.M)


def test_no_network_cloud_or_llm_imports():
    offenders = [p.relative_to(ROOT).as_posix() for p in SOURCES if FORBIDDEN_IMPORTS.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_urls_api_keys_or_telemetry_in_runtime_code():
    for path in SOURCES:
        text = path.read_text("utf-8").lower()
        for word in ("http://", "https://", "api_key", "apikey", "telemetry.", "posthog", "segment.io"):
            assert word not in text, f"{word!r} in {path.name}"


def test_neural_models_load_local_only():
    for name in ("dense.py", "rerank.py"):
        code = (APP / "rag" / name).read_text("utf-8")
        assert "local_files_only=True" in code and 'HF_HUB_OFFLINE' in code


def test_streamlit_local_only_and_no_telemetry():
    cfg = (ROOT / ".streamlit" / "config.toml").read_text("utf-8")
    assert re.search(r'^address\s*=\s*"localhost"', cfg, re.M)
    assert re.search(r"^gatherUsageStats\s*=\s*false", cfg, re.M)
    assert re.search(r"^showErrorDetails\s*=\s*false", cfg, re.M)


# ---- code hygiene ---------------------------------------------------------------------------
def test_no_bare_except():
    for path in SOURCES:
        assert not re.search(r"except\s*:", path.read_text("utf-8")), path.name


def test_ui_has_no_sql():
    code = (ROOT / "streamlit_app.py").read_text("utf-8")
    assert "sqlite3" not in code and not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\s+(\*|INTO|FROM|\w+\s+SET)", code)


def test_sql_values_are_bound_parameters():
    """f-strings may only build placeholder lists / WHERE skeletons, never interpolate values."""
    for path in APP.rglob("*.py"):
        for m in re.finditer(r'execute\(\s*f"([^"]*)"', path.read_text("utf-8")):
            interpolated = re.findall(r"\{([^}]*)\}", m.group(1))
            assert all(name.strip() in {"where"} for name in interpolated), f"{path.name}: {m.group(0)}"


RAG_CONSTANTS = {config.BM25_K1, config.BM25_B, config.CHUNK_TOKENS, config.CHUNK_OVERLAP,
                 config.RETRIEVER_TOP_N, config.RRF_K, config.RERANK_THRESHOLD}


def test_rag_constants_live_in_config_only():
    for path in (APP / "rag").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value in RAG_CONSTANTS:
                allowed = node.value in (0.75, 1.5) and path.name == "rerank.py"   # scorer weights, not BM25
                assert allowed, f"{path.name}:{node.lineno} hard-codes {node.value}; use app/config.py"


def test_spec_parameters():
    assert 1.2 <= config.BM25_K1 <= 1.5 and config.BM25_B == 0.75
    assert (config.CHUNK_TOKENS, config.CHUNK_OVERLAP, config.RETRIEVER_TOP_N, config.RRF_K) == (512, 64, 50, 60)
    assert config.RERANK_THRESHOLD == 0.40 and 3 <= config.FINAL_TOP_K <= 5
    assert config.CB_MAX_CONSECUTIVE_FAILURES == 2 and config.BATCH_PATTERN == r"^LOT-\d{5}$"


# ---- governance documents -----------------------------------------------------------------------
def test_claude_md_has_required_sections():
    text = (ROOT / "CLAUDE.md").read_text("utf-8")
    for heading in ("## 1. What this project is", "## 2. Hard scope boundaries", "## 3. Data handling rules",
                    "## 4. Architecture", "## 5. Coding guidelines", "## 6. Testing conventions",
                    "## 7. Workflow for agents"):
        assert heading in text
    for rule in ("causality", "treatment", "reportability", "No PII", "metadata only", "No network"):
        assert rule.lower() in text.lower()


def test_plan_is_approved_with_signature_line():
    plan = (ROOT / "docs" / "PLAN.md").read_text("utf-8")
    assert re.search(r"Approved by:\s*\S+", plan) and re.search(r"Date:\s*2026-\d{2}-\d{2}", plan)
    assert "## Milestone A" in plan and "## Verification" in plan


def test_review_skill_structure():
    skill = ROOT / ".claude" / "skills" / "pharma-intake-review"
    text = (skill / "SKILL.md").read_text("utf-8")
    assert text.startswith("---") and "name: pharma-intake-review" in text and "description:" in text
    for name in ("data_minimization", "error_handling", "auditability", "test_coverage"):
        assert (skill / "checklists" / f"{name}.md").is_file()
    assert (skill / "scripts" / "static_checks.py").is_file()


def test_skill_invocation_evidence_present():
    text = (ROOT / "docs" / "SKILL_REVIEW_EVIDENCE.md").read_text("utf-8")
    assert "Round 1" in text and "Round 3" in text and "BLOCKER" in text and "passed" in text


def test_hook_registered_for_edit_tools():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text("utf-8"))
    matcher = settings["hooks"]["PostToolUse"][0]["matcher"]
    assert set(matcher.split("|")) >= {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def test_reflection_answers_five_questions():
    text = (ROOT / "docs" / "REFLECTION.md").read_text("utf-8")
    assert len(re.findall(r"^## [1-5]\. ", text, re.M)) == 5


def test_demo_evidence_covers_required_walkthrough():
    text = (ROOT / "docs" / "DEMO_EVIDENCE.md").read_text("utf-8")
    for section in ("Intake of two fictional reports", "Human-in-the-Loop", "Multi-stage hybrid RAG",
                    "Audit trail", "Governance hook log"):
        assert section in text


def test_acceptance_matrix_rows_are_sequential_and_done():
    text = (ROOT / "docs" / "ACCEPTANCE.md").read_text("utf-8")
    rows = re.findall(r"^\| (\d+) \|.*\| (Done[^|]*) \|\s*$", text, re.M)
    assert [int(n) for n, _ in rows] == list(range(1, len(rows) + 1)) and len(rows) >= 40


def test_acceptance_matrix_references_real_tests():
    """Every `tests/<file>.py::<name>` cited in ACCEPTANCE.md must exist (no stale traceability)."""
    text = (ROOT / "docs" / "ACCEPTANCE.md").read_text("utf-8")
    refs = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", text)
    assert len(refs) >= 40
    missing = [f"{f}::{n}" for f, n in refs
               if not re.search(rf"^def {n}\(", (ROOT / f).read_text("utf-8"), re.M)]
    assert missing == []
