"""String templates for `agn init` (keeps init command module readable)."""

PROMPT_YAML = """\
# PROMPT.yaml — Agent A task definition
#
# HOW THIS WORKS:
#   - `title`, `build`, `criteria` are used by Agent A (implementer)
#   - `requirements.md` (or --requirements <file>) is used for verification (review / triangulation)
#   - If you delete this file, Agent A will read requirements.md directly instead
#
# TIP: If your Jira ticket already describes everything, you can skip this file
#      and just run: agn run --requirements path/to/ticket.docx
#
# Run `agn run` when ready. Only `title` is required; everything else is optional.

title: "Implement requirements"

build: |
  Read requirements.md and implement everything listed there.
  Read existing code first before making any changes.

# Add more context when the requirements file alone isn't enough for Agent A:
#
# context: |
#   FastAPI + SQLAlchemy ORM. Follow route patterns in src/api/routes/.
#   Tests use pytest + testcontainers — no mocking of DB layer.
#
# constraints:
#   - Do not change existing database schema
#   - Reuse existing service classes — no logic duplication
#   - All existing tests must continue to pass
#
# notes: |
#   See docs/architecture.md for system overview.

# Completion checklist — checked by quality gates; align with config verification mode.
criteria:
  - All requirements in requirements.md implemented
  - Lint passes
  - All existing tests pass
# Add test criteria only if the requirements explicitly ask for tests:
# - New tests cover the happy path and at least one error case
"""

REQUIREMENTS_MD = """\
# Requirements: <Feature Title>

<!--
  Source of truth for verification (review mode: Agent R; triangulation: B/C).
  Write each requirement as a testable statement.
  Tip: you can replace this file with a Jira ticket (.docx or .pdf) using:
       agn run --requirements path/to/PROJ-123.docx
-->

## Functional Requirements

### FR-1: <Short Name>

**What**: <One sentence describing the behavior>

**Acceptance criteria**:
- Given <precondition>, when <action>, then <expected result>
- Error case: <what happens when input is invalid / resource not found>

### FR-2: <Short Name>

**What**: ...

**Acceptance criteria**:
- ...

## Non-Functional Requirements

### NFR-1: Code Quality
- Follow existing project patterns and naming conventions
- No new public function left without a docstring
- No commented-out code in final output

### NFR-2: Test Coverage
- New logic must have unit tests
- Tests must be deterministic (no sleep, no real network calls)
"""


def config_yaml(project_type: str, lint_hint: str, test_hint: str) -> str:
    return f"""\
# agent-native-workflow configuration
# Edit this file to customize the workflow for this project.
# All settings are optional — defaults are auto-detected from the project.

# CLI provider for all agents (A, R, B, C).
# Options: claude, copilot, codex, cursor
cli-provider: claude

# Verification strategy after quality gates pass.
# Options: none, review, triangulation
#   none           — gates only (fastest; strong test suites)
#   review         — Agent R checks requirements vs changed files (recommended default)
#   triangulation  — B→C→B multi-agent consensus (thorough, slower)
verification: review

# Quality gate commands.
# Auto-detected from project type ({project_type}):
{lint_hint}
{test_hint}
# Uncomment and edit to override:
# lint-cmd: make lint
# test-cmd: make test

# Pipeline limits
# max-iterations: 5
# timeout: 300    # seconds per agent call
# max-retries: 2
"""
