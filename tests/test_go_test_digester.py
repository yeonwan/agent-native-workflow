"""Unit tests for GoTestDigester."""

from __future__ import annotations

import json

from agent_native_workflow.gates.digesters.factory import build_digester
from agent_native_workflow.gates.digesters.go_test_digester import GoTestDigester


# ---------------------------------------------------------------------------
# Helpers / sample output strings
# ---------------------------------------------------------------------------

_ALL_PASS_OUTPUT = """\
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSubtract
--- PASS: TestSubtract (0.00s)
PASS
ok  \tgithub.com/example/calc\t0.003s
"""

_SINGLE_FAILURE_OUTPUT = """\
=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestDivide
    divide_test.go:15: got 0, want 2
--- FAIL: TestDivide (0.00s)
FAIL
FAIL\tgithub.com/example/calc\t0.005s
"""

_MULTIPLE_FAILURES_OUTPUT = """\
=== RUN   TestFoo
    foo_test.go:10: assertion failed: 1 != 2
--- FAIL: TestFoo (0.00s)
=== RUN   TestBar
    bar_test.go:20: unexpected nil
--- FAIL: TestBar (0.00s)
=== RUN   TestBaz
--- PASS: TestBaz (0.00s)
FAIL
FAIL\tgithub.com/example/pkg\t0.012s
"""

_VERBOSE_WITH_SUBTEST_FAILURE = """\
=== RUN   TestMath
=== RUN   TestMath/addition
=== RUN   TestMath/subtraction
    math_test.go:30: subtraction wrong: got 0, want -1
--- FAIL: TestMath/subtraction (0.00s)
--- FAIL: TestMath (0.00s)
FAIL
FAIL\tgithub.com/example/math\t0.007s
"""

_EMPTY_OUTPUT = ""

_BUILD_ERROR_OUTPUT = """\
# github.com/example/broken
./main.go:5:2: undefined: Foo
FAIL\tgithub.com/example/broken [build failed]
"""


def _make_json_output(plain_output: str) -> str:
    """Wrap plain go test output as ``go test -json`` lines."""
    lines = plain_output.splitlines(keepends=True)
    json_lines: list[str] = []
    for line in lines:
        obj = {"Action": "output", "Output": line}
        json_lines.append(json.dumps(obj))
    return "\n".join(json_lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGoTestDigesterPassReturnsEmpty:
    def test_all_pass_exit_zero(self) -> None:
        d = GoTestDigester()
        assert d.digest(_ALL_PASS_OUTPUT, 0) == ""

    def test_empty_output_exit_zero(self) -> None:
        d = GoTestDigester()
        assert d.digest(_EMPTY_OUTPUT, 0) == ""


class TestGoTestDigesterSingleFailure:
    def test_extracts_fail_header(self) -> None:
        out = GoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "--- FAIL: TestDivide" in out

    def test_extracts_assertion_message(self) -> None:
        out = GoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "got 0, want 2" in out

    def test_extracts_package_summary(self) -> None:
        out = GoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "FAIL\tgithub.com/example/calc" in out

    def test_no_pass_lines(self) -> None:
        out = GoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "--- PASS:" not in out

    def test_no_run_lines(self) -> None:
        out = GoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "=== RUN" not in out


class TestGoTestDigesterMultipleFailures:
    def test_extracts_all_fail_headers(self) -> None:
        out = GoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "--- FAIL: TestFoo" in out
        assert "--- FAIL: TestBar" in out

    def test_no_passing_test_in_output(self) -> None:
        out = GoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "TestBaz" not in out

    def test_extracts_assertion_messages(self) -> None:
        out = GoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "assertion failed" in out
        assert "unexpected nil" in out

    def test_summary_line_present(self) -> None:
        out = GoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "github.com/example/pkg" in out


class TestGoTestDigesterVerboseOutput:
    def test_subtest_failure_extracted(self) -> None:
        out = GoTestDigester().digest(_VERBOSE_WITH_SUBTEST_FAILURE, 1)
        assert "TestMath/subtraction" in out

    def test_parent_fail_present(self) -> None:
        out = GoTestDigester().digest(_VERBOSE_WITH_SUBTEST_FAILURE, 1)
        assert "--- FAIL: TestMath" in out

    def test_no_run_lines_in_verbose(self) -> None:
        out = GoTestDigester().digest(_VERBOSE_WITH_SUBTEST_FAILURE, 1)
        assert "=== RUN" not in out


class TestGoTestDigesterJsonOutput:
    def test_json_failure_extracted(self) -> None:
        json_out = _make_json_output(_SINGLE_FAILURE_OUTPUT)
        out = GoTestDigester().digest(json_out, 1)
        # After stripping JSON, the failure info should still surface
        assert "TestDivide" in out

    def test_json_pass_returns_empty(self) -> None:
        json_out = _make_json_output(_ALL_PASS_OUTPUT)
        out = GoTestDigester().digest(json_out, 0)
        assert out == ""


class TestGoTestDigesterEmptyOutput:
    def test_empty_with_nonzero_exit(self) -> None:
        out = GoTestDigester().digest(_EMPTY_OUTPUT, 1)
        # Should not raise, and should return a string (possibly empty)
        assert isinstance(out, str)


class TestGoTestDigesterBuildError:
    def test_build_error_surfaces(self) -> None:
        out = GoTestDigester().digest(_BUILD_ERROR_OUTPUT, 1)
        # The FAIL line should be present
        assert "FAIL" in out


class TestGoTestDigesterMaxChars:
    def test_respects_max_chars(self) -> None:
        big_output = (_MULTIPLE_FAILURES_OUTPUT + "\n") * 50
        out = GoTestDigester(max_chars=200).digest(big_output, 1)
        assert len(out) <= 200


class TestBuildDigesterRouting:
    def test_go_test_routes_to_go_digester(self) -> None:
        d = build_digester("test", "go test ./...")
        assert isinstance(d, GoTestDigester)

    def test_go_test_v_routes_to_go_digester(self) -> None:
        d = build_digester("test", "go test -v ./...")
        assert isinstance(d, GoTestDigester)

    def test_go_test_case_insensitive(self) -> None:
        d = build_digester("test", "Go Test ./...")
        assert isinstance(d, GoTestDigester)
