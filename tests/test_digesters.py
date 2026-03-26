"""Gate output digesters (ENHANCE Phase C)."""

from __future__ import annotations

import json

from agent_native_workflow.gates.digesters.factory import build_digester
from agent_native_workflow.gates.digesters.generic import GenericDigester
from agent_native_workflow.gates.digesters.gradle_digester import GradleDigester
from agent_native_workflow.gates.digesters.jest_digester import JestDigester
from agent_native_workflow.gates.digesters.pytest_digester import PytestDigester


def test_generic_pass_returns_empty() -> None:
    assert GenericDigester().digest("anything", 0) == ""


def test_generic_small_failure_unchanged() -> None:
    msg = "Error: one line\n"
    assert GenericDigester(max_chars=5000).digest(msg, 1) == msg


def test_pytest_digester_extracts_summary_section() -> None:
    raw = """
foo
=========================== short test summary info ============================
FAILED tests/test_x.py::test_a - AssertionError: 1 != 2
========================= 1 failed, 2 passed in 1.2s ==========================
"""
    out = PytestDigester().digest(raw, 1)
    assert "FAILED tests/test_x.py::test_a" in out
    assert "1 failed" in out.lower() or "failed" in out.lower()


def test_build_digester_pytest_command() -> None:
    d = build_digester("test", "uv run pytest tests/")
    assert isinstance(d, PytestDigester)


def test_build_digester_jest_command() -> None:
    assert isinstance(build_digester("test", "npx jest"), JestDigester)
    assert isinstance(build_digester("test", "npx vitest run"), JestDigester)


def test_build_digester_unknown_uses_generic() -> None:
    assert isinstance(build_digester("lint", "ruff check ."), GenericDigester)


def test_generic_prioritises_high_signal_over_noise() -> None:
    """Pattern-matched lines that exceed max_chars should be truncated, not dropped."""
    # Build output where high-signal lines are buried among low-signal noise.
    high = ["SomeTest > method FAILED"] * 3
    noise = ["    2026-01-01 ERROR [,,] some log spam"] * 200
    raw = "\n".join(high + noise)
    out = GenericDigester(max_chars=500).digest(raw, 1)
    # High-signal FAILED lines must appear.
    assert "FAILED" in out
    # The 200 ERROR noise lines should not crowd out FAILED.
    assert out.count("FAILED") == 3


def test_generic_does_not_silently_drop_matches() -> None:
    """When pattern-matched summary > max_chars, old code silently fell through to tail."""
    lines = [f"test_{i} FAILED" for i in range(100)]
    lines += ["trailing noise"] * 40
    raw = "\n".join(lines)
    out = GenericDigester(max_chars=500).digest(raw, 1)
    assert "FAILED" in out


# ── Gradle digester ──────────────────────────────────────────────────────────


def test_build_digester_gradle_command() -> None:
    assert isinstance(build_digester("test", "./gradlew test"), GradleDigester)
    assert isinstance(build_digester("test", "gradle build"), GradleDigester)


def test_build_digester_maven_command() -> None:
    assert isinstance(build_digester("test", "mvn test"), GradleDigester)
    assert isinstance(build_digester("test", "./mvnw verify"), GradleDigester)


def test_gradle_pass_returns_empty() -> None:
    assert GradleDigester().digest("BUILD SUCCESSFUL in 3s", 0) == ""


def test_gradle_extracts_failed_tests_with_stacktrace() -> None:
    # Pad with enough noise to exceed max_chars so parsing kicks in.
    noise = "\n".join([f"    2026-01-01 INFO [Test worker] line {i}" for i in range(100)])
    raw = f"""\
> Task :compileJava UP-TO-DATE
{noise}

FooTest > bar() FAILED
    org.opentest4j.AssertionFailedError at FooTest.java:42
        Caused by: java.lang.NullPointerException at FooTest.java:44

BazTest > qux() FAILED
    java.lang.IllegalStateException at BazTest.java:10

> Task :test FAILED

457 tests completed, 2 failed

BUILD FAILED in 12s
"""
    out = GradleDigester(max_chars=2000).digest(raw, 1)
    assert "FooTest > bar() FAILED" in out
    assert "AssertionFailedError" in out
    assert "NullPointerException" in out
    assert "BazTest > qux() FAILED" in out
    assert "457 tests completed, 2 failed" in out
    assert "BUILD FAILED" in out
    # Noise must NOT appear
    assert "UP-TO-DATE" not in out


def test_gradle_filters_compiler_warnings() -> None:
    # Pad to exceed max_chars so the digester doesn't return raw.
    warnings = "\n".join(
        [f"/src/Foo.java:{i}: warning: [removal] old{i}() deprecated" for i in range(80)]
    )
    raw = f"""\
FooTest > bar() FAILED
    org.opentest4j.AssertionFailedError at FooTest.java:42

> Task :test FAILED
{warnings}
Note: Some input files use unchecked or unsafe operations.
10 warnings

5 tests completed, 1 failed

BUILD FAILED in 5s
"""
    out = GradleDigester(max_chars=2000).digest(raw, 1)
    assert "FooTest > bar() FAILED" in out
    assert "warning: [removal]" not in out
    assert "unchecked" not in out


def test_gradle_maven_surefire_output() -> None:
    raw = """\
[ERROR] Tests run: 10, Failures: 2, Errors: 0
[ERROR]   FooTest.bar:42 expected:<1> but was:<2>
[ERROR]   BazTest.qux:10 null

BUILD FAILED
"""
    out = GradleDigester().digest(raw, 1)
    assert "FooTest.bar:42" in out
    assert "BazTest.qux:10" in out
    assert "BUILD FAILED" in out


def test_gradle_small_output_returned_as_is() -> None:
    raw = "BUILD FAILED\nsome error"
    out = GradleDigester().digest(raw, 1)
    assert out == raw


def test_gradle_respects_max_chars() -> None:
    failures = "\n".join([f"Test{i} > method() FAILED" for i in range(200)])
    raw = failures + "\n\nBUILD FAILED in 5s\n"
    out = GradleDigester(max_chars=500).digest(raw, 1)
    assert len(out) <= 500


def test_jest_digester_json() -> None:
    payload = {
        "testResults": [
            {
                "assertionResults": [
                    {
                        "status": "failed",
                        "fullName": "suite a",
                        "failureMessages": ["expected 1 got 2"],
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload)
    out = JestDigester().digest(raw, 1)
    assert "suite a" in out
    assert "expected 1" in out
