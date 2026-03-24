from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


_PHASE_LABELS = {
    PipelinePhase.IMPLEMENT: "Agent A  Implement",
    PipelinePhase.QUALITY_GATES: "Gates    Lint/Test",
    PipelinePhase.TRIANGULAR_VERIFY: "Verify",
    PipelinePhase.COMPLETE: "Done",
    PipelinePhase.FAILED: "Failed",
}

_VERIFICATION_LABELS = {
    "review": "Agent R  Review",
    "triangulation": "B+C      Verify",
    "none": "         (skip)",
}

_STATUS_STYLE = {
    "running": "[bold yellow]⟳ RUNNING[/]",
    "pass": "[bold green]✓ PASS[/]",
    "fail": "[bold red]✗ FAIL[/]",
    "pending": "[dim]· pending[/]",
}

_MAX_LOG_LINES = 20


class RichVisualizer:
    """Real-time TUI using rich.live.Live.

    Layout:
    ┌─ Header ──────────────────────────────────────────────┐
    │  provider: copilot   iteration: 2/5   elapsed: 12.3s  │
    ├─ Phase Status ─────────────────────────────────────────┤
    │  [Agent A: PASS]  [Gates: RUNNING]  [B+C: pending]    │
    ├─ Log ──────────────────────────────────────────────────┤
    │  [12:34:01] Phase 2 quality gates started              │
    │  ...                                                   │
    └────────────────────────────────────────────────────────┘
    """

    def __init__(self) -> None:
        if not _RICH_AVAILABLE:
            raise ImportError("rich is required for RichVisualizer. Install it: pip install rich")
        self._console = Console()
        self._live: Live | None = None
        self._start_time = time.time()
        self._provider = "unknown"
        self._verification = "review"
        self._iteration = 0
        self._max_iterations = 1
        self._phase_states: dict[PipelinePhase, str] = {
            PipelinePhase.IMPLEMENT: "pending",
            PipelinePhase.QUALITY_GATES: "pending",
            PipelinePhase.TRIANGULAR_VERIFY: "pending",
        }
        self._log_lines: deque[str] = deque(maxlen=_MAX_LOG_LINES)

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        self._provider = config.cli_provider
        self._max_iterations = config.max_iterations
        self._verification = getattr(config, "verification", "review")
        self._start_time = time.time()
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            screen=False,
        )
        self._live.__enter__()

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        self._iteration = iteration
        self._max_iterations = max_iterations
        # Reset phase states for new iteration
        for phase in (
            PipelinePhase.IMPLEMENT,
            PipelinePhase.QUALITY_GATES,
            PipelinePhase.TRIANGULAR_VERIFY,
        ):
            self._phase_states[phase] = "pending"
        self._refresh()

    def on_phase_start(self, phase: PipelinePhase) -> None:
        if phase in self._phase_states:
            self._phase_states[phase] = "running"
        self._refresh()

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        if phase in self._phase_states:
            self._phase_states[phase] = result
        self._refresh()

    def on_log(self, message: str) -> None:
        self._log_lines.append(message)
        self._refresh()

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        if self._live:
            self._live.__exit__(None, None, None)
            self._live = None

        if metrics.converged:
            self._console.print(
                f"\n[bold green]✓ CONVERGED[/] — "
                f"{metrics.total_iterations} iteration(s), "
                f"{metrics.total_duration_s:.1f}s"
            )
        else:
            self._console.print(
                f"\n[bold yellow]⚠ MAX ITERATIONS REACHED[/] — "
                f"{metrics.total_iterations} iteration(s), "
                f"{metrics.total_duration_s:.1f}s"
            )

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Panel:
        elapsed = time.time() - self._start_time

        # Header row
        header = Text(
            f"  provider: {self._provider}   "
            f"iteration: {self._iteration}/{self._max_iterations}   "
            f"elapsed: {elapsed:.1f}s",
            style="bold",
        )

        # Phase status grid
        verify_label = _VERIFICATION_LABELS.get(self._verification, "Verify")
        phase_labels = {**_PHASE_LABELS, PipelinePhase.TRIANGULAR_VERIFY: verify_label}
        table = Table.grid(padding=(0, 2))
        table.add_row(
            *[
                Text.from_markup(f"{phase_labels[p]}  {_STATUS_STYLE.get(s, s)}")
                for p, s in self._phase_states.items()
            ]
        )

        # Log panel
        log_text = "\n".join(self._log_lines) if self._log_lines else "(no logs yet)"

        layout = Layout()
        layout.split_column(
            Layout(Panel(header, title="[bold]agent-native-workflow[/]"), size=3),
            Layout(Panel(table, title="Phase Status"), size=4),
            Layout(Panel(log_text, title="Log"), minimum_size=4),
        )

        return Panel(layout, border_style="blue")
