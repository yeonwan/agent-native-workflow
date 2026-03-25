"""Tests for pipeline no_progress handling.

Covers:
- consecutive_no_change == 1: feedback written, session dropped, iteration continues
- consecutive_no_change >= 2: pipeline aborts
- Counter resets to 0 when Agent A makes changes after a miss
- Session not dropped when runner.supports_resume is False
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_native_workflow.config import WorkflowConfig
from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import REVIEW_APPROVE_MARKER
from agent_native_workflow.pipeline import run_pipeline
from agent_native_workflow.runners.base import RunResult
from agent_native_workflow.store import RunStore

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_prompt_and_req(root: Path) -> tuple[Path, Path]:
    req = root / "requirements.md"
    req.write_text("# R\n\nDo thing.\n", encoding="utf-8")
    prompt = root / "PROMPT.yaml"
    prompt.write_text(
        'title: "T"\nbuild: |\n  Implement.\ncriteria:\n  - done\n',
        encoding="utf-8",
    )
    return prompt, req


class _DeadRunner:
    provider_name = "dead"
    supports_file_tools = True
    supports_resume = False

    def run(self, *args: object, **kwargs: object) -> RunResult:  # type: ignore[return]
        raise AssertionError("_DeadRunner.run must not be called")


def _make_runner(
    *,
    supports_resume: bool = True,
    outputs: list[str] | None = None,
    session_id_out: str | None = "sess-1",
) -> "_TrackingRunner":
    """Build a configurable mock runner."""

    class _TrackingRunner:
        provider_name = "mock"
        supports_file_tools = True

        def __init__(self) -> None:
            self.supports_resume = supports_resume
            self._outputs = list(outputs or [f"ok\nLOOP_COMPLETE\n"])
            self._idx = 0
            self.session_ids_received: list[str | None] = []

        def run(
            self,
            prompt: str,
            *,
            session_id: str | None = None,
            timeout: int = 300,
            max_retries: int = 2,
            logger: object = None,
            on_output: object = None,
        ) -> RunResult:
            self.session_ids_received.append(session_id)
            out = self._outputs[min(self._idx, len(self._outputs) - 1)]
            self._idx += 1
            return RunResult(out, session_id=session_id_out)

    return _TrackingRunner()


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: object,
    *,
    max_iterations: int = 5,
    change_sequence: list[list[str]],
) -> tuple[bool, RunStore]:
    """Helper: run the pipeline with a controlled per-iteration change sequence."""
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=[])
    wcfg = WorkflowConfig()
    wcfg.verification = "none"

    call_count = [0]

    def _files_changed(_before: dict) -> list[str]:
        idx = call_count[0]
        call_count[0] += 1
        return change_sequence[idx] if idx < len(change_sequence) else []

    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr("agent_native_workflow.pipeline.files_changed_since", _files_changed)

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=max_iterations,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        custom_gates=[("g", lambda: (True, ""))],
        runner=runner,
        verify_runner=_DeadRunner(),
        review_runner=_DeadRunner(),
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )
    return ok, store


# ── first no-change: continue ─────────────────────────────────────────────────


def test_first_no_change_writes_feedback_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """consecutive_no_change == 1: feedback written, pipeline continues to next iter."""
    runner = _make_runner()
    # iter 1: no changes; iter 2: changes (gates pass → converged)
    ok, store = _run(tmp_path, monkeypatch, runner, change_sequence=[[], ["src/x.py"]])
    assert ok is True
    feedback = (store.run_dir / "iter-001" / "feedback.md").read_text()
    assert "no file changes" in feedback.lower()


def test_first_no_change_session_dropped_for_resume_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After first no-change, a resume-capable runner's session is set to None."""
    runner = _make_runner(supports_resume=True)
    _run(tmp_path, monkeypatch, runner, change_sequence=[[], ["src/x.py"]])
    # iter 1: no change → session passed to iter 2 must be None (dropped)
    assert runner.session_ids_received[0] is None   # iter 1: first run, no session yet
    assert runner.session_ids_received[1] is None   # iter 2: session was dropped


def test_first_no_change_session_not_dropped_when_resume_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-resume runner: no session to drop — must not crash."""
    runner = _make_runner(supports_resume=False, session_id_out=None)
    ok, _ = _run(tmp_path, monkeypatch, runner, change_sequence=[[], ["src/x.py"]])
    assert ok is True  # pipeline ran to completion without crash


# ── second consecutive no-change: abort ──────────────────────────────────────


def test_two_consecutive_no_changes_abort_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """consecutive_no_change >= 2: pipeline aborts and returns False."""
    runner = _make_runner()
    ok, _ = _run(tmp_path, monkeypatch, runner, change_sequence=[[], []])
    assert ok is False


def test_two_consecutive_no_changes_stops_before_max_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipeline aborts after iter 2, not iter 5."""
    runner = _make_runner()
    _run(tmp_path, monkeypatch, runner, max_iterations=5, change_sequence=[[], []])
    # runner should only have been called twice
    assert len(runner.session_ids_received) == 2


# ── counter reset after a miss ────────────────────────────────────────────────


def test_counter_resets_when_changes_detected_after_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """miss → hit(gate fail) → miss → hit(gate pass) pattern.

    After iter 1 misses, iter 2 hits but gate fails → counter resets to 0.
    Iter 3 misses again → counter becomes 1 (not 2), so pipeline continues.
    Iter 4 hits and gate passes → converged.
    """
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=[])
    wcfg = WorkflowConfig()
    wcfg.verification = "none"

    change_seq = [[], ["src/x.py"], [], ["src/x.py"]]
    call_count = [0]

    def _files_changed(_before: dict) -> list[str]:
        idx = call_count[0]
        call_count[0] += 1
        return change_seq[idx] if idx < len(change_seq) else []

    gate_call = [0]

    def _flaky_gate() -> tuple[bool, str]:
        gate_call[0] += 1
        # Only pass on the second gate call (iter 4)
        return (gate_call[0] >= 2, "" if gate_call[0] >= 2 else "fail")

    runner = _make_runner()
    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr("agent_native_workflow.pipeline.files_changed_since", _files_changed)

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=5,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        custom_gates=[("g", _flaky_gate)],
        runner=runner,
        verify_runner=_DeadRunner(),
        review_runner=_DeadRunner(),
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )

    assert ok is True
    assert len(runner.session_ids_received) == 4


# ── verification not reached on no-progress iter ─────────────────────────────


def test_verify_runner_not_called_on_no_progress_iter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify/review runners must not be invoked if phase 1 produced no changes."""
    spy_called = [False]

    class _SpyRunner:
        provider_name = "spy"
        supports_file_tools = False
        supports_resume = False

        def run(self, *args: object, **kwargs: object) -> RunResult:
            spy_called[0] = True
            return RunResult(REVIEW_APPROVE_MARKER, session_id=None)

    spy = _SpyRunner()
    runner = _make_runner()
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=[])
    wcfg = WorkflowConfig()
    wcfg.verification = "review"  # review mode, spy would be called if reached

    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr(
        "agent_native_workflow.pipeline.files_changed_since",
        lambda _: [],  # always no change
    )

    run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=2,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        custom_gates=[("g", lambda: (True, ""))],
        runner=runner,
        verify_runner=_DeadRunner(),
        review_runner=spy,
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )

    assert spy_called[0] is False
