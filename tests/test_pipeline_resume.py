"""ENHANCE Phase E: RunResult / protocol checks and pipeline session behavior."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_native_workflow.config import WorkflowConfig
from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import REVIEW_APPROVE_MARKER
from agent_native_workflow.pipeline import run_pipeline
from agent_native_workflow.runners.base import AgentRunner, RunResult
from agent_native_workflow.store import RunStore


def test_run_result_is_frozen() -> None:
    r = RunResult("out", "sid")
    with pytest.raises(FrozenInstanceError):
        r.output = "x"  # type: ignore[misc]


def test_minimal_class_satisfies_agent_runner_protocol() -> None:
    class Minimal:
        @property
        def provider_name(self) -> str:
            return "minimal"

        @property
        def supports_file_tools(self) -> bool:
            return True

        @property
        def supports_resume(self) -> bool:
            return False

        def run(
            self,
            prompt: str,
            *,
            session_id: str | None = None,
            timeout: int = 300,
            max_retries: int = 2,
            logger=None,
            on_output=None,
        ) -> RunResult:
            return RunResult(output=prompt[:10], session_id=None)

    m = Minimal()
    assert isinstance(m, AgentRunner)


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
    """Verify / triangulation runners when mode does not invoke them."""

    provider_name = "dead"
    supports_file_tools = True
    supports_resume = False

    def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("DeadRunner.run should not be called in this test")


class _ResumeAgentA:
    provider_name = "resume-mock"
    supports_file_tools = True
    supports_resume = True

    def __init__(self) -> None:
        self.session_ids: list[str | None] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        self.session_ids.append(session_id)
        return RunResult("ok\nLOOP_COMPLETE\n", session_id="agent-sess-1")


class _NoResumeAgentA:
    provider_name = "noresum-mock"
    supports_file_tools = True
    supports_resume = False

    def __init__(self) -> None:
        self.session_ids: list[str | None] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        self.session_ids.append(session_id)
        return RunResult("ok\nLOOP_COMPLETE\n", session_id=None)


def test_pipeline_carries_agent_a_session_when_resume_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=[])

    # Mock file-change detection so no-progress logic doesn't interfere
    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr(
        "agent_native_workflow.pipeline.files_changed_since", lambda _: ["src/x.py"]
    )

    attempts = {"n": 0}

    def flaky_gate() -> tuple[bool, str]:
        attempts["n"] += 1
        return (attempts["n"] >= 2, "" if attempts["n"] >= 2 else "fail")

    agent = _ResumeAgentA()
    wcfg = WorkflowConfig()
    wcfg.verification = "none"

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=4,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        custom_gates=[("g", flaky_gate)],
        runner=agent,
        verify_runner=_DeadRunner(),
        review_runner=_DeadRunner(),
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )
    assert ok is True
    assert attempts["n"] == 2
    assert agent.session_ids == [None, "agent-sess-1"]

    state = json.loads((store.run_dir / "session-state.json").read_text(encoding="utf-8"))
    assert state["agent_a"] == "agent-sess-1"
    assert state["agent_r"] is None


def test_pipeline_non_resume_runner_always_gets_none_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=[])

    # Mock file-change detection so no-progress logic doesn't interfere
    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr(
        "agent_native_workflow.pipeline.files_changed_since", lambda _: ["src/x.py"]
    )

    attempts = {"n": 0}

    def flaky_gate() -> tuple[bool, str]:
        attempts["n"] += 1
        return (attempts["n"] >= 2, "" if attempts["n"] >= 2 else "fail")

    agent = _NoResumeAgentA()
    wcfg = WorkflowConfig()
    wcfg.verification = "none"

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=4,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        custom_gates=[("g", flaky_gate)],
        runner=agent,
        verify_runner=_DeadRunner(),
        review_runner=_DeadRunner(),
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )
    assert ok is True
    assert agent.session_ids == [None, None]


class _ReviewRunner:
    provider_name = "review-mock"
    supports_file_tools = True
    supports_resume = True

    def __init__(self) -> None:
        self.verification_sessions: list[str | None] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
    ) -> RunResult:
        self.verification_sessions.append(session_id)
        return RunResult(f"report\n{REVIEW_APPROVE_MARKER}\n", session_id="r-sess-1")


def test_pipeline_review_mode_persists_agent_r_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=["src/x.py"])

    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr(
        "agent_native_workflow.pipeline.files_changed_since", lambda _: ["src/x.py"]
    )

    agent = _ResumeAgentA()
    review = _ReviewRunner()
    wcfg = WorkflowConfig()
    wcfg.verification = "review"

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=2,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        runner=agent,
        verify_runner=review,
        review_runner=review,
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )
    assert ok is True
    assert review.verification_sessions == [None]

    state = json.loads((store.run_dir / "session-state.json").read_text(encoding="utf-8"))
    assert state["agent_r"] == "r-sess-1"
    assert state["agent_a"] == "agent-sess-1"


class _FlakyReview:
    provider_name = "review-flaky"
    supports_file_tools = True
    supports_resume = True

    def __init__(self) -> None:
        self._n = 0
        self.verification_sessions: list[str | None] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
    ) -> RunResult:
        self.verification_sessions.append(session_id)
        self._n += 1
        if self._n == 1:
            return RunResult("needs work\n", session_id="r-stable")
        return RunResult(f"ok\n{REVIEW_APPROVE_MARKER}\n", session_id="r-stable")


def test_pipeline_review_resumes_verification_session_on_second_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompt, req = _write_prompt_and_req(tmp_path)
    store = RunStore(base_dir=tmp_path / "meta")
    cfg = ProjectConfig(lint_cmd="", test_cmd="", changed_files=["src/x.py"])

    monkeypatch.setattr("agent_native_workflow.pipeline.snapshot_working_tree", lambda: {})
    monkeypatch.setattr(
        "agent_native_workflow.pipeline.files_changed_since", lambda _: ["src/x.py"]
    )

    agent = _ResumeAgentA()
    review = _FlakyReview()
    wcfg = WorkflowConfig()
    wcfg.verification = "review"

    ok = run_pipeline(
        prompt_file=prompt,
        requirements_file=req,
        store=store,
        max_iterations=4,
        agent_timeout=30,
        max_retries=1,
        config=cfg,
        runner=agent,
        verify_runner=review,
        review_runner=review,
        c_runner=_DeadRunner(),
        workflow_config=wcfg,
        parallel_gates=False,
    )
    assert ok is True
    assert review.verification_sessions == [None, "r-stable"]
