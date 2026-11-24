"""Governance hook: deterministic, metadata-only, never blocks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "log_edit_event.py"
SECRET = "Patient reported severe dyspnea SECRET-CONTENT"


def run_hook(payload, project_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=data, text=True, capture_output=True, env=env, timeout=30)


def read_log(project_dir: Path) -> list[dict]:
    path = project_dir / "logs" / "agent_edit_audit.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_settings_registers_post_tool_use_hook():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entry = settings["hooks"]["PostToolUse"][0]
    assert set(entry["matcher"].split("|")) >= {"Edit", "Write"}
    assert "log_edit_event.py" in entry["hooks"][0]["command"]


def test_hook_logs_metadata_only(tmp_path):
    payload = {"session_id": "s1", "tool_name": "Write",
               "tool_input": {"file_path": str(tmp_path / "app" / "x.py"), "content": SECRET},
               "tool_response": {"success": True}}
    assert run_hook(payload, tmp_path).returncode == 0
    (rec,) = read_log(tmp_path)
    assert set(rec) == {"ts_utc", "event", "session_id", "tool_name", "file_path", "content_bytes", "success"}
    assert rec["file_path"] == "app/x.py" and rec["content_bytes"] == len(SECRET.encode())
    assert SECRET not in (tmp_path / "logs" / "agent_edit_audit.jsonl").read_text(encoding="utf-8")


def test_hook_edit_old_new_strings_not_logged(tmp_path):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.py"),
                                                    "old_string": SECRET, "new_string": SECRET + "2"}}
    run_hook(payload, tmp_path)
    assert SECRET not in (tmp_path / "logs" / "agent_edit_audit.jsonl").read_text(encoding="utf-8")


def test_hook_ignores_non_edit_tools_and_bad_input(tmp_path):
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, tmp_path).returncode == 0
    assert run_hook("not json at all", tmp_path).returncode == 0
    assert read_log(tmp_path) == []


def test_hook_is_deterministic(tmp_path):
    payload = {"session_id": "s", "tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "f.py"), "new_string": "abc"}}
    run_hook(payload, tmp_path)
    run_hook(payload, tmp_path)
    a, b = read_log(tmp_path)
    a.pop("ts_utc"), b.pop("ts_utc")
    assert a == b


def test_outside_repo_paths_reduced_to_basename(tmp_path):
    run_hook({"tool_name": "Write", "tool_input": {"file_path": "C:/Users/someone/secret/notes.txt", "content": "x"}}, tmp_path)
    assert read_log(tmp_path)[0]["file_path"] == "<outside-repo>/notes.txt"
