# agent-native-workflow

**AI-native feature delivery pipeline** — turn a requirements doc into implementation that passes your quality gates, with optional AI verification before human review.

## What & why

- **What**: Orchestrates coding agents (Claude Code, Copilot CLI, Codex, Cursor) in a repeatable loop: **implement → lint & test → verify → feedback → repeat** until the run converges or hits `max-iterations`.
- **Why**: When everyone ships with AI assistants, the bottleneck becomes **iteration** (prompt → output → fix → PR → review ping-pong). This tool automates the inner loop so PRs arrive closer to “ready for humans.”
- **How**: One **Agent A** implements from `PROMPT.yaml` / `requirements.md`. After each change, **quality gates** run. Then a **verification strategy** (see below) can check requirements vs. the diff. Failures become structured feedback for the next iteration.

Human code review is still expected; this pipeline reduces how many round-trips you need.

## Installation

```bash
pip install agent-native-workflow
# or
uv add agent-native-workflow
```

Entry points: `anw` and `agent-native-workflow` (same CLI).

## Quick start

```bash
cd your-repo
anw init                    # .agent-native-workflow/ templates + config
# Edit .agent-native-workflow/PROMPT.yaml and requirements.md
anw run --cli claude        # or copilot, codex, cursor
```

Use `anw run --help` for flags (`--requirements`, `--verification`, `--dry-run`, etc.).

## Verification modes

After **lint** and **test** pass, one of:

| Mode | Behavior | When to use |
|------|------------|-------------|
| **`none`** | No extra AI step; iteration succeeds when gates pass. | Strong test coverage; fastest runs. |
| **`review`** (default in code) | **Agent R** reads requirements + changed files; emits `REVIEW_APPROVE` or feedback. | Good default for most features. |
| **`triangulation`** | **Agent B → C → B**: blind-style dev review, PM-style requirements check, then dev sign-off (`CONSENSUS_AGREE`). | Higher assurance; slower and more API calls. |

Configure in `.agent-native-workflow/config.yaml` as `verification: none | review | triangulation`, or override with `anw run --verification …` / `anw verify --verification …`.

Agent tooling and models live in `.agent-native-workflow/agent-config.yaml` (`agent_a`, **`agent_r`** for review mode, `agent_b` / `agent_c` for triangulation).

## Configuration

| File | Role |
|------|------|
| `config.yaml` | `cli-provider`, `verification`, `lint-cmd`, `test-cmd`, limits. |
| `agent-config.yaml` | Per-role tools and `model` (A, R, B, C). |
| `PROMPT.yaml` | Agent A task (optional if requirements alone suffice). |
| `requirements.md` | Source of truth for verification agents. |

Environment variables (see `WorkflowConfig` in code): e.g. `CLI_PROVIDER`, `VERIFICATION`, `LINT_CMD`, `TEST_CMD`.

## Commands

| Command | Purpose |
|---------|---------|
| `anw run` | Full pipeline with optional Rich UI. |
| `anw verify` | Run only the verification phase (uses same config / `--verification`). |
| `anw status` | Summarize latest or `--run <id>` run; `--list` all runs. |
| `anw detect` | Print auto-detected lint/test commands and project type. |
| `anw providers` | Which CLI backends are installed. |
| `anw init` | Scaffold `.agent-native-workflow/` files. |

## Artifacts

Each run is under `.agent-native-workflow/runs/run-YYYYMMDD-HHMMSS/iter-NNN/`:

- `a-output.md`, `gates.json`, `feedback.md` (on failure)
- `review.md` if `verification: review`
- `b-review.md`, `c-report.md`, `b-confirm.md` if `verification: triangulation`

## Python API

```python
from pathlib import Path
from agent_native_workflow.api import Workflow

Workflow().with_provider("claude").with_verification("review").run()
```

See `src/agent_native_workflow/api.py` for the fluent builder.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
```
