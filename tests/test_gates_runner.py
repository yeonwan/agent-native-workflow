"""Quality gate runner (subprocess + digest on failure)."""

from __future__ import annotations

from agent_native_workflow.gates.runner import run_gate_command, run_quality_gates
from agent_native_workflow.log import Logger


def test_run_gate_command_blocks_unsafe() -> None:
    ok, out = run_gate_command("echo hello && rm -rf /")
    assert ok is False
    assert "BLOCKED" in out


def test_run_gate_command_streams_lines_via_on_output() -> None:
    """on_output receives each line as it is produced, not as a single batch."""
    streamed: list[str] = []
    ok, full = run_gate_command(
        'echo "line1"; echo "line2"; echo "line3"',
        timeout=10,
        on_output=streamed.append,
    )
    assert ok is True
    assert streamed == ["line1", "line2", "line3"]
    assert "line1" in full
    assert "line3" in full


def test_run_gate_command_streams_on_failure() -> None:
    streamed: list[str] = []
    ok, full = run_gate_command(
        'echo "before"; exit 1',
        timeout=10,
        on_output=streamed.append,
    )
    assert ok is False
    assert "before" in streamed


def test_run_gate_sequential_streams_to_on_output() -> None:
    """Sequential gates should stream subprocess output line-by-line."""
    streamed: list[str] = []
    logger = Logger()
    ok, _, results = run_quality_gates(
        gates=[("echo-gate", 'echo "hello"; echo "world"')],
        callable_gates=[],
        use_parallel=False,
        timeout=10,
        logger=logger,
        on_output=streamed.append,
    )
    assert ok is True
    # Header + streamed lines
    assert "─── gate: echo-gate ───" in streamed
    assert "hello" in streamed
    assert "world" in streamed


def test_run_quality_gates_callable_fail_includes_digest() -> None:
    logger = Logger()

    def _fail() -> tuple[bool, str]:
        return False, "AssertionError\nexpected x\n" + ("noise\n" * 200)

    ok, fb, results = run_quality_gates(
        gates=[],
        callable_gates=[("unit", _fail)],
        use_parallel=False,
        timeout=30,
        logger=logger,
    )
    assert ok is False
    assert "callable:unit" in fb
    assert "AssertionError" in fb
    assert results[0].status.value == "fail"
