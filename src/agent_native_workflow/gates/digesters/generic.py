from __future__ import annotations


# Patterns ranked by signal strength — high-signal first so truncation
# preserves the most useful lines.
_HIGH_SIGNAL = ("FAILED", "FAIL", "AssertionError", "AssertionFailedError", "panic:", "PANIC")
_MED_SIGNAL = ("ERROR", "Error:", "error:", "expected", "actual", "not equal", "✗", "✘", "×")
_LOW_SIGNAL = ("assert", "WARN", "warning:")


class GenericDigester:
    """Best-effort digester for unknown commands (lint, custom test runners, etc.)."""

    def __init__(self, max_chars: int = 4000) -> None:
        self._max_chars = max_chars

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""
        if len(raw_output) <= self._max_chars:
            return raw_output

        high: list[str] = []
        med: list[str] = []
        low: list[str] = []
        for line in raw_output.splitlines():
            if any(p in line for p in _HIGH_SIGNAL):
                high.append(line)
            elif any(p in line for p in _MED_SIGNAL):
                med.append(line)
            elif any(p in line for p in _LOW_SIGNAL):
                low.append(line)

        # Build summary from highest-signal lines first, filling budget.
        parts: list[str] = []
        budget = self._max_chars
        for bucket in (high, med, low):
            for line in bucket:
                needed = len(line) + 1  # +1 for newline
                if needed > budget:
                    continue
                parts.append(line)
                budget -= needed
            if not budget:
                break

        if parts:
            return "\n".join(parts)

        # Absolute fallback: tail of output.
        lines = raw_output.splitlines()
        tail = "\n".join(lines[-60:])
        return tail[: self._max_chars]
