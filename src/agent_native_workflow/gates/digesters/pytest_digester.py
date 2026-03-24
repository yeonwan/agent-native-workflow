from __future__ import annotations


class PytestDigester:
    """Extract pytest failure summary and key lines from stdout/stderr."""

    def __init__(self, max_chars: int = 3000) -> None:
        self._max_chars = max_chars

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        lines = raw_output.splitlines()
        sections: list[str] = []

        in_summary = False
        summary_lines: list[str] = []
        for line in lines:
            if "short test summary info" in line.lower():
                in_summary = True
                continue
            if in_summary:
                if line.startswith("=") or not line.strip():
                    break
                summary_lines.append(line)

        if summary_lines:
            sections.append("Failed tests:\n" + "\n".join(summary_lines))

        for line in reversed(lines):
            low = line.lower()
            if "failed" in low and ("passed" in low or "error" in low or "warnings" in low):
                sections.append(line.strip())
                break

        if not summary_lines:
            failed_lines = [ln for ln in lines if "FAILED" in ln or "ERROR collecting" in ln]
            if failed_lines:
                sections.append("Failures:\n" + "\n".join(failed_lines[:20]))

        result = "\n\n".join(sections) if sections else raw_output[-self._max_chars :]
        return result[: self._max_chars]
