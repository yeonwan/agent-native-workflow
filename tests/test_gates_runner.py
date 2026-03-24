"""Quality gate runner (subprocess + digest on failure)."""

from __future__ import annotations

from agent_native_workflow.gates.runner import run_gate_command, run_quality_gates
from agent_native_workflow.log import Logger


def test_run_gate_command_blocks_unsafe() -> None:
    ok, out = run_gate_command("echo hello && rm -rf /")
    assert ok is False
    assert "BLOCKED" in out


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
