"""Textual-based pipeline dashboard (Phase 2).

TextualVisualizer runs PipelineApp in a daemon thread while the pipeline
executes synchronously.  All widget updates are posted via call_from_thread()
so they are safe to call from the pipeline's main thread.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics


# ── Widgets ───────────────────────────────────────────────────────────────────


class PipelineHeader(Static):
    """Top bar: provider, verification, iteration counter, elapsed timer."""

    iteration: reactive[int] = reactive(0)
    max_iterations: reactive[int] = reactive(1)

    def __init__(self, provider: str, verification: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._provider = provider
        self._verification = verification
        self._start_time = time.time()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> str:
        elapsed = int(time.time() - self._start_time)
        m, s = divmod(elapsed, 60)
        elapsed_str = f"{m}m {s}s" if m else f"{s}s"
        return (
            f"provider: [bold cyan]{self._provider}[/]   "
            f"verification: [bold]{self._verification}[/]   "
            f"iteration: [bold yellow]{self.iteration}/{self.max_iterations}[/]   "
            f"elapsed: [bold]{elapsed_str}[/]"
        )

    def update_iteration(self, iteration: int, max_iterations: int) -> None:
        self.iteration = iteration
        self.max_iterations = max_iterations


class FlowNode(Static):
    """A single node in the pipeline flow diagram."""

    status: reactive[str] = reactive("pending")

    _ICONS = {"running": "⟳", "pass": "✓", "fail": "✗", "pending": "·"}

    def __init__(self, label: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._label = label

    def render(self) -> str:
        icon = self._ICONS.get(self.status, "?")
        return f"{self._label}\n{icon} {self.status.upper()}"

    def watch_status(self, new_status: str) -> None:
        self.remove_class("pending", "running", "pass", "fail")
        self.add_class(new_status)


class FlowArrow(Static):
    """Decorative arrow between flow nodes."""

    def render(self) -> str:
        return "──▶"


# ── App ───────────────────────────────────────────────────────────────────────

_CSS_PATH = Path(__file__).with_suffix(".tcss")

_VERIFY_LABELS: dict[str, str] = {
    "review": "Agent R",
    "triangulation": "B + C",
    "none": "(skip)",
}

_PHASE_TO_NODE: dict[PipelinePhase, str] = {
    PipelinePhase.IMPLEMENT: "#node-a",
    PipelinePhase.QUALITY_GATES: "#node-gates",
    PipelinePhase.TRIANGULAR_VERIFY: "#node-verify",
}

_OUTCOME_SYMBOLS: dict[str, str] = {
    "pass": "[green]✓[/]",
    "gate_fail": "[red]✗[/]",
    "verify_fail": "[yellow]⚠[/]",
    "no_progress": "[dim]−[/]",
    "": "[dim]·[/]",
}


class PipelineApp(App[None]):
    """Textual dashboard for the agent-native-workflow pipeline."""

    CSS_PATH = _CSS_PATH
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: WorkflowConfig, ready_event: threading.Event | None = None) -> None:
        super().__init__()
        self._config = config
        self._provider = config.cli_provider
        self._verification = getattr(config, "verification", "review")
        self._max_iterations = config.max_iterations
        self._current_iter = 0
        self._iter_phase_states: dict[str, str] = {}  # tracks per-iter for history
        self._iter_start_time: float = time.time()
        self._ready_event = ready_event

    def on_mount(self) -> None:
        """Signal the pipeline worker that the Textual event loop is ready."""
        if self._ready_event:
            self._ready_event.set()

    def compose(self) -> ComposeResult:
        verify_label = _VERIFY_LABELS.get(self._verification, "Verify")
        yield Header(show_clock=True)
        yield PipelineHeader(
            self._provider,
            self._verification,
            id="pipeline-header",
        )
        with Horizontal(id="flow"):
            yield FlowNode("Agent A", id="node-a")
            yield FlowArrow()
            yield FlowNode("Gates", id="node-gates")
            yield FlowArrow()
            yield FlowNode(verify_label, id="node-verify")
        with Vertical():
            yield RichLog(id="iter-history", markup=True, highlight=False, auto_scroll=True)
            yield RichLog(id="agent-stream", markup=True, highlight=True, auto_scroll=True)
            yield RichLog(id="log-panel", markup=True, highlight=False, auto_scroll=True)
        yield Footer()

    # ── update methods (called via call_from_thread) ──────────────────────────

    def update_iteration(self, iteration: int, max_iterations: int) -> None:
        if self._current_iter > 0:
            self._record_iteration_history()
        self._current_iter = iteration
        self._iter_start_time = time.time()
        self._iter_phase_states = {}

        self.query_one("#pipeline-header", PipelineHeader).update_iteration(
            iteration, max_iterations
        )
        for node_id in ("#node-a", "#node-gates", "#node-verify"):
            self.query_one(node_id, FlowNode).status = "pending"
        self.query_one("#agent-stream", RichLog).clear()

    def update_phase(self, phase: PipelinePhase, status: str) -> None:
        node_id = _PHASE_TO_NODE.get(phase)
        if node_id:
            self.query_one(node_id, FlowNode).status = status
            self._iter_phase_states[node_id] = status

    def append_agent_stream(self, line: str) -> None:
        self.query_one("#agent-stream", RichLog).write(line)

    def append_log(self, message: str) -> None:
        self.query_one("#log-panel", RichLog).write(message)

    def show_summary(self, metrics: PipelineMetrics) -> None:
        self._record_iteration_history()
        if metrics.converged:
            msg = (
                f"[bold green]✓ CONVERGED[/] — "
                f"{metrics.total_iterations} iteration(s), "
                f"{metrics.total_duration_s:.1f}s"
            )
        else:
            msg = (
                f"[bold yellow]⚠ MAX ITERATIONS[/] — "
                f"{metrics.total_iterations} iteration(s), "
                f"{metrics.total_duration_s:.1f}s"
            )
        self.query_one("#log-panel", RichLog).write(msg)

    # ── private helpers ───────────────────────────────────────────────────────

    def _record_iteration_history(self) -> None:
        """Append a one-line summary of the completed iteration to iter-history."""
        duration = time.time() - self._iter_start_time
        a_sym = _sym(self._iter_phase_states.get("#node-a", "pending"))
        g_sym = _sym(self._iter_phase_states.get("#node-gates", "pending"))
        v_sym = _sym(self._iter_phase_states.get("#node-verify", "pending"))
        line = (
            f"iter {self._current_iter:2d}   "
            f"A {a_sym}  →  Gates {g_sym}  →  R {v_sym}   "
            f"[dim]{duration:.1f}s[/]"
        )
        self.query_one("#iter-history", RichLog).write(line)


def _sym(status: str) -> str:
    return {
        "pass": "[green]✓[/]",
        "fail": "[red]✗[/]",
        "running": "[yellow]⟳[/]",
        "pending": "[dim]·[/]",
    }.get(status, "[dim]?[/]")


# ── Visualizer bridge ─────────────────────────────────────────────────────────


class TextualVisualizer:
    """Visualizer that drives PipelineApp on the main thread.

    Normal usage via ``cmd_run``:
        1. ``make_visualizer("textual")`` creates the visualizer.
        2. ``cmd_run`` detects ``TextualVisualizer`` and calls
           ``run_blocking(pipeline_fn, config)``.
        3. ``run_blocking`` pre-creates the app, starts the pipeline in a worker thread,
           then calls ``app.run()`` which blocks the main thread (Textual requirement).

    All ``on_*`` callbacks from the pipeline worker thread use ``call_from_thread``
    to safely post widget updates to the Textual event loop.
    """

    def __init__(self) -> None:
        self._app: PipelineApp | None = None

    def run_blocking(
        self,
        pipeline_fn: Callable[[], bool],
        config: WorkflowConfig,
    ) -> bool:
        """Run pipeline in a worker thread; Textual app blocks the main thread.

        Returns the bool result of ``pipeline_fn()``.
        """
        ready = threading.Event()
        self._app = PipelineApp(config, ready_event=ready)
        result: list[bool] = [False]

        def _worker() -> None:
            # Wait until Textual's event loop is running before calling call_from_thread
            ready.wait(timeout=10)
            try:
                result[0] = pipeline_fn()
            except Exception as exc:
                if self._app:
                    self._app.call_from_thread(
                        self._app.append_log, f"[bold red]ERROR:[/] {exc}"
                    )
            finally:
                time.sleep(1.0)
                if self._app:
                    self._app.call_from_thread(self._app.exit)

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        self._app.run()
        worker.join(timeout=10)
        return result[0]

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        # App is pre-created by run_blocking(); nothing to do here.
        # (If called outside run_blocking, app stays None and all on_* become no-ops.)
        pass

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        if self._app:
            self._app.call_from_thread(self._app.update_iteration, iteration, max_iterations)

    def on_phase_start(self, phase: PipelinePhase) -> None:
        if self._app:
            self._app.call_from_thread(self._app.update_phase, phase, "running")

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        if self._app:
            self._app.call_from_thread(self._app.update_phase, phase, result)

    def on_agent_stream(self, line: str) -> None:
        if self._app:
            self._app.call_from_thread(self._app.append_agent_stream, line)

    def on_log(self, message: str) -> None:
        if self._app:
            self._app.call_from_thread(self._app.append_log, message)

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        if self._app:
            self._app.call_from_thread(self._app.show_summary, metrics)
            time.sleep(1)
            self._app.call_from_thread(self._app.exit)
