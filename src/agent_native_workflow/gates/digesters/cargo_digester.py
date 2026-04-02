from __future__ import annotations

import re


class CargoTestDigester:
    """Extract failure summaries from ``cargo test`` output.

    Cargo test output typically has the following structure::

        running N tests
        test foo::bar ... ok
        test foo::baz ... FAILED

        failures:

        ---- foo::baz stdout ----
        thread 'foo::baz' panicked at 'assertion failed: ...', src/lib.rs:42:5
        left:  1
        right: 2

        failures:
            foo::baz

        test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out

    This digester collects the ``---- <name> stdout ----`` sections and the
    final ``test result:`` summary while filtering out compilation progress,
    passing-test lines, and other noise.
    """

    def __init__(self, max_chars: int = 4000) -> None:
        self._max_chars = max_chars

    # "---- foo::bar stdout ----"
    _STDOUT_HEADER = re.compile(r"^---- .+ stdout ----$")
    # "test result: FAILED. ..."
    _RESULT_LINE = re.compile(r"^test result:", re.IGNORECASE)
    # "test foo::bar ... ok" / "test foo::bar ... FAILED"
    _TEST_STATUS = re.compile(r"^test .+ \.\.\. (ok|FAILED|ignored)$")
    # "failures:" header (the section listing failure names)
    _FAILURES_HEADER = re.compile(r"^failures:\s*$")
    # Compilation / cargo progress lines
    _CARGO_PROGRESS = re.compile(
        r"^\s*(Compiling|Downloading|Downloaded|Finished|Running|Fresh|Checking|Blocking)\s"
        r"|^warning:|^error\[|^note:|^ --> "
        r"|^\s+\|"    # code context pipes from rustc
        r"|^\s+\^\^"  # rustc caret underline
        r"|^running \d+ tests?"
    )

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        lines = raw_output.splitlines()
        failure_blocks: list[str] = []
        summary_lines: list[str] = []
        failure_name_lines: list[str] = []

        current_block: list[str] = []
        in_stdout_block = False
        in_failures_list = False

        for line in lines:
            # Result summary line — always capture
            if self._RESULT_LINE.match(line) and "FAILED" in line:
                summary_lines.append(line.strip())
                continue

            # Start of a per-test stdout section
            if self._STDOUT_HEADER.match(line):
                if current_block:
                    failure_blocks.append("\n".join(current_block))
                current_block = [line]
                in_stdout_block = True
                in_failures_list = False
                continue

            if in_stdout_block:
                # An empty line followed by another stdout header ends the block;
                # we keep going until the next stdout header or the failures list.
                if self._STDOUT_HEADER.match(line):
                    failure_blocks.append("\n".join(current_block))
                    current_block = [line]
                    continue
                if self._FAILURES_HEADER.match(line):
                    failure_blocks.append("\n".join(current_block))
                    current_block = []
                    in_stdout_block = False
                    in_failures_list = True
                    continue
                if self._RESULT_LINE.match(line):
                    # Result line ends the stdout section
                    failure_blocks.append("\n".join(current_block))
                    current_block = []
                    in_stdout_block = False
                    if "FAILED" in line:
                        summary_lines.append(line.strip())
                    continue

                current_block.append(line)
                continue

            # "failures:" section (the list of names)
            if self._FAILURES_HEADER.match(line):
                in_failures_list = True
                in_stdout_block = False
                continue

            if in_failures_list:
                if self._RESULT_LINE.match(line):
                    in_failures_list = False
                    if "FAILED" in line:
                        summary_lines.append(line.strip())
                    continue
                stripped = line.strip()
                if stripped:
                    failure_name_lines.append(stripped)
                continue

            # Discard noise outside blocks
            # (cargo progress, passing tests, running header)
            # Anything else we leave alone (could be build errors etc.)

        # Flush any open block
        if current_block:
            failure_blocks.append("\n".join(current_block))

        sections: list[str] = []
        if failure_blocks:
            sections.append("Failed tests:\n" + "\n\n".join(failure_blocks[:20]))
        if failure_name_lines:
            sections.append("failures:\n    " + "\n    ".join(failure_name_lines))
        if summary_lines:
            sections.append("\n".join(dict.fromkeys(summary_lines)))

        if sections:
            result = "\n\n".join(sections)
            return result[: self._max_chars]

        # Fallback: filter out compilation noise and return remaining lines
        useful = [ln for ln in lines if not self._CARGO_PROGRESS.match(ln)]
        if useful:
            return "\n".join(useful[-60:])[: self._max_chars]

        return "\n".join(lines[-60:])[: self._max_chars]
