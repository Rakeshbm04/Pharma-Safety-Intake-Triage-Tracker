"""PostToolUse governance hook: append a metadata-only record of every file edit.

Deterministic by design: no model reasoning, no network, same input -> same record.
The event payload deliberately EXCLUDES file content, old/new strings, descriptions
and prompts so that no sensitive or user-entered text ever reaches the log.

Registered in .claude/settings.json. Always exits 0 so it can never block a tool call.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Only these keys are ever written. Anything else in the payload is dropped.
ALLOWED_KEYS = ("ts_utc", "event", "session_id", "tool_name", "file_path", "content_bytes", "success")


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def relative_path(raw: str, root: Path) -> str:
    """Return a repo-relative POSIX path; absolute paths outside the repo become their basename."""
    if not raw:
        return ""
    try:
        return Path(raw).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return f"<outside-repo>/{Path(raw).name}"


def content_size(tool_input: dict) -> int:
    """Size in bytes of the written content -- the size only, never the content itself."""
    for key in ("content", "new_string", "new_source"):
        val = tool_input.get(key)
        if isinstance(val, str):
            return len(val.encode("utf-8"))
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        return sum(len(str(e.get("new_string", "")).encode("utf-8")) for e in edits if isinstance(e, dict))
    return 0


def build_record(payload: dict, root: Path) -> dict | None:
    tool_name = payload.get("tool_name", "")
    if tool_name not in EDIT_TOOLS:
        return None
    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response")
    success = True
    if isinstance(tool_response, dict) and tool_response.get("success") is False:
        success = False
    record = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "event": "file_edit",
        "session_id": str(payload.get("session_id", ""))[:64],
        "tool_name": tool_name,
        "file_path": relative_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "", root),
        "content_bytes": content_size(tool_input),
        "success": success,
    }
    return {k: record[k] for k in ALLOWED_KEYS}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        root = project_root()
        record = build_record(payload, root)
        if record is None:
            return 0
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "agent_edit_audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:  # never block the agent because of logging
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
