"""RunStore session state and resume-aware Agent A context (ENHANCE Phase B)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_native_workflow.domain import GateResult, GateStatus, IterationOutcome
from agent_native_workflow.store import RunStore


def test_write_and_load_session_state(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    store.write_session_state({"agent_a": "abc-uuid", "agent_r": None})
    loaded = store.load_session_state()
    assert loaded["agent_a"] == "abc-uuid"
    assert loaded["agent_r"] is None


def test_load_session_state_empty_when_missing(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    assert store.load_session_state() == {}


def test_build_agent_a_context_full_history(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    store.set_agent_session_resume(False)

    store.write_gate_results(
        1,
        [GateResult(name="lint", status=GateStatus.FAIL, output="e1")],
    )
    store.write_feedback(1, "fix lint", outcome=IterationOutcome.GATE_FAIL)

    store.iter_dir(2)
    reqs = tmp_path / "PROMPT.yaml"
    reqs.write_text("x")
    text = store.build_agent_a_context(2, reqs)
    assert "Previous Iterations Summary" in text
    assert "### Iteration 1" in text
    assert "fix lint" in text
    assert "iteration 2" in text.lower()


def test_build_agent_a_context_resume_uses_latest_only(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    store.set_agent_session_resume(True)

    store.write_gate_results(1, [GateResult(name="test", status=GateStatus.PASS, output="")])
    store.write_feedback(1, "verify-round-1-only", outcome=IterationOutcome.VERIFY_FAIL)

    store.write_gate_results(
        2,
        [GateResult(name="lint", status=GateStatus.FAIL, output="fail2")],
    )
    store.write_feedback(2, "fix lint now", outcome=IterationOutcome.GATE_FAIL)

    reqs = tmp_path / "req.md"
    reqs.write_text("r")
    text = store.build_agent_a_context(3, reqs)
    assert "same CLI session" in text
    assert "### Iteration 2" in text
    assert "fix lint now" in text
    assert "### Iteration 1" not in text
    assert "verify-round-1-only" not in text


def test_build_agent_a_context_iteration_must_be_ge_2(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    with pytest.raises(ValueError, match="iteration >= 2"):
        store.build_agent_a_context(1, Path("p.md"))
