"""Textual-based pipeline dashboard (Phase 2).

TextualVisualizer runs PipelineApp on the main thread while the pipeline
executes in a worker thread.  Communication uses a thread-safe queue instead
of call_from_thread() to avoid deadlocks between the pipeline and the
Textual event loop.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from rich.markup import escape as _escape_markup

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics


# ── Widgets ───────────────────────────────────────────────────────────────────


class PipelineHeader(Static):
    """Top bar: provider, verification, iteration counter, elapsed timer."""

    iteration: reactive[int] = reactive(0)
    max_iterations: reactive[int] = reactive(1)
    final_status: reactive[str] = reactive("")

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
        base = (
            f"provider: [bold cyan]{self._provider}[/]   "
            f"verification: [bold]{self._verification}[/]   "
            f"iteration: [bold yellow]{self.iteration}/{self.max_iterations}[/]   "
            f"elapsed: [bold]{elapsed_str}[/]"
        )
        if self.final_status:
            return f"{base}   {self.final_status}"
        return base

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


_QUIT_COUNTDOWN = 5  # seconds to wait before force-quitting


class PipelineApp(App[None]):
    """Textual dashboard for the agent-native-workflow pipeline."""

    CSS_PATH = _CSS_PATH
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        config: WorkflowConfig,
        event_queue: queue.Queue[tuple[str, object]] | None = None,
        ready_event: threading.Event | None = None,
        pipeline_done: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._provider = config.cli_provider
        self._verification = getattr(config, "verification", "review")
        self._max_iterations = config.max_iterations
        self._current_iter = 0
        self._iter_phase_states: dict[str, str] = {}
        self._iter_start_time: float = time.time()
        self._event_queue = event_queue
        self._ready_event = ready_event
        self._pipeline_done = pipeline_done
        self._quit_countdown: int = 0  # 0 = not in countdown

    def on_mount(self) -> None:
        """Start draining the event queue and signal readiness."""
        if self._event_queue is not None:
            self.set_interval(0.05, self._drain_queue)
        if self._ready_event:
            self._ready_event.set()

    def action_quit(self) -> None:
        """Quit immediately if pipeline is done; otherwise start a countdown."""
        from agent_native_workflow.pipeline import _shutdown_event

        pipeline_done = self._pipeline_done is None or self._pipeline_done.is_set()
        if pipeline_done:
            self.exit()
            return
        if self._quit_countdown > 0:
            # Second q press during countdown → force quit immediately.
            _shutdown_event.set()
            self.exit()
            return
        # First q press while pipeline is running → signal shutdown + countdown.
        _shutdown_event.set()
        self._quit_countdown = _QUIT_COUNTDOWN
        self._update_quit_header()
        self.set_interval(1.0, self._countdown_tick, name="quit-countdown")

    def _countdown_tick(self) -> None:
        self._quit_countdown -= 1
        if self._quit_countdown <= 0:
            self.exit()
        else:
            self._update_quit_header()

    def _update_quit_header(self) -> None:
        header = self.query_one("#pipeline-header", PipelineHeader)
        header.final_status = (
            f"[bold yellow]⚠ Pipeline running — press q again to force quit "
            f"({self._quit_countdown}s)[/]"
        )

    def _drain_queue(self) -> None:
        """Process all pending events from the pipeline worker thread."""
        if self._event_queue is None:
            return
        for _ in range(200):
            try:
                kind, data = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "iteration_start":
                it, mx = data  # type: ignore[misc]
                self.update_iteration(it, mx)
            elif kind == "phase_start":
                self.update_phase(data, "running")  # type: ignore[arg-type]
            elif kind == "phase_end":
                phase, result = data  # type: ignore[misc]
                self.update_phase(phase, result)
            elif kind == "agent_stream":
                self.append_agent_stream(data)  # type: ignore[arg-type]
            elif kind == "log":
                self.append_log(data)  # type: ignore[arg-type]
            elif kind == "pipeline_end":
                self.show_summary(data)  # type: ignore[arg-type]
            elif kind == "exit":
                self.exit()

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
        self.query_one("#agent-stream", RichLog).write(_escape_markup(line))

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
            self.query_one("#pipeline-header", PipelineHeader).final_status = (
                f"[bold green]✓ CONVERGED[/]"
            )
            self.notify("Pipeline converged successfully!", severity="information")
        else:
            msg = (
                f"[bold yellow]⚠ MAX ITERATIONS[/] — "
                f"{metrics.total_iterations} iteration(s), "
                f"{metrics.total_duration_s:.1f}s"
            )
            self.query_one("#pipeline-header", PipelineHeader).final_status = (
                f"[bold yellow]⚠ MAX ITERATIONS[/]"
            )
            self.notify("Max iterations reached without convergence.", severity="warning")
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

    All ``on_*`` callbacks enqueue events into a thread-safe queue.  The Textual
    app drains the queue at 20 Hz via ``set_interval``, keeping the pipeline
    thread fully decoupled from the event loop.
    """

    def __init__(self) -> None:
        self._app: PipelineApp | None = None
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()

    def run_blocking(
        self,
        pipeline_fn: Callable[[], bool],
        config: WorkflowConfig,
    ) -> bool:
        """Run pipeline in a worker thread; Textual app blocks the main thread.

        Returns the bool result of ``pipeline_fn()``.
        """
        ready = threading.Event()
        pipeline_done = threading.Event()
        self._app = PipelineApp(
            config,
            event_queue=self._queue,
            ready_event=ready,
            pipeline_done=pipeline_done,
        )
        result: list[bool] = [False]

        def _worker() -> None:
            ready.wait(timeout=10)
            try:
                result[0] = pipeline_fn()
            except Exception as exc:
                self._queue.put(("log", f"[bold red]ERROR:[/] {exc}"))
            finally:
                pipeline_done.set()

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        self._app.run()
        worker.join(timeout=0)  # daemon thread; no need to block after TUI exits
        return result[0]

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        pass

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        self._queue.put(("iteration_start", (iteration, max_iterations)))

    def on_phase_start(self, phase: PipelinePhase) -> None:
        self._queue.put(("phase_start", phase))

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        self._queue.put(("phase_end", (phase, result)))

    def on_agent_stream(self, line: str) -> None:
        self._queue.put(("agent_stream", line))

    def on_log(self, message: str) -> None:
        self._queue.put(("log", message))

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        self._queue.put(("pipeline_end", metrics))
