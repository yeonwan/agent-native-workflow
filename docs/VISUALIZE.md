# Pipeline Visualization Overhaul

> Replace the current Rich Live TUI with a Textual-based dashboard, add agent
> output streaming, and lay groundwork for a web dashboard via WebSocket.

---

## Why

The current `RichVisualizer` has a fundamental problem: `subprocess.run(capture_output=True)`
blocks for the entire duration of Agent A/R execution. During that time (often minutes), the
TUI shows "RUNNING" with zero log activity. Users think the pipeline is frozen.

The fix requires two things that Rich Live cannot do well:
1. **Streaming agent output** into the UI while the subprocess runs
2. **Interactive widgets** (scrollable logs, clickable iteration history, resizable panels)

Textual (by the same team that built Rich) solves both.

---

## Architecture Overview

```
pipeline.py
    │
    ├── Logger ──→ on_log callback
    │
    └── Visualizer (Protocol)
            │
            ├── PlainVisualizer        (existing, unchanged)
            ├── RichVisualizer         (existing, deprecated but kept)
            ├── TextualVisualizer      (Phase 2 — new TUI)
            └── WebSocketVisualizer    (Phase 3 — new web layer)

MultiplexVisualizer(TextualVisualizer(), WebSocketVisualizer())
    └── fans out every event to all children
```

The `Visualizer` Protocol is the event bus. No new event system needed.

---

## Phase 1 — Foundation: Streaming + Protocol Extension

### 1a. Add `on_agent_stream` to `Visualizer` Protocol

**File:** `src/agent_native_workflow/visualization/base.py`

Add one method to the Protocol:

```python
class Visualizer(Protocol):
    def on_pipeline_start(self, config: WorkflowConfig) -> None: ...
    def on_iteration_start(self, iteration: int, max_iterations: int) -> None: ...
    def on_phase_start(self, phase: PipelinePhase) -> None: ...
    def on_phase_end(self, phase: PipelinePhase, result: str) -> None: ...
    def on_agent_stream(self, line: str) -> None: ...          # ← NEW
    def on_log(self, message: str) -> None: ...
    def on_pipeline_end(self, metrics: PipelineMetrics) -> None: ...
```

Update `PlainVisualizer` and `RichVisualizer` to implement it (both can just pass or
print the line).

### 1b. Add `on_output` callback to `AgentRunner.run()`

**File:** `src/agent_native_workflow/runners/base.py`

Add an optional streaming callback:

```python
from collections.abc import Callable

class AgentRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
        on_output: Callable[[str], None] | None = None,   # ← NEW optional
    ) -> RunResult: ...
```

Since `on_output` defaults to `None`, all existing runners remain compatible without changes.

### 1c. Switch Claude runner to streaming `Popen`

**File:** `src/agent_native_workflow/runners/claude.py`

Replace `subprocess.run(capture_output=True)` with `subprocess.Popen` + line-by-line read:

```python
import subprocess
import threading

def run(self, prompt, *, session_id=None, timeout=300, max_retries=2,
        logger=None, on_output=None):
    # ... build cmd as before ...

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    output_lines: list[str] = []

    def _read_stream():
        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            output_lines.append(stripped)
            if on_output:
                on_output(stripped)

    reader = threading.Thread(target=_read_stream, daemon=True)
    reader.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        # ... handle timeout ...

    reader.join(timeout=5)
    full_output = "\n".join(output_lines)

    # ... return RunResult(output=full_output, session_id=...) ...
```

Key points:
- stdout is read in a daemon thread so the main thread can enforce timeout
- Each line is forwarded to `on_output` immediately
- Full output is still captured for `RunResult.output`

Apply the same pattern to `copilot.py`. For `codex.py` and `cursor.py`, just add the
`on_output` parameter to the signature (they can ignore it for now).

### 1d. Wire streaming through the pipeline

**File:** `src/agent_native_workflow/pipeline.py`

In `_run_implementation_phase`, pass the visualizer's stream callback:

```python
run_result = runner.run(
    prompt_text,
    session_id=session_id,
    timeout=timeout,
    max_retries=max_retries,
    logger=logger,
    on_output=on_output,    # ← pass through from caller
)
```

In `run_pipeline`, pass `visualizer.on_agent_stream` into `_run_implementation_phase`:

```python
new_session_id = _run_implementation_phase(
    ...,
    on_output=visualizer.on_agent_stream,
)
```

Add `on_output` as a parameter to `_run_implementation_phase`.

### 1e. MultiplexVisualizer

**File:** `src/agent_native_workflow/visualization/multiplex.py` (new file)

```python
from __future__ import annotations
from typing import TYPE_CHECKING
from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics

class MultiplexVisualizer:
    """Fans out every Visualizer event to multiple children."""

    def __init__(self, *children: object) -> None:
        self._children = children

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        for c in self._children:
            c.on_pipeline_start(config)

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        for c in self._children:
            c.on_iteration_start(iteration, max_iterations)

    def on_phase_start(self, phase: PipelinePhase) -> None:
        for c in self._children:
            c.on_phase_start(phase)

    def on_phase_end(self, phase: PipelinePhase, result: str) -> None:
        for c in self._children:
            c.on_phase_end(phase, result)

    def on_agent_stream(self, line: str) -> None:
        for c in self._children:
            c.on_agent_stream(line)

    def on_log(self, message: str) -> None:
        for c in self._children:
            c.on_log(message)

    def on_pipeline_end(self, metrics: PipelineMetrics) -> None:
        for c in self._children:
            c.on_pipeline_end(metrics)
```

### 1f. Update `make_visualizer`

**File:** `src/agent_native_workflow/visualization/__init__.py`

Update the factory to support `"textual"` mode and fall back gracefully:

```python
def make_visualizer(mode: str) -> object:
    if mode == "textual":
        try:
            from agent_native_workflow.visualization.textual_ui import TextualVisualizer
            return TextualVisualizer()
        except ImportError:
            pass
    if mode in ("rich", "textual"):
        try:
            from agent_native_workflow.visualization.rich_ui import RichVisualizer
            return RichVisualizer()
        except ImportError:
            pass
    return PlainVisualizer()
```

### 1g. Update `config.py` default

**File:** `src/agent_native_workflow/config.py`

Change default:

```python
visualization: str = "textual"  # "textual" | "rich" | "plain"
```

---

## Phase 2 — Textual TUI

### 2a. Dependencies

**File:** `pyproject.toml`

Add textual to dependencies:

```toml
dependencies = [
    "rich>=13.0",
    "pyyaml>=6.0",
    "textual>=3.0",
]
```

### 2b. App Structure

**File:** `src/agent_native_workflow/visualization/textual_ui.py` (new file)

The Textual app runs in a **daemon thread** so the pipeline stays synchronous. All
`on_*` callbacks use `app.call_from_thread()` to safely update widgets.

```python
from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, RichLog, Static
from textual.reactive import reactive
from textual.message import Message

from agent_native_workflow.visualization.base import PipelinePhase

if TYPE_CHECKING:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.domain import PipelineMetrics
```

### 2c. Target Layout

```
┌─ agent-native-workflow ─────────────────────────────────────────────────┐
│                                                                         │
│  provider: claude    model: haiku    verification: review               │
│  iteration: 2 / 5                    elapsed: 1m 23s                    │
│                                                                         │
├─ Pipeline Flow ─────────────────────────────────────────────────────────┤
│                                                                         │
│    ╭──────────╮       ╭──────────╮       ╭──────────╮                   │
│    │ Agent A  │──────▶│  Gates   │──────▶│ Agent R  │                   │
│    │  ✓ PASS  │       │ ⟳ RUNNING│       │ · pending│                   │
│    ╰──────────╯       ╰──────────╯       ╰──────────╯                   │
│                                                                         │
├─ Iterations ────────────────────────────────────────────────────────────┤
│                                                                         │
│  iter 1   A ✓  →  Gates ✓  →  R ✗  (verify_fail)          35.2s       │
│  iter 2   A ✓  →  Gates ⟳  →  R ·  (running)              12.1s       │
│                                                                         │
├─ Agent Output (streaming) ──────────────────────────────────────────────┤
│                                                                         │
│  Reading requirements.md...                                             │
│  Found 3 functional requirements.                                       │
│  Creating src/agent_native_workflow/commands/log.py...                   │
│  Writing cmd_log function...                                            │
│  ▌                                                                      │
│                                                                         │
├─ Pipeline Log ──────────────────────────────────────────────────────────┤
│                                                                         │
│  [12:34:01] === agent-native-workflow (provider: claude) ===            │
│  [12:34:01] Max iterations: 5                                           │
│  [12:34:02] --- Iteration 1 / 5 ---                                     │
│  [12:34:02] [phase1_implement] Started                                  │
│  [12:34:35] Agent A changed 2 file(s): commands/log.py, __init__.py     │
│                                                                         │
└─ q: quit  ──────────────────────────────────────────────────────────────┘
```

### 2d. Widget Breakdown

The app is composed of these custom widgets:

**`PipelineHeader`** — Static widget showing provider, model, iteration, elapsed time.
Uses `reactive` for `iteration` and a 1-second timer for elapsed time updates.

**`FlowDiagram`** — Three node boxes in a horizontal row connected by arrows.
Each node has a label and a status. Status determines color:
- `pending` → dim gray, border: gray
- `running` → yellow, border: yellow, pulsing dot animation
- `pass` → green, border: green
- `fail` → red, border: red

Implementation: Use three `Static` widgets in a `Horizontal` container.
Each node renders a Rich `Panel` with colored border. Arrow is a `Static("──▶")`.

```python
class FlowNode(Static):
    status = reactive("pending")

    def __init__(self, label: str, **kwargs):
        super().__init__(**kwargs)
        self.label = label

    def render(self) -> str:
        # Return a Rich-formatted box with colored border based on status
        ...

    def watch_status(self, new_status: str) -> None:
        # Trigger re-render and CSS class change
        self.remove_class("pending", "running", "pass", "fail")
        self.add_class(new_status)
```

**`IterationHistory`** — A `RichLog` or `ListView` that accumulates one line per
iteration as they complete. Each line shows the iteration number, phase results as
colored symbols, outcome, and duration.

Format per line:
```
iter 1   A ✓  →  Gates ✓  →  R ✗  (verify_fail)          35.2s
```

New iterations are added via `on_iteration_complete(iteration, outcome, duration, phase_results)`.

**`AgentStream`** — A `RichLog` widget (auto-scroll, max 200 lines) that receives
live agent output from `on_agent_stream`. Cleared at the start of each phase.

**`LogPanel`** — A `RichLog` widget (auto-scroll, max 100 lines) for pipeline log
messages from `on_log`.

### 2e. CSS Theme

**File:** `src/agent_native_workflow/visualization/textual_ui.tcss` (new file)

Dark theme with colored accents:

```css
Screen {
    background: $surface;
}

PipelineHeader {
    height: 4;
    padding: 1 2;
    background: $boost;
}

FlowDiagram {
    height: 5;
    padding: 0 2;
    layout: horizontal;
    align: center middle;
}

FlowNode {
    width: 16;
    height: 3;
    content-align: center middle;
    border: round $secondary;
}

FlowNode.running {
    border: round $warning;
    color: $warning;
}

FlowNode.pass {
    border: round $success;
    color: $success;
}

FlowNode.fail {
    border: round $error;
    color: $error;
}

FlowNode.pending {
    border: round $surface-lighten-2;
    color: $text-muted;
}

FlowArrow {
    width: 6;
    height: 3;
    content-align: center middle;
    color: $text-muted;
}

IterationHistory {
    height: auto;
    max-height: 8;
    border: round $primary;
}

AgentStream {
    height: 1fr;
    min-height: 6;
    border: round $accent;
}

LogPanel {
    height: 1fr;
    min-height: 6;
    border: round $secondary;
}
```

### 2f. TextualVisualizer class

This is the glue between the pipeline and the Textual app. It implements the
`Visualizer` protocol and communicates with the Textual app via `call_from_thread`.

```python
class TextualVisualizer:
    def __init__(self) -> None:
        self._app: PipelineApp | None = None
        self._thread: threading.Thread | None = None

    def on_pipeline_start(self, config: WorkflowConfig) -> None:
        self._app = PipelineApp(config)
        self._thread = threading.Thread(target=self._app.run, daemon=True)
        self._thread.start()
        # Wait for app to be ready
        time.sleep(0.5)

    def on_iteration_start(self, iteration: int, max_iterations: int) -> None:
        if self._app:
            self._app.call_from_thread(
                self._app.update_iteration, iteration, max_iterations
            )

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
```

### 2g. PipelineApp

```python
class PipelineApp(App):
    CSS_PATH = "textual_ui.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: WorkflowConfig) -> None:
        super().__init__()
        self._config = config
        self._iteration = 0
        self._max_iterations = config.max_iterations
        self._start_time = time.time()
        self._iter_history: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield PipelineHeaderWidget(self._config)
        with Horizontal(id="flow"):
            yield FlowNode("Agent A", id="node-a")
            yield FlowArrow()
            yield FlowNode("Gates", id="node-gates")
            yield FlowArrow()
            yield FlowNode(self._verify_label(), id="node-verify")
        yield IterationHistory(id="iter-history")
        yield RichLog(id="agent-stream", markup=True, highlight=True, auto_scroll=True)
        yield RichLog(id="log-panel", markup=True, highlight=True, auto_scroll=True)
        yield Footer()

    def _verify_label(self) -> str:
        v = getattr(self._config, "verification", "review")
        return {"review": "Agent R", "triangulation": "B + C", "none": "(skip)"}.get(v, "Verify")

    # Methods called from TextualVisualizer via call_from_thread:

    def update_iteration(self, iteration: int, max_iterations: int) -> None:
        self._iteration = iteration
        self._max_iterations = max_iterations
        # Reset flow nodes
        self.query_one("#node-a", FlowNode).status = "pending"
        self.query_one("#node-gates", FlowNode).status = "pending"
        self.query_one("#node-verify", FlowNode).status = "pending"
        # Update header
        self.query_one(PipelineHeaderWidget).update_iteration(iteration, max_iterations)
        # Clear agent stream for new iteration
        self.query_one("#agent-stream", RichLog).clear()

    def update_phase(self, phase: PipelinePhase, status: str) -> None:
        node_id = {
            PipelinePhase.IMPLEMENT: "#node-a",
            PipelinePhase.QUALITY_GATES: "#node-gates",
            PipelinePhase.TRIANGULAR_VERIFY: "#node-verify",
        }.get(phase)
        if node_id:
            self.query_one(node_id, FlowNode).status = status

    def append_agent_stream(self, line: str) -> None:
        self.query_one("#agent-stream", RichLog).write(line)

    def append_log(self, message: str) -> None:
        self.query_one("#log-panel", RichLog).write(message)

    def show_summary(self, metrics: PipelineMetrics) -> None:
        if metrics.converged:
            msg = f"[bold green]✓ CONVERGED — {metrics.total_iterations} iteration(s), {metrics.total_duration_s:.1f}s[/]"
        else:
            msg = f"[bold yellow]⚠ MAX ITERATIONS — {metrics.total_iterations} iteration(s), {metrics.total_duration_s:.1f}s[/]"
        self.query_one("#log-panel", RichLog).write(msg)
```

### 2h. Elapsed Timer

`PipelineHeaderWidget` should have a `set_interval(1.0, self._tick)` that updates
the elapsed time display every second. This solves the "frozen UI" feeling even when
Agent A is running.

---

## Phase 3 — Web Dashboard

### 3a. WebSocketVisualizer

**File:** `src/agent_native_workflow/visualization/ws_server.py` (new file)

A Visualizer that serializes events as JSON and broadcasts to WebSocket clients.

```python
import asyncio
import json
import threading
from collections import deque

class WebSocketVisualizer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9100) -> None:
        self._host = host
        self._port = port
        self._clients: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._history: deque[dict] = deque(maxlen=500)

    def on_pipeline_start(self, config) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        self._broadcast({"type": "pipeline_start", "data": {
            "provider": config.cli_provider,
            "max_iterations": config.max_iterations,
            "verification": getattr(config, "verification", "review"),
        }})

    def on_iteration_start(self, iteration, max_iterations) -> None:
        self._broadcast({"type": "iteration_start", "data": {
            "iteration": iteration, "max_iterations": max_iterations,
        }})

    def on_phase_start(self, phase) -> None:
        self._broadcast({"type": "phase_start", "data": {"phase": phase.value}})

    def on_phase_end(self, phase, result) -> None:
        self._broadcast({"type": "phase_end", "data": {"phase": phase.value, "result": result}})

    def on_agent_stream(self, line) -> None:
        self._broadcast({"type": "agent_stream", "data": {"line": line}})

    def on_log(self, message) -> None:
        self._broadcast({"type": "log", "data": {"message": message}})

    def on_pipeline_end(self, metrics) -> None:
        self._broadcast({"type": "pipeline_end", "data": {
            "converged": metrics.converged,
            "total_iterations": metrics.total_iterations,
            "total_duration_s": metrics.total_duration_s,
        }})
```

Use `websockets` library (add to optional deps). The server:
- Starts on `on_pipeline_start` in a daemon thread
- Keeps an event history so late-joining clients get full state
- Broadcasts every event as JSON to all connected clients

### 3b. Static Web Frontend

**File:** `src/agent_native_workflow/visualization/web/index.html` (new file)

Self-contained HTML file (no build step, no npm). Uses:
- Vanilla JS + WebSocket API
- CSS Grid for layout
- CSS custom properties for theming
- CSS animations for the flow diagram

Target design:

```
┌─────────────────────────────────────────────────────────────────┐
│  agent-native-workflow                    iteration 2/5  1m23s  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│        ┌─────┐           ┌─────┐           ┌─────┐             │
│        │  A  │ ·····▶    │Gates│ ·····▶    │  R  │             │
│        │  ✓  │           │  ⟳  │           │  ·  │             │
│        └─────┘           └─────┘           └─────┘             │
│                                                                 │
│  RUNNING phase pulses with a glow animation                     │
│  Arrows animate (CSS dash-offset) when data flows between nodes │
│                                                                 │
├─ Iteration Timeline ────────────────────────────────────────────┤
│                                                                 │
│  ● iter 1: A✓ Gates✓ R✗  35s                                   │
│  ◉ iter 2: A✓ Gates⟳     12s (running)                         │
│  ○ iter 3-5: pending                                            │
│                                                                 │
├─ Agent Output ─────────────── Pipeline Log ─────────────────────┤
│                              │                                  │
│  Creating log.py...          │ [12:34] Phase 1 started          │
│  Writing cmd_log...          │ [12:35] A changed 2 files        │
│  ▌                           │ [12:35] Phase 2 started          │
│                              │                                  │
└─────────────────────────────────────────────────────────────────┘
```

**CSS theme:** Dark background (#0d1117), colored nodes, glow effects on active elements,
smooth transitions when states change. Use CSS `@keyframes` for the pulsing glow on
running nodes and dash-offset animation on flow arrows.

**Flow diagram:** Use SVG for the arrow paths and HTML divs for the nodes. This allows
smooth CSS animations. Alternatively, use pure HTML/CSS with `::before`/`::after` pseudo
elements for arrows.

**Key CSS animations:**

```css
@keyframes pulse-glow {
    0%, 100% { box-shadow: 0 0 8px var(--color-running); }
    50%      { box-shadow: 0 0 20px var(--color-running); }
}

@keyframes flow-arrow {
    from { stroke-dashoffset: 20; }
    to   { stroke-dashoffset: 0; }
}

.node.running {
    border-color: var(--color-running);
    animation: pulse-glow 1.5s ease-in-out infinite;
}

.arrow.active path {
    stroke-dasharray: 8 4;
    animation: flow-arrow 0.8s linear infinite;
}
```

**WebSocket client logic:**

```javascript
const ws = new WebSocket(`ws://${location.hostname}:9100`);

ws.onmessage = (event) => {
    const { type, data } = JSON.parse(event.data);

    switch (type) {
        case "pipeline_start":
            initDashboard(data);
            break;
        case "iteration_start":
            resetNodes();
            updateIteration(data.iteration, data.max_iterations);
            break;
        case "phase_start":
            setNodeStatus(data.phase, "running");
            break;
        case "phase_end":
            setNodeStatus(data.phase, data.result);
            break;
        case "agent_stream":
            appendAgentOutput(data.line);
            break;
        case "log":
            appendLog(data.message);
            break;
        case "pipeline_end":
            showSummary(data);
            break;
    }
};
```

### 3c. Serve the static file

The `WebSocketVisualizer` can optionally serve `index.html` via a simple HTTP server
on the same port +1 (e.g. http://127.0.0.1:9101). Or simply print the URL to the
terminal and let the user open it manually.

### 3d. CLI flag

**File:** `src/agent_native_workflow/commands/parser.py`

Add `--web` flag to the `run` subparser:

```python
run_parser.add_argument("--web", action="store_true", help="Open web dashboard")
```

**File:** `src/agent_native_workflow/commands/run.py`

When `--web` is set, create a MultiplexVisualizer:

```python
from agent_native_workflow.visualization.multiplex import MultiplexVisualizer

if args.web:
    from agent_native_workflow.visualization.ws_server import WebSocketVisualizer
    ws_viz = WebSocketVisualizer()
    visualizer = MultiplexVisualizer(make_visualizer(wcfg.visualization), ws_viz)
```

### 3e. Optional dependency

**File:** `pyproject.toml`

Add websockets as an optional dependency:

```toml
[project.optional-dependencies]
web = ["websockets>=14.0"]
```

Users install with `pip install agent-native-workflow[web]`.

---

## Implementation Order

| Step | Description | Files |
|------|-------------|-------|
| 1a | `on_agent_stream` in Visualizer Protocol | `visualization/base.py`, `plain.py`, `rich_ui.py` |
| 1b | `on_output` callback in AgentRunner Protocol | `runners/base.py` |
| 1c | Streaming Popen in Claude runner | `runners/claude.py` |
| 1c | Streaming Popen in Copilot runner | `runners/copilot.py` |
| 1d | Wire streaming in pipeline | `pipeline.py` |
| 1e | MultiplexVisualizer | `visualization/multiplex.py` (new) |
| 1f | Update make_visualizer factory | `visualization/__init__.py` |
| 2a | Add textual dependency | `pyproject.toml` |
| 2b | TextualVisualizer + PipelineApp | `visualization/textual_ui.py` (new) |
| 2c | CSS theme | `visualization/textual_ui.tcss` (new) |
| 2d | Update config default | `config.py` |
| 3a | WebSocketVisualizer | `visualization/ws_server.py` (new) |
| 3b | Web frontend | `visualization/web/index.html` (new) |
| 3c | CLI --web flag | `commands/parser.py`, `commands/run.py` |
| 3d | Optional dependency | `pyproject.toml` |

---

## Testing

### Phase 1 Tests

**`tests/test_streaming.py`** (new):

1. **`test_claude_runner_calls_on_output`**: Mock subprocess.Popen, verify `on_output`
   callback receives each line as the subprocess outputs it.
2. **`test_runner_without_on_output_still_works`**: Call `runner.run()` without `on_output`
   — must not raise.
3. **`test_multiplex_visualizer_fans_out`**: Create MultiplexVisualizer with 2 mock
   children. Call every event. Verify both children received all events.

### Phase 2 Tests

**`tests/test_textual_ui.py`** (new):

4. **`test_textual_app_starts_and_exits`**: Create PipelineApp, call `run_test()`, verify
   it renders without error.
5. **`test_flow_node_status_updates`**: Programmatically set FlowNode status, verify CSS
   class changes.
6. **`test_agent_stream_appends_to_log`**: Call `append_agent_stream` with lines, verify
   RichLog content.

### Phase 3 Tests

7. **`test_ws_visualizer_serializes_events`**: Create WebSocketVisualizer, mock the
   broadcast, call all events. Verify JSON payloads are correct.
8. **`test_web_index_html_exists`**: Verify the static file is included in the package.

---

## File Change Summary

| File | Change |
|------|--------|
| `src/agent_native_workflow/visualization/base.py` | Add `on_agent_stream` to Protocol |
| `src/agent_native_workflow/visualization/plain.py` | Add `on_agent_stream` (no-op) |
| `src/agent_native_workflow/visualization/rich_ui.py` | Add `on_agent_stream` (no-op) |
| `src/agent_native_workflow/visualization/multiplex.py` | New: event fan-out |
| `src/agent_native_workflow/visualization/textual_ui.py` | New: Textual TUI |
| `src/agent_native_workflow/visualization/textual_ui.tcss` | New: CSS theme |
| `src/agent_native_workflow/visualization/ws_server.py` | New: WebSocket broadcaster |
| `src/agent_native_workflow/visualization/web/index.html` | New: web dashboard |
| `src/agent_native_workflow/visualization/__init__.py` | Update factory for textual/web |
| `src/agent_native_workflow/runners/base.py` | Add `on_output` to Protocol |
| `src/agent_native_workflow/runners/claude.py` | Streaming Popen |
| `src/agent_native_workflow/runners/copilot.py` | Streaming Popen |
| `src/agent_native_workflow/runners/codex.py` | Add `on_output` param (ignored) |
| `src/agent_native_workflow/runners/cursor.py` | Add `on_output` param (ignored) |
| `src/agent_native_workflow/pipeline.py` | Wire `on_output` through to runner |
| `src/agent_native_workflow/config.py` | Default visualization → "textual" |
| `src/agent_native_workflow/commands/parser.py` | Add `--web` flag |
| `src/agent_native_workflow/commands/run.py` | MultiplexVisualizer when --web |
| `pyproject.toml` | Add textual dep + optional websockets |
| `tests/test_streaming.py` | New: streaming + multiplex tests |
| `tests/test_textual_ui.py` | New: Textual widget tests |
