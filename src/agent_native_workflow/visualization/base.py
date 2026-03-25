from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics


class PipelinePhase(enum.Enum):
    IMPLEMENT = "phase1_implement"
    QUALITY_GATES = "phase2_quality_gates"
    TRIANGULAR_VERIFY = "phase3_triangular_verify"
    COMPLETE = "complete"
    FAILED = "failed"


class Visualizer(Protocol):
    """Observer interface for pipeline progress visualization."""

    def on_pipeline_start(self, config: WorkflowConfig) -> None: ...
    def on_iteration_start(self, iteration: int, max_iterations: int) -> None: ...
    def on_phase_start(self, phase: PipelinePhase) -> None: ...
    def on_phase_end(self, phase: PipelinePhase, result: str) -> None: ...
    def on_agent_stream(self, line: str) -> None: ...
    def on_log(self, message: str) -> None: ...
    def on_pipeline_end(self, metrics: PipelineMetrics) -> None: ...
