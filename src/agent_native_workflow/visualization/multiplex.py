from __future__ import annotations

from typing import TYPE_CHECKING

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics


class MultiplexVisualizer:
    """Fans out every Visualizer event to multiple children.

    Use this to run e.g. RichVisualizer and WebSocketVisualizer in parallel:

        visualizer = MultiplexVisualizer(RichVisualizer(), WebSocketVisualizer())
    """

    def __init__(self, *children: object) -> None:
        self._children = children

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        for c in self._children:
            c.on_pipeline_start(config)  # type: ignore[union-attr]

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        for c in self._children:
            c.on_iteration_start(iteration, max_iterations)  # type: ignore[union-attr]

    def on_phase_start(self, phase: PipelinePhase) -> None:
        for c in self._children:
            c.on_phase_start(phase)  # type: ignore[union-attr]

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        for c in self._children:
            c.on_phase_end(phase, result)  # type: ignore[union-attr]

    def on_agent_stream(self, line: str) -> None:
        for c in self._children:
            c.on_agent_stream(line)  # type: ignore[union-attr]

    def on_log(self, message: str) -> None:
        for c in self._children:
            c.on_log(message)  # type: ignore[union-attr]

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        for c in self._children:
            c.on_pipeline_end(metrics)  # type: ignore[union-attr]
