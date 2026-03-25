"""Tests for Phase 1 streaming: on_output callback + MultiplexVisualizer."""

from __future__ import annotations

from unittest.mock import patch

from agent_native_workflow.runners.copilot import GitHubCopilotRunner
from agent_native_workflow.visualization.base import PipelinePhase
from agent_native_workflow.visualization.multiplex import MultiplexVisualizer
from agent_native_workflow.visualization.plain import PlainVisualizer

# ── on_output streaming ───────────────────────────────────────────────────────


class _FakeStderr:
    def read(self) -> str:
        return ""


def _make_streaming_popen(lines: list[str], captured: list[list[str]] | None = None):
    """Fake Popen whose stdout yields the given lines."""

    class _FakePopen:
        def __init__(self, cmd: list[str], **_kwargs: object) -> None:
            if captured is not None:
                captured.append(cmd)
            self.returncode = 0
            self.stdout = iter(f"{line}\n" for line in lines)
            self.stderr = _FakeStderr()

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    return _FakePopen


def test_copilot_runner_calls_on_output() -> None:
    received: list[str] = []
    FakePopen = _make_streaming_popen(["line one", "line two", "line three"])

    with patch("agent_native_workflow.runners.copilot.subprocess.Popen", FakePopen):
        runner = GitHubCopilotRunner()
        runner.run("prompt", timeout=10, max_retries=1, on_output=received.append)

    assert received == ["line one", "line two", "line three"]


def test_copilot_runner_without_on_output_still_works() -> None:
    FakePopen = _make_streaming_popen(["some output"])
    with patch("agent_native_workflow.runners.copilot.subprocess.Popen", FakePopen):
        runner = GitHubCopilotRunner()
        result = runner.run("prompt", timeout=10, max_retries=1)
    assert "some output" in result.output


def test_copilot_runner_output_joined_as_full_text() -> None:
    FakePopen = _make_streaming_popen(["alpha", "beta", "gamma"])
    with patch("agent_native_workflow.runners.copilot.subprocess.Popen", FakePopen):
        result = GitHubCopilotRunner().run("prompt", timeout=10, max_retries=1)
    assert result.output == "alpha\nbeta\ngamma"


# ── MultiplexVisualizer ───────────────────────────────────────────────────────


def _make_recording_visualizer():
    """PlainVisualizer subclass that records on_agent_stream calls."""

    class _Recorder(PlainVisualizer):
        def __init__(self) -> None:
            self.streamed: list[str] = []
            self.logged: list[str] = []

        def on_agent_stream(self, line: str) -> None:
            self.streamed.append(line)

        def on_log(self, message: str) -> None:
            self.logged.append(message)

    return _Recorder()


def test_multiplex_fans_out_all_events() -> None:
    a = _make_recording_visualizer()
    b = _make_recording_visualizer()
    mx = MultiplexVisualizer(a, b)

    mx.on_agent_stream("hello from agent")
    mx.on_log("pipeline log message")

    assert a.streamed == ["hello from agent"]
    assert b.streamed == ["hello from agent"]
    assert a.logged == ["pipeline log message"]
    assert b.logged == ["pipeline log message"]


def test_multiplex_fans_out_phase_events() -> None:
    events_a: list[str] = []
    events_b: list[str] = []

    class _PhaseRecorder(PlainVisualizer):
        def __init__(self, log: list[str]) -> None:
            self._log = log

        def on_phase_start(self, phase: PipelinePhase) -> None:
            self._log.append(f"start:{phase.value}")

        def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
            self._log.append(f"end:{phase.value}:{result}")

        def on_agent_stream(self, line: str) -> None:
            pass

    mx = MultiplexVisualizer(_PhaseRecorder(events_a), _PhaseRecorder(events_b))
    mx.on_phase_start(PipelinePhase.IMPLEMENT)
    mx.on_phase_end(PipelinePhase.IMPLEMENT, "pass")

    assert events_a == ["start:phase1_implement", "end:phase1_implement:pass"]
    assert events_b == events_a
