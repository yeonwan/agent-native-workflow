from __future__ import annotations

import json
import re


class GoTestDigester:
    """Extract failure summaries from ``go test`` output.

    Handles plain text output (``go test ./...``), verbose output
    (``go test -v ./...``), and gracefully falls back when JSON output
    (``go test -json``) is detected.

    In ``go test`` output the log lines (assertion messages, file:line output)
    appear **before** the ``--- FAIL:`` header that closes the test.  This
    digester therefore buffers inter-test lines and attaches them to the
    failure block only when a ``--- FAIL:`` header is seen, discarding them
    when a ``--- PASS:`` / ``--- SKIP:`` header is seen.
    """

    def __init__(self, max_chars: int = 4000) -> None:
        self._max_chars = max_chars

    # "--- FAIL: TestFoo (0.00s)"
    _FAIL_HEADER = re.compile(r"^--- FAIL:\s+\S+")
    # "--- PASS: TestFoo (0.00s)" or "--- SKIP: TestFoo (0.00s)"
    _PASS_HEADER = re.compile(r"^--- (PASS|SKIP):\s+\S+")
    # "=== RUN   TestFoo"
    _RUN_LINE = re.compile(r"^=== RUN\s")
    # "=== PAUSE" / "=== CONT"
    _PAUSE_CONT = re.compile(r"^=== (PAUSE|CONT)\s")
    # Package-level FAIL summary: "FAIL\tgithub.com/foo/bar\t0.015s"
    # Also bare "FAIL" on its own line
    _PKG_FAIL = re.compile(r"^FAIL(\s|$)")
    # Package-level ok summary: "ok  \tgithub.com/foo/bar\t0.015s"
    _PKG_OK = re.compile(r"^ok\s")

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        # Detect JSON output and convert to plain text lines.
        lines = raw_output.splitlines()
        text_lines = self._strip_json_lines(lines)

        failure_blocks: list[str] = []
        summary_lines: list[str] = []

        # Lines collected since the last test boundary (=== RUN / --- PASS/FAIL).
        # These are the log/assertion lines that belong to the current test.
        pending_lines: list[str] = []

        for line in text_lines:
            # ── Test result headers ──────────────────────────────────────────
            if self._FAIL_HEADER.match(line):
                # The pending lines are the log output of this failing test.
                block = pending_lines + [line]
                failure_blocks.append("\n".join(block))
                pending_lines = []
                continue

            if self._PASS_HEADER.match(line):
                # Discard pending lines — the test passed, we don't want its output.
                pending_lines = []
                continue

            # ── Package-level summary lines ──────────────────────────────────
            if self._PKG_FAIL.match(line):
                # Flush any leftover pending (e.g. build errors before the FAIL line).
                if pending_lines:
                    failure_blocks.append("\n".join(pending_lines))
                    pending_lines = []
                summary_lines.append(line.strip())
                continue

            if self._PKG_OK.match(line):
                pending_lines = []  # passing package — discard
                continue

            # ── Noise: skip RUN / PAUSE / CONT ──────────────────────────────
            if self._RUN_LINE.match(line) or self._PAUSE_CONT.match(line):
                continue

            # ── Everything else: buffer as potential log output ──────────────
            pending_lines.append(line)

        # Flush any remaining pending lines (e.g. build errors with no FAIL line).
        if pending_lines:
            failure_blocks.append("\n".join(pending_lines))

        sections: list[str] = []
        if failure_blocks:
            sections.append("Failed tests:\n" + "\n\n".join(failure_blocks[:30]))
        if summary_lines:
            sections.append("\n".join(dict.fromkeys(summary_lines)))

        if sections:
            result = "\n\n".join(sections)
            return result[: self._max_chars]

        # Fallback: return tail of output (no structured failures found but
        # exit_code != 0, so something went wrong).
        return "\n".join(text_lines[-60:])[: self._max_chars]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_json_lines(lines: list[str]) -> list[str]:
        """Return non-JSON lines from the output.

        ``go test -json`` emits one JSON object per line.  We detect this by
        checking whether the *majority* of non-blank lines parse as JSON
        objects.  If so, we extract the ``Output`` field from each action
        so that downstream parsing still works.
        """
        json_count = 0
        total = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            total += 1
            if stripped.startswith("{"):
                try:
                    json.loads(stripped)
                    json_count += 1
                except (json.JSONDecodeError, ValueError):
                    pass

        if total > 0 and json_count / total >= 0.7:
            # Looks like JSON output — extract Output fields
            extracted: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("{"):
                    try:
                        obj = json.loads(stripped)
                        output = obj.get("Output", "")
                        if output:
                            # Output lines typically end with "\n"
                            extracted.extend(output.splitlines())
                    except (json.JSONDecodeError, ValueError):
                        extracted.append(line)
                else:
                    extracted.append(line)
            return extracted

        return lines
