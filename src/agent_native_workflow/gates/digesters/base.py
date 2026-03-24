from __future__ import annotations

from typing import Protocol


class GateDigester(Protocol):
    """Transforms raw gate command output into a concise failure summary."""

    def digest(self, raw_output: str, exit_code: int) -> str:
        """Return text suitable for agent feedback.

        For ``exit_code == 0``, return empty string.
        For failures, return a shortened, failure-focused summary.
        """
        ...
