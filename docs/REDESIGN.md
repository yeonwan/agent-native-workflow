# Project Redesign: AI-Native Feature Delivery Pipeline

## Context & Motivation

This project started as a "triangular verification" tool — three AI agents (Implementer, Blind Reviewer, Judge) cross-checking each other to verify code correctness. After real-world testing, we discovered fundamental issues:

1. **Blind review paradox**: Agent B (reviewer) doesn't know the requirements, so it can't focus its review on what matters. It produces unfocused analyses (full codebase audits) instead of targeted reviews.
2. **Overly strict judging**: Agent C (judge) marks things as "not verified" simply because Agent B didn't mention them — even when the implementation is correct.
3. **Same-model bias**: When A, B, C all use the same model family, they share systematic blind spots. This isn't real triangulation.
4. **Cost vs value**: For simple tasks, triangulation adds ~10min latency and 3x agent calls while catching nothing that tests don't already catch.

**The real value of this project is not triangulation — it's the automated iteration loop.** Implement → lint → test → fix → repeat until clean. This alone saves significant human time by producing PR-ready code.

### New Direction

Reposition from "triangular verification tool" to **"AI-native feature delivery pipeline"**:

- **Core**: Implement → quality gates → auto-iterate → produce clean PR
- **Optional**: Verification strategies (review, triangulation) for when extra confidence is needed

---

## Architecture Overview

### Pipeline Modes

```
┌─────────────────────────────────────────────────────────┐
│                    agn run                               │
│                                                         │
│  ┌──────────┐     ┌──────────────┐     ┌────────────┐  │
│  │ Agent A   │────▶│ Quality Gates│────▶│ Converged? │  │
│  │(implement)│◀────│ (lint+test)  │     │            │  │
│  └──────────┘     └──────────────┘     └─────┬──────┘  │
│       ▲                                      │         │
│       │              gate_fail               │         │
│       └──────────────────────────────────────┘         │
│                                              │         │
│                              gates pass      ▼         │
│                                                         │
│              ┌───────────────────────────────┐          │
│              │     Verification Strategy     │          │
│              │  (configurable, optional)      │          │
│              │                               │          │
│              │  "none"   → skip, ship it     │          │
│              │  "review" → Agent R reviews   │          │
│              │  "triangulation" → B→C→B      │          │
│              └───────────────────────────────┘          │
│                              │                          │
│              pass ───────────┼──────────▶ Done (PR)     │
│              fail ───────────┘──▶ feedback → Agent A    │
└─────────────────────────────────────────────────────────┘
```

### Verification Strategies

#### `none` (default)
Quality gates only. If lint + test pass, the iteration converges.
Best for: well-tested codebases, simple/routine tasks, fast iteration.

#### `review` (recommended for most tasks)
A single reviewer agent checks requirements against the actual code changes.

```
Agent R reads:
  1. Requirements file (source of truth)
  2. Changed files (git diff or file list)
  3. Test results summary

Agent R produces:
  - Per-requirement status: MET / NOT MET / PARTIAL
  - Issues found (with file:line references)
  - Overall verdict: APPROVE or REQUEST_CHANGES

If REQUEST_CHANGES → feedback to Agent A → next iteration
If APPROVE → converge
```

This mirrors what a human code reviewer does: they see the ticket AND the diff.

#### `triangulation` (opt-in, for high-stakes changes)
The existing B→C→B consensus flow. Senior Dev (B) reviews code without seeing requirements, PM (C) cross-checks, Senior Dev confirms.

Best for: complex requirements, high-risk changes, when extra paranoia is warranted.

---

## Implementation Plan

### Phase 1: Make Verification Pluggable

Currently verification is hardcoded in `pipeline.py`. Refactor so the pipeline accepts a verification strategy.

#### 1.1 New: `VerificationStrategy` protocol

File: `src/agent_native_workflow/domain.py`

Add a protocol (or base class) that all verification strategies implement:

```python
class VerificationResult:
    passed: bool
    feedback: str  # content for Agent A if failed, empty if passed

class VerificationStrategy(Protocol):
    def run(
        self,
        requirements_file: Path,
        store: RunStore,
        iteration: int,
        config: ProjectConfig,
        timeout: int,
        max_retries: int,
        logger: Logger,
    ) -> VerificationResult: ...
```

#### 1.2 Implement `NoneStrategy`

File: `src/agent_native_workflow/strategies/none.py`

Trivial — always returns `VerificationResult(passed=True, feedback="")`. Quality gates already ran; if they passed, we're done.

#### 1.3 Implement `ReviewStrategy` (NEW — the main addition)

File: `src/agent_native_workflow/strategies/review.py`

Single reviewer agent that reads requirements + code diff:

```python
REVIEW_APPROVE_MARKER = "REVIEW_APPROVE"

class ReviewStrategy:
    def __init__(self, runner: AgentRunner):
        self.runner = runner

    def run(self, ...) -> VerificationResult:
        # Build prompt with requirements + changed files
        # Agent R reviews and outputs structured verdict
        # Check for REVIEW_APPROVE_MARKER
        # Return result
```

**Agent R prompt design** (this is the critical part):

```
You are a code reviewer checking whether an implementation meets its requirements.

## Requirements
Read `{requirements_file}` — this is the source of truth.

## Changed Files
The following files were changed in this implementation:
{changed_files}

Read each changed file and verify the implementation against requirements.

## Your Review

For each requirement or acceptance criterion:
- **Requirement**: [quote it]
- **Status**: MET / NOT MET / PARTIAL
- **Evidence**: specific code references (function names, line behavior) that confirm or deny

## Issues
List anything that must be fixed, with specific file and location.

## Verdict
If all requirements are MET and no blocking issues exist, output on its own line:
REVIEW_APPROVE

Otherwise, list exactly what Agent A must fix.
```

Key design decisions for Agent R:
- Agent R CAN read code files (unlike Agent C in triangulation)
- Agent R CAN read requirements (unlike Agent B in triangulation)
- Agent R is essentially a simulated senior code reviewer with full context
- The prompt asks for structured per-requirement evidence, not open-ended analysis

#### 1.4 Move Existing Triangulation to `TriangulationStrategy`

File: `src/agent_native_workflow/strategies/triangulation.py`

Move the current `verify.py` logic (B→C→B consensus) into this strategy class. Keep the improved prompts (Senior Dev B, PM C, consensus round) that were already implemented.

#### 1.5 Refactor `pipeline.py`

Remove the hardcoded call to `run_triangular_verification`. Instead:

```python
# In run_pipeline(), after quality gates pass:

strategy = _build_strategy(wcfg.verification_mode, runner, verify_runner, c_runner)
result = strategy.run(
    requirements_file=agents_requirements_file,
    store=store,
    iteration=iteration,
    config=cfg,
    timeout=agent_timeout,
    max_retries=max_retries,
    logger=logger,
)

if result.passed:
    # converge
else:
    store.write_feedback(iteration, result.feedback, ...)
    # continue to next iteration
```

#### 1.6 Add `verification` to config

File: `src/agent_native_workflow/config.py` and `.agent-native-workflow/config.yaml`

```yaml
# config.yaml
verification: review    # none | review | triangulation
```

Also accept as CLI flag: `agn run --verification none`

### Phase 2: Agent R Runner Configuration

#### 2.1 Add `agent_r` to `AgentConfig`

File: `src/agent_native_workflow/domain.py`

Agent R needs to read code AND requirements, so it gets more tools than old Agent B/C:

```python
_AGENT_R_TOOLS = ["Read", "Grep", "Glob", "Bash(git:diff)", "Bash(git:log)"]
```

Agent R's permission model is read-only (like B), but with access to both code and requirements.

Add to `agent-config.yaml`:

```yaml
agent_r:
  allowed_tools:
    - Read
    - Grep
    - Glob
    - Bash(git:diff)
    - Bash(git:log)
  permission_mode: bypassPermissions
  model: ""  # user configures
```

#### 2.2 Update `agent_config_for()` default models

Agent R should default to a balanced model (same tier as old Agent B):

```python
_DEFAULT_MODELS = {
    "claude": {
        "agent_a": "claude-opus-4-6",
        "agent_r": "claude-sonnet-4-6",    # reviewer
        "agent_b": "claude-sonnet-4-6",    # triangulation only
        "agent_c": "claude-haiku-4-5-20251001",  # triangulation only
    },
    ...
}
```

### Phase 3: Store & Artifacts

#### 3.1 Update `RunStore` for review artifacts

Add methods for the review strategy:

```python
def write_review(self, iteration: int, content: str) -> Path:
    """Write Agent R's review."""
    path = self.iter_dir(iteration) / "review.md"
    path.write_text(content)
    return path
```

Updated directory structure:

```
iter-001/
├── a-output.md        (Agent A — always)
├── gates.json         (quality gates — always)
├── review.md          (Agent R — review mode)
├── b-review.md        (Agent B — triangulation mode)
├── c-report.md        (Agent C — triangulation mode)
├── b-confirm.md       (Agent B confirm — triangulation mode)
└── feedback.md        (if failed — always)
```

#### 3.2 Update `agn status` display

Show which verification strategy was used and its result.

### Phase 4: Update CLI & Init

#### 4.1 Update `build_parser()` in `cli.py`

Add `--verification` flag to `run` subparser:

```python
run_parser.add_argument(
    "--verification",
    choices=["none", "review", "triangulation"],
    default=None,  # falls back to config.yaml, then "review"
    help="Verification strategy after quality gates pass",
)
```

#### 4.2 Update `agn init` templates

Update the generated `config.yaml` template to document the new option:

```yaml
# Verification strategy after quality gates pass.
# Options: none, review, triangulation
# - none: gates only (fastest, for well-tested codebases)
# - review: AI reviewer checks requirements vs code (recommended)
# - triangulation: multi-agent cross-verification (thorough, slower)
verification: review
```

#### 4.3 Update `agn verify` standalone command

`agn verify` currently runs triangulation. Update it to respect the `--verification` flag,
defaulting to `review`.

### Phase 5: Documentation & README

Update the project README to reflect the new positioning:

- **What**: AI-native feature delivery pipeline
- **How**: Automated implement → test → review → iterate loop
- **Why**: Reduce human iteration cycles, produce PR-ready code
- **Verification modes**: explain none/review/triangulation and when to use each

---

## File Change Summary

### New files to create:
- `src/agent_native_workflow/strategies/__init__.py`
- `src/agent_native_workflow/strategies/none.py`
- `src/agent_native_workflow/strategies/review.py`
- `src/agent_native_workflow/strategies/triangulation.py`

### Files to modify:
- `src/agent_native_workflow/domain.py` — add VerificationResult, REVIEW_APPROVE_MARKER, Agent R config
- `src/agent_native_workflow/pipeline.py` — replace hardcoded verify call with strategy pattern
- `src/agent_native_workflow/config.py` — add `verification` field to WorkflowConfig
- `src/agent_native_workflow/store.py` — add `write_review()` method
- `src/agent_native_workflow/cli.py` — add `--verification` flag, update `_cmd_verify`
- `.agent-native-workflow/config.yaml` — add `verification` option (template)

### Files to keep as-is:
- `src/agent_native_workflow/verify.py` — keep for now, content moves to `strategies/triangulation.py`
- `src/agent_native_workflow/runners/` — no changes needed
- `src/agent_native_workflow/prompt_loader.py` — no changes needed
- `src/agent_native_workflow/detect.py` — no changes needed
- `tests/` — existing tests should continue to pass

### Files to eventually deprecate:
- `src/agent_native_workflow/verify.py` — after migration to strategies/triangulation.py, this becomes a thin re-export wrapper for backward compatibility

---

## Implementation Order & Dependencies

```
Phase 1.1  VerificationStrategy protocol in domain.py
   │
   ├── Phase 1.2  NoneStrategy (trivial, no deps)
   │
   ├── Phase 1.3  ReviewStrategy (needs 1.1 + store changes from 3.1)
   │
   ├── Phase 1.4  TriangulationStrategy (needs 1.1, wraps existing verify.py)
   │
   └── Phase 1.5  Refactor pipeline.py (needs 1.1 + at least one strategy)
           │
           Phase 1.6  Config changes (needs 1.5)
               │
               Phase 2    Agent R runner config
               │
               Phase 3    Store artifacts
               │
               Phase 4    CLI updates
               │
               Phase 5    Documentation
```

Recommended approach: implement phases 1.1 → 1.2 → 1.3 → 1.5 → 1.6 first (the core path), then 1.4 (triangulation migration), then 2-5.

---

## Testing Strategy

### Existing tests (must not break):
- `tests/test_dry_run.py` (29 tests) — CLI behavior, unaffected by verification changes
- `tests/test_status.py` — status display, may need minor updates for new fields

### New tests needed:

#### Unit tests for strategies:
- `tests/test_strategies.py`:
  - `NoneStrategy.run()` always returns passed=True
  - `ReviewStrategy.run()` with mocked runner — check prompt construction, marker detection
  - `TriangulationStrategy.run()` — migrated from existing verify tests if any

#### Integration test for pipeline with different strategies:
- `tests/test_pipeline_modes.py`:
  - Pipeline with `verification="none"` converges after gates pass
  - Pipeline with `verification="review"` calls reviewer agent
  - Pipeline with `verification="triangulation"` calls B→C→B

#### Config tests:
- `verification` field parsed from config.yaml
- `--verification` CLI flag overrides config
- Default is `"review"` when not specified

---

## Design Decisions & Rationale

### Why "review" as default instead of "none"?

`none` is fastest but provides no requirements verification — only mechanical checks (lint/test).
For most real tasks, having one reviewer pass costs ~30 seconds of API time and catches
"implemented the wrong thing" before it reaches human review. The cost-benefit is favorable.

### Why keep triangulation at all?

It's already implemented and tuned. Some users may want the extra rigor for critical changes.
Making it opt-in costs nothing; removing it loses an option.

### Why a new Agent R instead of reusing Agent B or C?

Agent B was designed to NOT see requirements (blind review). Agent C was designed to NOT see code.
Agent R sees both — it's a fundamentally different role. Clean separation avoids prompt confusion.

### Why strategy pattern instead of if/else?

Each strategy has different agent needs (runners, tools, prompts). Strategy pattern keeps
pipeline.py clean and makes it easy to add new strategies later (e.g., a "security" strategy
that focuses on vulnerability scanning).
