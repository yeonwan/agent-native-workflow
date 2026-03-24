from __future__ import annotations


class GenericDigester:
    """Best-effort digester for unknown commands (lint, custom test runners, etc.)."""

    def __init__(self, max_chars: int = 2000) -> None:
        self._max_chars = max_chars

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""
        if len(raw_output) <= self._max_chars:
            return raw_output

        failure_patterns = (
            "FAILED",
            "FAIL",
            "ERROR",
            "Error:",
            "error:",
            "AssertionError",
            "assert",
            "panic:",
            "PANIC",
            "expected",
            "actual",
            "not equal",
            "✗",
            "✘",
            "×",
        )
        relevant: list[str] = []
        for line in raw_output.splitlines():
            if any(p in line for p in failure_patterns):
                relevant.append(line)

        if relevant:
            summary = "\n".join(relevant[:50])
            if len(summary) <= self._max_chars:
                return summary

        lines = raw_output.splitlines()
        tail = "\n".join(lines[-40:])
        return tail[: self._max_chars]
