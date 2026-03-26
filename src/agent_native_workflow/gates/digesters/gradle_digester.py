from __future__ import annotations

import re


class GradleDigester:
    """Extract test failures and build errors from Gradle / Maven output.

    Recognises patterns from ``./gradlew test``, ``mvn test``, and
    ``mvn verify``.  The goal is to surface *which* tests failed and the
    root-cause exception/assertion while filtering out noise like compiler
    warnings and STANDARD_ERROR log spew.
    """

    def __init__(self, max_chars: int = 4000) -> None:
        self._max_chars = max_chars

    # Gradle format: "TestClass > methodName() FAILED"
    _GRADLE_FAILED = re.compile(r"^\S.+ FAILED\s*$")
    # Maven surefire format: "  TestClass.method:123 Assertion message"
    _MVN_FAILURE_HEADER = re.compile(r"^Tests run:.*Failures:|^\[ERROR\] Tests run:")
    # Maven surefire: "[ERROR]   FooTest.bar:42 expected..." or "  FooTest.bar:42 ..."
    _MVN_FAILED_TEST = re.compile(r"^(\[ERROR\])?\s{2,}\S+\.\S+:\d+")
    # Summary line: "457 tests completed, 2 failed"
    _SUMMARY = re.compile(r"\d+ tests? completed.*\d+ failed|\d+ tests? run.*\d+ failure", re.IGNORECASE)
    # Task failure: "> Task :test FAILED"
    _TASK_FAILED = re.compile(r"^> Task .+ FAILED")
    # Build result
    _BUILD_RESULT = re.compile(r"^BUILD (FAILED|SUCCESSFUL)")
    # Caused-by / exception first line (indented stacktrace header)
    _EXCEPTION = re.compile(r"^\s+(org\.\w|java\.\w|com\.\w|Caused by:)")
    # Noise: compiler warnings, STANDARD_ERROR markers, logback, blank
    _NOISE = re.compile(
        r"warning: \[|STANDARD_ERROR|STANDARD_OUT"
        r"|^\s*\^$|logback|UP-TO-DATE|FROM-CACHE|actionable task"
        r"|Note: Some input files|Note: Recompile with"
        r"|^\s*$"
    )

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""
        if len(raw_output) <= self._max_chars:
            return raw_output

        lines = raw_output.splitlines()
        failure_blocks: list[str] = []
        summary_lines: list[str] = []
        current_block: list[str] = []
        in_failure = False

        for line in lines:
            # Capture "TestClass > method FAILED" blocks
            if self._GRADLE_FAILED.match(line) or self._TASK_FAILED.match(line):
                if current_block:
                    failure_blocks.append("\n".join(current_block))
                current_block = [line]
                in_failure = True
                continue

            if in_failure:
                # Exception / Caused-by lines belong to the block
                if self._EXCEPTION.match(line):
                    current_block.append(line)
                    continue
                # Blank line or non-indented line ends the block
                if not line.strip() or (not line.startswith(" ") and not line.startswith("\t")):
                    failure_blocks.append("\n".join(current_block))
                    current_block = []
                    in_failure = False
                    # Don't skip this line — fall through to summary check
                else:
                    current_block.append(line)
                    continue

            # Summary / build-result lines
            if self._SUMMARY.search(line) or self._BUILD_RESULT.match(line):
                summary_lines.append(line.strip())
                continue

            # Maven failure header
            if self._MVN_FAILURE_HEADER.search(line):
                summary_lines.append(line.strip())
                continue
            if self._MVN_FAILED_TEST.match(line):
                failure_blocks.append(line.strip())
                continue

        if current_block:
            failure_blocks.append("\n".join(current_block))

        # Assemble output: failures first, then summary.
        sections: list[str] = []
        if failure_blocks:
            sections.append("Failed tests:\n" + "\n\n".join(failure_blocks[:20]))
        if summary_lines:
            sections.append("\n".join(dict.fromkeys(summary_lines)))

        if sections:
            result = "\n\n".join(sections)
            return result[: self._max_chars]

        # Fallback: filter out noise, return what remains.
        useful = [ln for ln in lines if not self._NOISE.search(ln)]
        if useful:
            return "\n".join(useful[-60:])[: self._max_chars]

        return "\n".join(lines[-60:])[: self._max_chars]
