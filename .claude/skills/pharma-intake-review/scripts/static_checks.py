"""Deterministic static checks for the pharma-intake-review skill. Exit 1 on any failure."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = [p for p in [*(ROOT / "app").rglob("*.py"), ROOT / "streamlit_app.py"] if p.exists()]

CHECKS: list[tuple[str, re.Pattern[str], set[str]]] = [
    # (description, pattern, files allowed to match - relative posix paths)
    ("PII field/column names", re.compile(r"\b(patient_name|patient_initials|date_of_birth|\bdob\b|home_address|phone_number|email_address|mrn)\s*[:=]", re.I), set()),
    ("f-string / %-formatted SQL with values", re.compile(r"execute\(\s*f?[\"'].*(\{[a-z_]+\}|%s).*(VALUES|WHERE).*[\"']\s*%?", re.I), set()),
    ("audit UPDATE/DELETE path", re.compile(r"(UPDATE|DELETE FROM)\s+audit_log", re.I), {"app/db.py"}),
    ("network client usage", re.compile(r"\bimport (requests|httpx|urllib3)\b|urllib\.request|socket\.connect|openai|anthropic", re.I), set()),
    ("direct status UPDATE outside workflow", re.compile(r"UPDATE reports SET status", re.I), {"app/workflow.py"}),
    ("traceback shown in UI", re.compile(r"st\.exception\(|traceback\.format_exc", re.I), set()),
    ("hard-coded RAG constants outside config", re.compile(r"\b(k1\s*=\s*1\.[0-9]|rrf_k\s*=\s*60|threshold\s*=\s*0\.4)\b", re.I), {"app/config.py"}),
]


def main() -> int:
    failures = 0
    for desc, pat, allowed in CHECKS:
        hits = []
        for path in SRC:
            rel = path.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()[:100]}")
        status = "PASS" if not hits else "FAIL"
        failures += bool(hits)
        print(f"[{status}] {desc}")
        for h in hits:
            print(f"    {h}")
    print("static_checks:", "PASS" if not failures else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
