"""Tests for MultiplexVisualizer — complete event fan-out and exception behaviour.

Documents the current (no-isolation) contract: if a child raises, the
exception propagates and subsequent children are NOT called.  This makes the
behaviour explicit so any future change to add error-isolation is visible.
"""

from __future__ import annotations

import pytest

from agent_native_workflow.visualization.base import PipelinePhase
from agent_native_workflow.visualization.multiplex import MultiplexVisualizer


# ── recording helpers ─────────────────────────────────────────────────────────


class _Recorder:
    """Minimal visualizer that records every event it receives."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def on_pipeline_start(self, config: object) -> None:
        self.events.append(("pipeline_start", config))

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        self.events.append(("iteration_start", (iteration, max_iterations)))

    def on_phase_start(self, phase: PipelinePhase) -> None:
        self.events.append(("phase_start", phase))

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        self.events.append(("phase_end", (phase, result)))

    def on_agent_stream(self, line: str) -> None:
        self.events.append(("agent_stream", line))

    def on_log(self, message: str) -> None:
        self.events.append(("log", message))

    def on_pipeline_end(self, metrics: object) -> None:
        self.events.append(("pipeline_end", metrics))


class _Raiser:
    """Visualizer that raises on every call — used to test exception propagation."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def on_pipeline_start(self, config: object) -> None:
        raise self._exc

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        raise self._exc

    def on_phase_start(self, phase: PipelinePhase) -> None:
        raise self._exc

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        raise self._exc

    def on_agent_stream(self, line: str) -> None:
        raise self._exc

    def on_log(self, message: str) -> None:
        raise self._exc

    def on_pipeline_end(self, metrics: object) -> None:
        raise self._exc


# ── full fan-out: all event types ─────────────────────────────────────────────


def test_multiplex_fans_out_pipeline_start() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_pipeline_start("cfg")
    assert a.events == [("pipeline_start", "cfg")]
    assert b.events == a.events


def test_multiplex_fans_out_iteration_start() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_iteration_start(2, 5)
    assert a.events == [("iteration_start", (2, 5))]
    assert b.events == a.events


def test_multiplex_fans_out_phase_start() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_phase_start(PipelinePhase.IMPLEMENT)
    assert a.events == [("phase_start", PipelinePhase.IMPLEMENT)]
    assert b.events == a.events


def test_multiplex_fans_out_phase_end() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_phase_end(PipelinePhase.QUALITY_GATES, "pass")
    assert a.events == [("phase_end", (PipelinePhase.QUALITY_GATES, "pass"))]
    assert b.events == a.events


def test_multiplex_fans_out_agent_stream() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_agent_stream("streaming line")
    assert a.events == [("agent_stream", "streaming line")]
    assert b.events == a.events


def test_multiplex_fans_out_log() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_log("log message")
    assert a.events == [("log", "log message")]
    assert b.events == a.events


def test_multiplex_fans_out_pipeline_end() -> None:
    a, b = _Recorder(), _Recorder()
    MultiplexVisualizer(a, b).on_pipeline_end("metrics")
    assert a.events == [("pipeline_end", "metrics")]
    assert b.events == a.events


def test_multiplex_works_with_zero_children() -> None:
    mx = MultiplexVisualizer()
    # No exception, no crash
    mx.on_log("msg")
    mx.on_agent_stream("line")
    mx.on_phase_start(PipelinePhase.IMPLEMENT)


def test_multiplex_works_with_single_child() -> None:
    a = _Recorder()
    MultiplexVisualizer(a).on_log("only child")
    assert a.events == [("log", "only child")]


def test_multiplex_all_events_sequence() -> None:
    """All event types dispatched in pipeline order reach both recorders."""
    a, b = _Recorder(), _Recorder()
    mx = MultiplexVisualizer(a, b)
    mx.on_pipeline_start("cfg")
    mx.on_iteration_start(1, 3)
    mx.on_phase_start(PipelinePhase.IMPLEMENT)
    mx.on_agent_stream("line1")
    mx.on_phase_end(PipelinePhase.IMPLEMENT, "pass")
    mx.on_log("done")
    mx.on_pipeline_end("metrics")
    assert len(a.events) == 7
    assert a.events == b.events


# ── exception behaviour (no isolation) ───────────────────────────────────────


def test_exception_in_first_child_propagates() -> None:
    """Current behaviour: exception from first child propagates out of multiplex."""
    err = ValueError("child failed")
    raiser = _Raiser(err)
    recorder = _Recorder()
    mx = MultiplexVisualizer(raiser, recorder)
    with pytest.raises(ValueError, match="child failed"):
        mx.on_log("msg")


def test_exception_in_first_child_prevents_second_child_from_being_called() -> None:
    """Current behaviour: second child is NOT called when first raises.

    NOTE: This test documents the *current* no-isolation contract.
    If isolation is added in the future this test should be updated to
    assert recorder.events == [("log", "msg")].
    """
    err = RuntimeError("oops")
    raiser = _Raiser(err)
    recorder = _Recorder()
    mx = MultiplexVisualizer(raiser, recorder)
    with pytest.raises(RuntimeError):
        mx.on_log("msg")
    # Second child was NOT reached because the first raised
    assert recorder.events == []
