"""Circuit breaker for repeated invalid tool calls.

The signature of a call is the tool name plus a SHA-256 of its canonical JSON arguments.
If the *same* signature fails validation more than CB_MAX_CONSECUTIVE_FAILURES times in a
row (i.e. on the 3rd consecutive identical invalid call) the breaker opens and execution is
halted. A successful call, or a call with a different signature, resets the streak. An open
signature stays blocked until `reset()` is called.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app import config


class CircuitOpenError(RuntimeError):
    def __init__(self, tool: str, signature: str, failures: int):
        self.tool, self.signature, self.failures = tool, signature, failures
        super().__init__(
            f"Circuit breaker OPEN for tool '{tool}': {failures} consecutive identical invalid calls. "
            "Execution halted to prevent an infinite retry loop. Correct the arguments and reset."
        )


def call_signature(tool: str, args: Any) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    return f"{tool}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


@dataclass
class CircuitBreaker:
    max_consecutive_failures: int = config.CB_MAX_CONSECUTIVE_FAILURES
    last_signature: str | None = None
    consecutive_failures: int = 0
    open_signatures: dict[str, int] = field(default_factory=dict)   # signature -> failures when tripped

    def is_open(self, signature: str) -> bool:
        return signature in self.open_signatures

    def before_call(self, tool: str, signature: str) -> None:
        if self.is_open(signature):
            raise CircuitOpenError(tool, signature, self.open_signatures[signature])

    def record_failure(self, tool: str, signature: str) -> int:
        """Register an invalid call. Raises CircuitOpenError when the threshold is exceeded."""
        if signature == self.last_signature:
            self.consecutive_failures += 1
        else:
            self.last_signature, self.consecutive_failures = signature, 1
        if self.consecutive_failures > self.max_consecutive_failures:
            self.open_signatures[signature] = self.consecutive_failures
            raise CircuitOpenError(tool, signature, self.consecutive_failures)
        return self.consecutive_failures

    def record_success(self) -> None:
        self.last_signature, self.consecutive_failures = None, 0

    def reset(self) -> None:
        self.record_success()
        self.open_signatures.clear()
