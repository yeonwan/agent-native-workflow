<div align="center">

# agent-native-workflow

**AI-native feature delivery pipeline**

*Turn a requirements doc into a production-ready PR — automatically.*

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

```
Requirements → Agent A (implement) → Quality Gates → Verification → Feedback Loop
                  ↑                                                      |
                  └──────────────────────────────────────────────────────┘
                                    (until convergence)
```

</div>

---

## The Problem

AI coding assistants are everywhere. But the bottleneck isn't writing code — it's the **iteration loop**: prompt → output → fix → PR → review → fix → review → merge. Each round-trip costs human attention.

## The Solution

**agent-native-workflow** (`anw`) automates the inner loop. It orchestrates coding agents in a repeatable cycle:

> **Implement → Lint & Test → Verify → Feedback → Repeat**

PRs arrive closer to "ready for humans." Human code review is still expected — this pipeline reduces how many round-trips you need to get there.

## Supported Agents

| Agent | CLI Flag | Resume Support |
|-------|----------|:--------------:|
| **Claude Code** | `--cli claude` | Yes |
| **GitHub Copilot CLI** | `--cli copilot` | Yes |
| **OpenAI Codex** | `--cli codex` | — |
| **Cursor** | `--cli cursor` | Experimental |

---

## Quick Start

### Installation

```bash
pip install agent-native-workflow
# or
uv add agent-native-workflow
```

### First Run

```bash
cd your-repo

# 1. Scaffold config files
anw init

# 2. Edit your task & requirements
#    .agent-native-workflow/PROMPT.yaml
#    .agent-native-workflow/requirements.md

# 3. Run the pipeline
anw run --cli claude
```

That's it. `anw` will iterate until quality gates pass and verification succeeds, or until `max-iterations` is reached.

---

## How It Works

### Three-Phase Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1: Implementation (Agent A)                      │
│  ─ Reads PROMPT.yaml + requirements.md                  │
│  ─ Writes / edits code in your repo                     │
│  ─ On iteration 2+: receives structured feedback        │
├─────────────────────────────────────────────────────────┤
│  Phase 2: Quality Gates                                 │
│  ─ Lint (ruff, eslint, clippy, …)                       │
│  ─ Test (pytest, jest, cargo test, …)                   │
│  ─ Auto-detected or configured per project              │
├─────────────────────────────────────────────────────────┤
│  Phase 3: Verification                                  │
│  ─ AI-powered review of requirements vs. changes        │
│  ─ Three strategies: none / review / triangulation      │
│  ─ Failures become feedback → back to Phase 1           │
└─────────────────────────────────────────────────────────┘
```

### Verification Strategies

| Strategy | How It Works | Best For |
|----------|-------------|----------|
| **`none`** | No AI verification — gates only. | Strong test coverage; fastest runs. |
| **`review`** | **Agent R** reads requirements + diff, approves or sends feedback. | Good default for most features. |
| **`triangulation`** | **Agent B** (dev review) → **Agent C** (PM/requirements check) → **Agent B** (sign-off). Three-agent consensus. | High-assurance delivery; critical features. |

#### Triangulation: Role Purity by Design

The triangulation strategy enforces **information restriction** to prevent bias:

- **Agent B** (Senior Dev) reviews code quality — *never sees requirements*
- **Agent C** (PM Judge) checks requirements coverage — *never sees raw code, only Agent B's changelog*
- Both must independently agree for the iteration to pass

This mirrors real-world review where different reviewers catch different classes of issues.

---

## Configuration

### File Structure

After `anw init`, your project gets:

```
.agent-native-workflow/
├── config.yaml          # Pipeline settings + advanced per-agent overrides
├── PROMPT.yaml          # Task definition for Agent A
└── requirements.md      # Source of truth for verification agents
```

### Key Settings

**config.yaml**
```yaml
cli-provider: claude          # claude | copilot | codex | cursor
verification: review          # none | review | triangulation
lint-cmd: ruff check .
test-cmd: pytest
max-iterations: 5
agents:
  agent_a:
    model: claude-sonnet-4-6
  agent_r:
    model: claude-sonnet-4-6
```

All settings can be overridden via CLI flags or environment variables (`CLI_PROVIDER`, `VERIFICATION`, `LINT_CMD`, `TEST_CMD`, etc.).

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `anw run` | Full pipeline — implement, gate, verify, iterate. |
| `anw verify` | Run only the verification phase against existing changes. |
| `anw status` | Show latest run summary. `--list` for all runs, `--run <id>` for a specific one. |
| `anw detect` | Auto-detect project type, lint/test commands. |
| `anw providers` | Check which agent CLIs are installed. |
| `anw init` | Scaffold `.agent-native-workflow/` in your project. |
| `anw log` | View execution logs. |
| `anw clean` | Remove old run artifacts. |
| `anw export` | Export run results. |

### Common Flags

```bash
anw run \
  --cli claude \
  --requirements path/to/requirements.md \
  --verification triangulation \
  --max-iterations 5 \
  --model claude-sonnet-4-6 \
  --dry-run
```

---

## Auto-Detection

`anw` automatically detects your project type and configures appropriate gates:

| Project | Lint | Test |
|---------|------|------|
| **Python** (uv / poetry / pip) | `ruff check .` | `pytest` |
| **Node.js** (npm / yarn / pnpm) | `eslint .` | `jest` / `vitest` |
| **Rust** (cargo) | `cargo clippy` | `cargo test` |
| **Go** | `golangci-lint run` | `go test ./...` |
| **Java** (Maven / Gradle) | — | `mvn test` / `gradle test` |

Override with `lint-cmd` and `test-cmd` in config or CLI flags.

---

## Run Artifacts

Every pipeline run is fully auditable:

```
.agent-native-workflow/runs/
└── run-20260401-143022/
    ├── iter-001/
    │   ├── a-output.md       # Agent A's implementation output
    │   ├── gates.json        # Structured lint/test results
    │   └── feedback.md       # Feedback sent to next iteration
    ├── iter-002/
    │   ├── a-output.md
    │   ├── gates.json
    │   └── review.md         # Agent R's review (review mode)
    └── ...
```

---

## Python API

For programmatic use:

```python
from agent_native_workflow.api import Workflow

converged = (
    Workflow()
    .with_provider("claude")
    .with_verification("review")
    .with_max_iterations(3)
    .with_model("claude-sonnet-4-6")
    .run()
)

if converged:
    print("Pipeline converged — ready for human review!")
```

---

## Requirements Format

`anw` supports multiple requirements formats:

- **Markdown** (`.md`) — native, recommended
- **Word** (`.docx`) — auto-converted via python-docx
- **PDF** (`.pdf`) — auto-converted via pypdf

Non-markdown formats are converted to markdown snapshots stored in the run directory.

---

## Design Philosophy

> *Simple agents x many iterations > Complex agents x few iterations*

1. **Iterate, don't over-engineer** — Imperfection per iteration is fine. The loop handles convergence.
2. **Role purity** — Each agent holds deliberately scoped information. No agent does another's job.
3. **Gates before verification** — Catch mechanical errors (lint, test) cheaply before expensive AI review.
4. **Full auditability** — Every iteration's inputs and outputs are preserved.
5. **Human-in-the-loop** — The pipeline reduces round-trips, not replaces human judgment.

---

## Development

```bash
git clone https://github.com/your-org/agent-native-workflow.git
cd agent-native-workflow

uv sync --group dev
uv run pytest              # Run tests
uv run ruff check src tests  # Lint
```

---

<div align="center">

**Stop babysitting AI-generated PRs. Let the loop do the work.**

</div>
