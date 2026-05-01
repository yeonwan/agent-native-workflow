"""String templates for `anw init` (keeps init command module readable)."""

PROMPT_YAML = """\
# PROMPT.yaml — Agent A task definition
#
# HOW THIS WORKS:
#   - `title`, `build`, `criteria` are used by Agent A (implementer)
#   - `requirements.md` (or --requirements <file>) is used for verification (review / triangulation)
#   - If you delete this file, Agent A will read requirements.md directly instead
#
# TIP: If your Jira ticket already describes everything, you can skip this file
#      and just run: anw run --requirements path/to/ticket.docx
#
# Run `anw run` when ready. Only `title` is required; everything else is optional.

title: "Implement requirements"

build: |
  Read requirements.md and implement everything listed there.


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
# Add test criteria only if the requirements explicitly ask for tests:
# - New tests cover the happy path and at least one error case
"""

REQUIREMENTS_MD = """\
# Requirements: <Feature Title>

<!--
  Source of truth for verification (review mode: Agent R; triangulation: B/C).
  Write each requirement as a testable statement.
  Tip: you can replace this file with a Jira ticket (.docx or .pdf) using:
       anw run --requirements path/to/PROJ-123.docx
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


_CODEREVIEW_HEADER = """\
# Code Review Guidelines

<!--
  Optional: project-specific conventions for Agent R (the reviewer).
  Agent R reads this file during review but violations here do NOT block approval.
  They appear as "Suggestions" in the review output.
  Delete this file if you only want requirements-based review.
-->
"""

_CODEREVIEW_BODIES: dict[str, str] = {
    "python": """
## Conventions
- Follow existing naming patterns in the codebase
- All public functions must have type hints and a docstring
- No bare `except:` — always specify exception type

## Patterns
- Use `pathlib.Path` over `os.path` for file operations
- Prefer dataclasses or Pydantic models over raw dicts for structured data

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use fixtures for shared test setup
""",
    "node": """
## Conventions
- Follow existing naming patterns in the codebase
- Use TypeScript strict mode — no `any` unless unavoidable
- Prefer `const` over `let`; never use `var`

## Patterns
- Use async/await over raw Promises or callbacks
- Prefer named exports over default exports
- Use path aliases over deep relative imports (`../../../`)

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use `describe`/`it` blocks with clear test names
""",
    "rust": """
## Conventions
- Follow existing naming patterns in the codebase
- All public items must have doc comments (`///`)
- No `unwrap()` or `expect()` in library code — propagate errors with `?`

## Patterns
- Prefer `impl Trait` over `dyn Trait` where possible
- Use `thiserror` for custom error types
- Derive `Debug` on all public structs

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use `#[cfg(test)]` module for unit tests
""",
    "go": """
## Conventions
- Follow existing naming patterns in the codebase
- Exported functions must have a Go doc comment
- Always handle errors — no `_ = someFunc()`

## Patterns
- Use `context.Context` as the first parameter for I/O functions
- Prefer table-driven tests
- Use `errors.Is`/`errors.As` over string matching

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use `t.Helper()` in test helper functions
""",
    "java-maven": """
## Conventions
- Follow existing naming patterns in the codebase
- All public methods must have Javadoc
- No raw types — always use generics

## Patterns
- Prefer constructor injection over field injection
- Use `Optional` instead of returning null
- Prefer immutable collections where possible

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use JUnit 5 with descriptive `@DisplayName`
""",
    "java-gradle": """
## Conventions
- Follow existing naming patterns in the codebase
- All public methods must have Javadoc
- No raw types — always use generics

## Patterns
- Prefer constructor injection over field injection
- Use `Optional` instead of returning null
- Prefer immutable collections where possible

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use JUnit 5 with descriptive `@DisplayName`
""",
}

_CODEREVIEW_DEFAULT = """
## Conventions
- Follow existing naming patterns in the codebase
- All public functions/methods must be documented
- Handle errors explicitly — no silent failures

## Patterns
- Keep functions small and focused
- Prefer composition over inheritance

## Testing
- Tests must be deterministic (no sleep, no real network)
- Cover the happy path and at least one error case
"""


def codereview_md(project_type: str) -> str:
    """Generate codereview.md content tailored to the detected project type."""
    body = _CODEREVIEW_BODIES.get(project_type, _CODEREVIEW_DEFAULT)
    return _CODEREVIEW_HEADER + body


def config_yaml(
    project_type: str,
    lint_hint: str,
    test_hint: str,
    cli_provider: str = "claude",
    agents_yaml: str = "",
) -> str:
    return f"""\
# agent-native-workflow configuration
# Edit this file to customize the workflow for this project.
# All settings are optional — defaults are auto-detected from the project.

# CLI provider for all agents (A, R, B, C).
# Options: claude, copilot, codex, cursor
cli-provider: {cli_provider}

# Verification strategy after quality gates pass.
# Options: none, review, triangulation
#   none           — gates only (fastest; strong test suites)
#   review         — Agent R checks requirements vs changed files (recommended default)
#   triangulation  — B→C→B multi-agent consensus (thorough, slower)
verification: review

# Advisory convergence: when > 0, Agent R's advisory suggestions are sent back
# to Agent A up to N times before accepting. Default: 1 advisory retry.
advisory-iterations: 1

# Quality gate commands (auto-detected from project type: {project_type}).
# Edit or remove to override.
{lint_hint}
{test_hint}

# Pipeline limits
# max-iterations: 5
# timeout: 600    # seconds per agent call
# max-retries: 2

{agents_yaml.rstrip()}
"""
