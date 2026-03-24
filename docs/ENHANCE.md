# Enhancement: Session Resume & Token-Efficient Gate Output

## Context

After the REDESIGN (verification strategies, multi-provider support), two fundamental
architectural weaknesses remain:

1. **Context loss per iteration**: Every `claude -p "..."` call starts from zero.
   Agent A re-reads the entire codebase, re-discovers the architecture, re-understands
   what it did last time. Same for Agent R/B/C. This wastes tokens and degrades fix quality.

2. **Gate output flooding**: Quality gate output (test logs, lint output) is passed raw
   into the next Agent A prompt. A large test suite can produce thousands of lines; all of
   it eats into the agent's context window even though only the failure summary matters.

These two changes reposition the project as a **token-efficient AI coding orchestrator**
— the pipeline manages context and information flow so agents focus on coding.

---

## Part 1: Session Resume

### 1.1 The Problem

Current flow (one-shot per iteration):
```
iter 1: claude -p "implement X"       → session created and destroyed
iter 2: claude -p "test failed, fix"  → new session, codebase re-read from scratch
iter 3: claude -p "reviewer says ..."  → new session again
```

Each iteration: Agent A spends ~30% of its time/tokens just re-reading files it already
knows. It also loses memory of approaches it already tried, making it likely to repeat
the same failed fix.

### 1.2 Target Flow

```
iter 1: claude -p "implement X"                        → session_id = "abc123"
        (pipeline runs gates externally)
iter 2: claude --resume "abc123" -p "test failed, fix" → same context, remembers everything
        (pipeline runs gates externally)
iter 3: claude --resume "abc123" -p "reviewer says ..." → still same context
```

Agent A keeps its full working memory across iterations. It remembers what it changed,
why, and what it already tried. Massively reduces redundant codebase exploration.

### 1.3 Provider CLI Resume Flags

| Provider | Resume mechanism | Flag |
|----------|-----------------|------|
| Claude Code | Session resume by ID | `claude --resume --session-id <id> -p "..."` |
| GitHub Copilot | Named sessions | `copilot --session <name> -p "..."` (verify exact flag) |
| OpenAI Codex | TBD — check if supported | May not have resume yet |
| Cursor | TBD — experimental CLI | May not have resume yet |

**Important**: Before implementing, verify the exact CLI flags for each provider.
Run `claude --help`, `copilot --help` etc. and document the actual flags.
Providers that don't support resume continue to work as one-shot (graceful fallback).

### 1.4 Protocol Changes

File: `src/agent_native_workflow/runners/base.py`

Replace the current `AgentRunner` protocol:

```python
from dataclasses import dataclass


@dataclass
class RunResult:
    """Result of an agent run, including optional session ID for resume."""
    output: str
    session_id: str | None = None  # None if provider doesn't support resume


@runtime_checkable
class AgentRunner(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def supports_file_tools(self) -> bool: ...

    @property
    def supports_resume(self) -> bool:
        """True if this provider supports session resume across calls."""
        ...

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
    ) -> RunResult:
        """Execute the prompt. If session_id is provided and supports_resume is True,
        resume the existing session instead of starting fresh."""
        ...
```

**Breaking change**: `run()` now returns `RunResult` instead of `str`.
All callers must be updated (`pipeline.py`, `strategies/*.py`, `verify.py`, `security.py`,
tests).

### 1.5 Runner Implementations

#### `runners/claude.py`

```python
class ClaudeCodeRunner:
    supports_resume = True

    def run(self, prompt, *, session_id=None, timeout=300, ...) -> RunResult:
        cmd = ["claude", "--print"]

        if session_id:
            cmd.extend(["--resume", "--session-id", session_id])

        # ... existing permission_mode, allowed_tools, model flags ...
        cmd.extend(["-p", prompt])

        result = subprocess.run(cmd, ...)

        # Extract session_id from Claude's output/metadata.
        # Claude Code prints session info — parse it, or generate a stable ID
        # from the run. Verify how Claude actually exposes the session ID.
        new_session_id = self._extract_session_id(result) or session_id

        return RunResult(output=result.stdout, session_id=new_session_id)
```

**Action needed**: Run `claude --help` to verify exact resume flags and how session ID
is obtained/returned. The flags above are best-guess; adjust after verification.

#### `runners/copilot.py`

Same pattern. Verify with `copilot --help`.

#### `runners/codex.py` and `runners/cursor.py`

If they don't support resume:
```python
class OpenAICodexRunner:
    supports_resume = False

    def run(self, prompt, *, session_id=None, ...) -> RunResult:
        # session_id ignored
        # ... existing implementation ...
        return RunResult(output=result.stdout, session_id=None)
```

### 1.6 Pipeline Changes

File: `src/agent_native_workflow/pipeline.py`

The pipeline tracks session IDs per agent role across iterations:

```python
# In run_pipeline(), before the iteration loop:
agent_a_session: str | None = None
review_session: str | None = None  # for Agent R / B / C

# In _run_implementation_phase (or its caller):
result = runner.run(prompt_text, session_id=agent_a_session, timeout=..., ...)
agent_a_session = result.session_id  # carry forward to next iteration
store.write_agent_output(iteration, result.output)
```

For verification agents (Agent R in review mode):
```python
# In strategy.run():
result = self.runner.run(review_prompt, session_id=self._session_id, ...)
self._session_id = result.session_id  # remember for next iteration's review
```

**Session ID storage**: Write `session_id` to `manifest.json` or a separate
`session-state.json` in the run directory. This allows potential resume of interrupted
pipeline runs in the future (not in scope now, but the data is there).

### 1.7 Store Changes

File: `src/agent_native_workflow/store.py`

Add session tracking:

```python
def write_session_state(self, agent_sessions: dict[str, str | None]) -> Path:
    """Persist agent session IDs for the current run.

    Args:
        agent_sessions: Mapping of role → session_id, e.g.
            {"agent_a": "abc123", "agent_r": "def456"}
    """
    path = self.run_dir / "session-state.json"
    path.write_text(json.dumps(agent_sessions, indent=2))
    return path

def load_session_state(self) -> dict[str, str | None]:
    """Load persisted session IDs, or empty dict if none."""
    path = self.run_dir / "session-state.json"
    if path.is_file():
        return json.loads(path.read_text())
    return {}
```

### 1.8 Agent R Resume (Optional, Lower Priority)

Agent R benefits less from resume than Agent A because its input (requirements + diff)
is already concise. But if Agent R remembers what it flagged last iteration, it can:
- Verify that specific issues it raised were actually fixed
- Skip re-checking requirements it already confirmed as MET
- Produce more targeted reviews ("I asked you to fix X — you did / you didn't")

Implement this the same way: pass `session_id` through the strategy's `run()` method.
The strategy holds `self._session_id` as state.

**Recommendation**: Implement Agent A resume first. Add Agent R resume as a follow-up
if the session mechanism works well.

---

## Part 2: Token-Efficient Gate Output

### 2.1 The Problem

Current gate handling in `pipeline.py`:

```python
_GATE_OUTPUT_LIMIT = 500  # characters — already truncated but crude

output = result.stdout + result.stderr   # raw concatenation
gate_output = f"{name} ({cmd}) FAILED:\n{output}"
# This goes into feedback → Agent A's prompt next iteration
```

Problems:
- 500 chars is arbitrary. A pytest traceback for one failure is easily 500+ chars.
  For multiple failures, you get truncated garbage.
- No structure — it's just raw text. "line 42 of test output" means nothing without
  knowing which test or which file.
- For large test suites (minutes of runtime, thousands of lines), even 500 chars
  might include irrelevant noise (progress bars, collection output, warnings).

### 2.2 Design: Output Digesters

A **digester** transforms raw gate output into a concise, structured summary optimized
for agent consumption. The pipeline calls the digester before passing output to feedback.

```
raw gate output (potentially thousands of lines)
    │
    ▼
  Digester (framework-aware or generic)
    │
    ▼
  Structured summary (tens of lines, failure-focused)
    │
    ▼
  Agent A's feedback prompt
```

### 2.3 Digester Interface

File: `src/agent_native_workflow/digesters/base.py`

```python
from typing import Protocol


class GateDigester(Protocol):
    """Transforms raw gate command output into a concise failure summary."""

    def digest(self, raw_output: str, exit_code: int) -> str:
        """Return a concise summary suitable for inclusion in an agent prompt.

        For passing gates (exit_code == 0), return empty string or a one-liner.
        For failing gates, return structured failure information.
        """
        ...
```

### 2.4 Built-in Digesters

File: `src/agent_native_workflow/digesters/`

#### `generic.py` — Fallback for Any Framework

```python
class GenericDigester:
    """Best-effort digester for unknown frameworks.

    Strategy:
    1. If output <= max_chars, return as-is
    2. Otherwise, extract lines containing common failure patterns
    3. Fall back to last N lines (usually contains the summary)
    """

    def __init__(self, max_chars: int = 2000) -> None:
        self._max_chars = max_chars

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""
        if len(raw_output) <= self._max_chars:
            return raw_output

        # Try to extract failure-relevant lines
        failure_patterns = [
            "FAILED", "FAIL", "ERROR", "Error:", "error:",
            "AssertionError", "assert", "panic:", "PANIC",
            "expected", "actual", "not equal",
            "✗", "✘", "×",
        ]
        relevant = []
        for line in raw_output.splitlines():
            if any(p in line for p in failure_patterns):
                relevant.append(line)

        if relevant:
            summary = "\n".join(relevant[:50])  # cap at 50 relevant lines
            if len(summary) <= self._max_chars:
                return summary

        # Last resort: tail of output (most frameworks print summary at end)
        lines = raw_output.splitlines()
        tail = "\n".join(lines[-40:])
        return tail[:self._max_chars]
```

#### `pytest_digester.py` — Python pytest

```python
class PytestDigester:
    """Parses pytest output for concise failure summaries.

    Pytest has several output modes. This digester handles:
    - Default verbose output (FAILED lines + short tracebacks)
    - --tb=short output (already concise)
    - --tb=line output (one line per failure — ideal)

    For best results, configure test-cmd with --tb=short -q:
        test-cmd: uv run pytest --tb=short -q
    """

    def __init__(self, max_chars: int = 3000) -> None:
        self._max_chars = max_chars

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        lines = raw_output.splitlines()
        sections: list[str] = []

        # 1. Extract "short test summary info" section (pytest prints this near the end)
        in_summary = False
        summary_lines: list[str] = []
        for line in lines:
            if "short test summary info" in line:
                in_summary = True
                continue
            if in_summary:
                if line.startswith("=") or not line.strip():
                    break
                summary_lines.append(line)

        if summary_lines:
            sections.append("Failed tests:\n" + "\n".join(summary_lines))

        # 2. Extract the final result line (e.g. "3 failed, 12 passed in 4.2s")
        for line in reversed(lines):
            if "failed" in line and ("passed" in line or "error" in line):
                sections.append(line.strip())
                break

        # 3. If no summary section found, extract FAILED lines
        if not summary_lines:
            failed_lines = [l for l in lines if "FAILED" in l or "ERROR" in l]
            if failed_lines:
                sections.append("Failures:\n" + "\n".join(failed_lines[:20]))

        result = "\n\n".join(sections) if sections else raw_output[-self._max_chars:]
        return result[:self._max_chars]
```

#### `jest_digester.py` — JavaScript jest

```python
class JestDigester:
    """Parses jest output (plain text or --json mode)."""

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        # If jest --json was used, parse structured output
        try:
            import json
            data = json.loads(raw_output)
            failures = []
            for suite in data.get("testResults", []):
                for test in suite.get("testResults", []):
                    if test["status"] == "failed":
                        name = test.get("fullName", test.get("title", "?"))
                        msg = "\n".join(test.get("failureMessages", [])[:2])
                        failures.append(f"FAIL: {name}\n{msg[:300]}")
            if failures:
                return "\n\n".join(failures[:10])
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback: text mode — extract "● " prefixed failure blocks
        lines = raw_output.splitlines()
        failure_blocks: list[str] = []
        current_block: list[str] = []
        in_failure = False

        for line in lines:
            if line.strip().startswith("●"):
                if current_block:
                    failure_blocks.append("\n".join(current_block))
                current_block = [line]
                in_failure = True
            elif in_failure:
                if line.strip() == "" and len(current_block) > 3:
                    failure_blocks.append("\n".join(current_block))
                    current_block = []
                    in_failure = False
                else:
                    current_block.append(line)

        if current_block:
            failure_blocks.append("\n".join(current_block))

        if failure_blocks:
            return "\n\n".join(failure_blocks[:10])[:3000]

        return raw_output[-2000:]
```

#### Additional digesters to consider (not in initial scope):

- `go_test_digester.py` — `go test -json` output parser
- `cargo_test_digester.py` — Rust cargo test output
- `eslint_digester.py` — ESLint/ruff lint output (group by severity)

### 2.5 Digester Selection

File: `src/agent_native_workflow/digesters/factory.py`

```python
def build_digester(gate_name: str, cmd: str) -> GateDigester:
    """Select the best digester for a gate command.

    Detection order:
    1. Explicit config (future: digest-format: pytest in config.yaml)
    2. Infer from command string
    3. Fall back to GenericDigester
    """
    cmd_lower = cmd.lower()

    if "pytest" in cmd_lower or "py.test" in cmd_lower:
        return PytestDigester()
    if "jest" in cmd_lower or "vitest" in cmd_lower:
        return JestDigester()

    # Future: go, cargo, eslint, ruff ...

    return GenericDigester()
```

### 2.6 Pipeline Integration

File: `src/agent_native_workflow/pipeline.py`

Change `_run_gate_command` to return the full output, and add digestion at the feedback
stage — not at execution. Raw output is always stored for debugging; only the digest
goes into Agent A's prompt.

```python
# In the gate failure path (around line 82 currently):

from agent_native_workflow.digesters.factory import build_digester

# After gate fails:
digester = build_digester(name, cmd)
digested_output = digester.digest(raw_output, exit_code=1)

# Store raw output in gates.json (full, for human debugging)
results.append(GateResult(name=name, status=GateStatus.FAIL, output=raw_output))

# But pass only the digest to feedback → Agent A
gate_output = f"{name} ({cmd}) FAILED:\n{digested_output}"
```

Also update `_GATE_OUTPUT_LIMIT`: remove the crude character truncation (line 37)
or increase it significantly, since digesters now handle summarization properly.

### 2.7 Config: Suggested Concise Commands

File: `src/agent_native_workflow/detect.py`

When auto-detecting test commands, prefer concise output flags:

```python
# In detect_test_cmd():

# Current:
#   return f"{runner} pytest" if runner else "pytest"

# Enhanced:
#   return f"{runner} pytest --tb=short -q" if runner else "pytest --tb=short -q"
```

This gives the digester cleaner input to work with. Do the same for other frameworks:

| Framework | Current detect output | Enhanced |
|-----------|----------------------|----------|
| pytest | `uv run pytest` | `uv run pytest --tb=short -q` |
| jest | `npx jest` | `npx jest --ci` (cleaner output) |
| go test | `go test ./...` | `go test -count=1 ./...` |
| cargo test | `cargo test` | `cargo test` (already concise) |

**Important**: Only add flags that don't change test behavior, only output format.
`--tb=short` changes formatting only. `-q` reduces noise. `--ci` in jest disables
interactive/watch mode.

### 2.8 Config: User-Defined Digest Format (Optional)

For power users who know their output format:

```yaml
# config.yaml
test-cmd: make test
test-digest: generic    # or: pytest, jest, custom
# test-digest-cmd: ./scripts/parse-test-output.sh   # custom script
```

This is low priority — auto-detection from the command string covers most cases.

---

## Part 3: Enhanced Iteration Context

### 3.1 Motivation

With session resume, Agent A remembers its own actions. But the pipeline still needs to
pass structured feedback for gate failures and verification results. This section improves
the quality of that feedback.

### 3.2 Changes to `store.build_agent_a_context()`

Currently (in `store.py`), the context for iteration N includes all previous iterations'
gate results and feedback. Enhance this to be more actionable:

```python
def build_agent_a_context(self, iteration: int, source_file: Path) -> str:
    # ... existing code to load previous iterations ...

    # NEW: If session resume is active, use a shorter context format.
    # The agent already remembers what it did — just tell it what failed.
    if self._session_active:
        return self._build_resume_context(iteration, source_file)
    else:
        return self._build_full_context(iteration, source_file)
```

Resume context (shorter, focused):
```
## Iteration 2 — Fix Required

Your previous changes are still in the working directory. Do not start over.

### Gate Failure
test (uv run pytest --tb=short -q) FAILED:
  FAILED test_auth.py::test_login - AssertionError: expected 200, got 401

### What to Fix
- The login endpoint returns 401. Check the auth middleware.

Fix only the failing items. Do not re-implement what already works.
When done, output LOOP_COMPLETE.
```

Full context (for non-resume providers, same as current but with digested gate output
instead of raw).

### 3.3 Implementation

The `_session_active` flag comes from the pipeline:

```python
store.set_session_mode(runner.supports_resume and agent_a_session is not None)
```

This is a simple boolean on `RunStore` that affects context generation format.

---

## Implementation Order

```
Phase A: Protocol & Runner Changes (foundation)
  A.1  Add RunResult dataclass to runners/base.py
  A.2  Add supports_resume property to AgentRunner protocol
  A.3  Update run() signature: add session_id param, return RunResult
  A.4  Update ClaudeCodeRunner (add --resume flags)
  A.5  Update CopilotRunner (add session flags — verify CLI first)
  A.6  Update CodexRunner + CursorRunner (supports_resume = False, return RunResult)
  A.7  Fix all callers: pipeline.py, strategies/*.py, verify.py, security.py
  A.8  Fix all tests

Phase B: Pipeline Session Tracking
  B.1  Add session tracking to RunStore (write_session_state / load_session_state)
  B.2  Update run_pipeline() to carry session_id across iterations
  B.3  Update _run_implementation_phase() to pass/receive session_id
  B.4  Add session_mode flag to RunStore for context generation
  B.5  Add resume-aware context format in build_agent_a_context()

Phase C: Gate Digesters
  C.1  Create digesters/ package with base.py (GateDigester protocol)
  C.2  Implement GenericDigester
  C.3  Implement PytestDigester
  C.4  Implement JestDigester (if time permits)
  C.5  Create factory.py (auto-select digester from command string)
  C.6  Integrate into pipeline.py gate failure path
  C.7  Remove or raise _GATE_OUTPUT_LIMIT
  C.8  Update detect.py to suggest concise output flags

Phase D: Verification Resume (optional, after A+B work)
  D.1  Pass session_id through VerificationStrategy.run() — **done** (`verification_session_id` on `VerificationStrategy.run`)
  D.2  Update ReviewStrategy to carry Agent R session across iterations — **done** (`next_agent_r_session_id`, pipeline `agent_r_session`)
  D.3  Update TriangulationStrategy if applicable — **skipped** (B/C multi-call sessions out of scope)

Phase E: Tests — **done**
  E.1  Unit tests for RunResult / new protocol — `tests/test_pipeline_resume.py` (`RunResult` frozen, `AgentRunner` `isinstance`)
  E.2  Unit tests for each digester — `tests/test_digesters.py` (+ `tests/test_gates_runner.py`)
  E.3  Integration test: pipeline with mock resume runner — `tests/test_pipeline_resume.py` (flaky gate + session carry; review + `agent_r` in `session-state.json`)
  E.4  Test backward compat: non-resume runner — `tests/test_pipeline_resume.py` (`_NoResumeAgentA`)
```

### Dependencies

```
A.1 ──▶ A.2 ──▶ A.3 ──▶ A.4/A.5/A.6 (parallel) ──▶ A.7 ──▶ A.8
                                                        │
                                            B.1 ──▶ B.2 ──▶ B.3 ──▶ B.4 ──▶ B.5

C.1 ──▶ C.2/C.3/C.4 (parallel) ──▶ C.5 ──▶ C.6 ──▶ C.7 ──▶ C.8

D.1 ──▶ D.2 ──▶ D.3  (after Phase B)
```

Phase A and Phase C are independent and can be done in parallel.
Phase B depends on Phase A.
Phase D depends on Phase B.

---

## File Change Summary

### New files:
- `src/agent_native_workflow/digesters/__init__.py`
- `src/agent_native_workflow/digesters/base.py`
- `src/agent_native_workflow/digesters/generic.py`
- `src/agent_native_workflow/digesters/pytest_digester.py`
- `src/agent_native_workflow/digesters/jest_digester.py`
- `src/agent_native_workflow/digesters/factory.py`

### Modified files:
- `src/agent_native_workflow/runners/base.py` — RunResult, supports_resume, session_id param
- `src/agent_native_workflow/runners/claude.py` — resume flags, return RunResult
- `src/agent_native_workflow/runners/copilot.py` — resume flags, return RunResult
- `src/agent_native_workflow/runners/codex.py` — return RunResult (no resume)
- `src/agent_native_workflow/runners/cursor.py` — return RunResult (no resume)
- `src/agent_native_workflow/pipeline.py` — session tracking, digester integration
- `src/agent_native_workflow/store.py` — session state persistence, resume-aware context
- `src/agent_native_workflow/detect.py` — concise output flags for test commands
- `src/agent_native_workflow/strategies/review.py` — handle RunResult
- `src/agent_native_workflow/strategies/triangulation.py` — handle RunResult
- `src/agent_native_workflow/verify.py` — handle RunResult
- `src/agent_native_workflow/security.py` — handle RunResult

### Test files to update:
- `tests/test_strategies.py` — mock RunResult instead of str
- `tests/test_dry_run.py` — should be unaffected (no runner calls)
- `tests/test_status.py` — should be unaffected
- New: `tests/test_digesters.py`
- New: `tests/test_session_resume.py`

---

## Pre-Implementation Checklist

Before coding, the implementer MUST:

1. **Verify Claude Code resume flags**: Run `claude --help` and find the exact session
   resume syntax. The flags in this doc (`--resume --session-id <id>`) are guesses.
   Update Section 1.5 with actual flags.

2. **Verify Copilot resume flags**: Run `copilot --help` or check docs. Update Section 1.5.

3. **Check if Codex/Cursor support resume**: If they do, add implementation.
   If not, confirm `supports_resume = False` is correct.

4. **Run existing tests**: `uv run pytest -q` — all 42 tests must pass before starting.

5. **Read current pipeline.py carefully**: The gate output flow (lines 60-167) and
   implementation phase (lines 170-224) are the primary integration points.

---

## Design Decisions

### Why return RunResult instead of adding a separate get_session_id() method?

Session ID is a product of calling `run()`. It doesn't exist before the call and may
change between calls. Returning it from `run()` is the natural place. A separate method
would require runners to hold mutable state, which is less clean.

### Why digesters instead of just adding --tb=short everywhere?

1. Users may have existing test commands in CI they don't want to change.
2. Some projects use `make test` which wraps the real test command — we can't inject flags.
3. Digesters work on any output, including custom test frameworks.
4. The `--tb=short` suggestion in detect.py is additive — it helps digesters work better,
   but digesters still work without it.

### Why not use AI to summarize gate output?

Using a model to summarize test logs for another model is circular and adds cost/latency.
Deterministic parsing is faster, cheaper, and more predictable. The whole point of gates
being external is to avoid burning tokens on test output processing.

### Why session resume before digesters?

Session resume has higher impact on token savings and fix quality. Digesters are also
valuable but the improvement is incremental (from 500 chars truncated to ~2000 chars
structured). Resume eliminates entire categories of redundant work.
