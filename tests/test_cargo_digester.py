"""Unit tests for CargoTestDigester."""

from __future__ import annotations

from agent_native_workflow.gates.digesters.cargo_digester import CargoTestDigester
from agent_native_workflow.gates.digesters.factory import build_digester


# ---------------------------------------------------------------------------
# Sample output strings
# ---------------------------------------------------------------------------

_ALL_PASS_OUTPUT = """\
   Compiling mylib v0.1.0 (/home/user/mylib)
    Finished test [unoptimized + debuginfo] target(s) in 1.23s
     Running unittests src/lib.rs (target/debug/deps/mylib-abc123)

running 3 tests
test tests::test_add ... ok
test tests::test_subtract ... ok
test tests::test_multiply ... ok

test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

_SINGLE_FAILURE_OUTPUT = """\
   Compiling mylib v0.1.0 (/home/user/mylib)
    Finished test [unoptimized + debuginfo] target(s) in 0.45s
     Running unittests src/lib.rs (target/debug/deps/mylib-abc123)

running 2 tests
test tests::test_add ... ok
test tests::test_divide ... FAILED

failures:

---- tests::test_divide stdout ----
thread 'tests::test_divide' panicked at 'assertion failed: `(left == right)`
  left: `0`,
 right: `2`', src/lib.rs:42:5
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

failures:
    tests::test_divide

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

_MULTIPLE_FAILURES_OUTPUT = """\
   Compiling myapp v0.2.0 (/home/user/myapp)
    Finished test [unoptimized + debuginfo] target(s) in 0.60s
     Running unittests src/lib.rs (target/debug/deps/myapp-def456)

running 4 tests
test unit::test_foo ... FAILED
test unit::test_bar ... FAILED
test unit::test_baz ... ok
test unit::test_qux ... ok

failures:

---- unit::test_foo stdout ----
thread 'unit::test_foo' panicked at 'assertion failed: false', src/lib.rs:10:9

---- unit::test_bar stdout ----
thread 'unit::test_bar' panicked at 'called `Option::unwrap()` on a `None` value', src/lib.rs:25:22

failures:
    unit::test_foo
    unit::test_bar

test result: FAILED. 2 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

_EMPTY_OUTPUT = ""

_COMPILATION_ERROR_OUTPUT = """\
   Compiling mylib v0.1.0 (/home/user/mylib)
error[E0425]: cannot find value `undefined_var` in this scope
  --> src/lib.rs:5:5
   |
5  |     undefined_var + 1
   |     ^^^^^^^^^^^^^ not found in this scope

error: aborting due to previous error

For more information about this error, try `rustc --explain E0425`.
error: could not compile `mylib` due to previous error
"""

_DOC_TEST_FAILURE = """\
   Compiling mylib v0.1.0 (/home/user/mylib)
    Finished test [unoptimized + debuginfo] target(s) in 0.30s
   Doc-tests mylib

running 1 test
test src/lib.rs - add (line 5) ... FAILED

failures:

---- src/lib.rs - add (line 5) stdout ----
thread 'main' panicked at 'assertion failed: add(2, 3) == 6', src/lib.rs:7:5

failures:
    src/lib.rs - add (line 5)

test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.15s
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCargoTestDigesterPassReturnsEmpty:
    def test_all_pass_exit_zero(self) -> None:
        d = CargoTestDigester()
        assert d.digest(_ALL_PASS_OUTPUT, 0) == ""

    def test_empty_output_exit_zero(self) -> None:
        d = CargoTestDigester()
        assert d.digest(_EMPTY_OUTPUT, 0) == ""


class TestCargoTestDigesterSingleFailure:
    def test_extracts_stdout_header(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "---- tests::test_divide stdout ----" in out

    def test_extracts_panic_message(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "panicked at" in out

    def test_extracts_left_right_comparison(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "left:" in out.lower() or "right:" in out.lower() or "left" in out

    def test_extracts_summary_line(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "test result: FAILED" in out

    def test_no_compilation_progress(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        assert "Compiling" not in out
        assert "Finished" not in out

    def test_no_passing_test_lines(self) -> None:
        out = CargoTestDigester().digest(_SINGLE_FAILURE_OUTPUT, 1)
        # "test tests::test_add ... ok" should not appear
        assert "test tests::test_add ... ok" not in out


class TestCargoTestDigesterMultipleFailures:
    def test_extracts_all_stdout_headers(self) -> None:
        out = CargoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "---- unit::test_foo stdout ----" in out
        assert "---- unit::test_bar stdout ----" in out

    def test_extracts_panic_messages(self) -> None:
        out = CargoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "assertion failed: false" in out
        assert "Option::unwrap()" in out

    def test_summary_line_present(self) -> None:
        out = CargoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "test result: FAILED" in out
        assert "2 failed" in out

    def test_no_passing_tests_in_output(self) -> None:
        out = CargoTestDigester().digest(_MULTIPLE_FAILURES_OUTPUT, 1)
        assert "test_baz" not in out
        assert "test_qux" not in out


class TestCargoTestDigesterEmptyOutput:
    def test_empty_nonzero_exit(self) -> None:
        out = CargoTestDigester().digest(_EMPTY_OUTPUT, 1)
        assert isinstance(out, str)


class TestCargoTestDigesterDocTestFailure:
    def test_doc_test_stdout_header_captured(self) -> None:
        out = CargoTestDigester().digest(_DOC_TEST_FAILURE, 1)
        assert "src/lib.rs - add" in out

    def test_doc_test_panic_captured(self) -> None:
        out = CargoTestDigester().digest(_DOC_TEST_FAILURE, 1)
        assert "panicked at" in out

    def test_doc_test_summary_present(self) -> None:
        out = CargoTestDigester().digest(_DOC_TEST_FAILURE, 1)
        assert "test result: FAILED" in out


class TestCargoTestDigesterMaxChars:
    def test_respects_max_chars(self) -> None:
        big_output = (_MULTIPLE_FAILURES_OUTPUT + "\n") * 30
        out = CargoTestDigester(max_chars=200).digest(big_output, 1)
        assert len(out) <= 200


class TestBuildDigesterRouting:
    def test_cargo_test_routes_to_cargo_digester(self) -> None:
        d = build_digester("test", "cargo test")
        assert isinstance(d, CargoTestDigester)

    def test_cargo_test_with_args_routes_correctly(self) -> None:
        d = build_digester("test", "cargo test --release -- --nocapture")
        assert isinstance(d, CargoTestDigester)

    def test_cargo_test_case_insensitive(self) -> None:
        d = build_digester("test", "Cargo Test")
        assert isinstance(d, CargoTestDigester)
