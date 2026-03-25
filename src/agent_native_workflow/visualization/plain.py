from __future__ import annotations

from typing import TYPE_CHECKING

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics


class PlainVisualizer:
    """Minimal visualizer using plain print(). Used when --no-ui is set or rich is unavailable."""

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        print(f"[workflow] Starting pipeline — provider: {config.cli_provider}")

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        print(f"[workflow] === Iteration {iteration}/{max_iterations} ===")

    def on_phase_start(self, phase: PipelinePhase) -> None:
        print(f"[workflow] → {phase.value} started")

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        symbol = "✓" if result == "pass" else "✗"
        print(f"[workflow] {symbol} {phase.value}: {result.upper()}")

    def on_agent_stream(self, line: str) -> None:
        pass  # plain mode does not stream agent output

    def on_log(self, message: str) -> None:
        print(message)

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        status = "CONVERGED" if metrics.converged else "MAX ITERATIONS REACHED"
        print(
            f"[workflow] {status} — "
            f"{metrics.total_iterations} iteration(s), "
            f"{metrics.total_duration_s:.1f}s"
        )
