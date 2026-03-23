"""Prompt loader — reads PROMPT.yaml or PROMPT.md and returns agent-ready text.

PROMPT.yaml schema:
    title: str                  # Feature title (required)
    context: str                # Project context, frameworks, conventions
    build: str                  # What to build (concrete description)
    constraints: list[str]      # Rules the agent must follow
    criteria: list[str]         # Completion checklist (checked by gates + triangulation)
    notes: str                  # Optional extra info or hints

Example PROMPT.yaml:
    title: "Add user deactivation endpoint"
    context: |
      FastAPI + SQLAlchemy ORM. Follow route patterns in src/api/routes/.
    build: |
      POST /users/{id}/deactivate — sets active=false, sends email, returns 204.
    constraints:
      - Reuse EmailService.send_transactional(), no direct SMTP
      - Respect existing auth middleware
    criteria:
      - All requirements in requirements.md implemented
      - Lint passes
      - New tests cover happy path and error case

Usage:
    from agent_native_workflow.prompt_loader import load_prompt

    text = load_prompt(Path(".agent-native-workflow/PROMPT.yaml"))
    text = load_prompt(Path(".agent-native-workflow/PROMPT.md"))   # passthrough
"""

from __future__ import annotations

from pathlib import Path

_YAML_SUFFIXES = {".yaml", ".yml"}


def load_prompt_title(path: Path) -> str:
    """Extract just the title from a PROMPT.yaml file.

    Returns empty string for non-YAML files or on any error.
    """
    if not path.is_file() or path.suffix.lower() not in _YAML_SUFFIXES:
        return ""
    try:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return str(raw.get("title", "")).strip()
    except Exception:
        pass
    return ""


def load_prompt(path: Path) -> str:
    """Read PROMPT file and return agent-ready markdown text.

    .yaml / .yml → parsed and rendered as structured markdown
    .md / .txt   → returned as-is
    """
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    if path.suffix.lower() in _YAML_SUFFIXES:
        return _load_yaml_prompt(path)

    return path.read_text(encoding="utf-8")


def is_yaml_prompt(path: Path) -> bool:
    return path.suffix.lower() in _YAML_SUFFIXES


# ── YAML renderer ─────────────────────────────────────────────────────────────

def _load_yaml_prompt(path: Path) -> str:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "Reading PROMPT.yaml requires PyYAML. Install it with:\n"
            "  pip install pyyaml\n"
            "  # or: uv add pyyaml"
        ) from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"PROMPT.yaml must be a YAML mapping, got: {type(raw).__name__}")

    return _render(raw)


def _render(data: dict[str, object]) -> str:
    """Render parsed YAML dict into a markdown prompt string."""
    sections: list[str] = []

    title = str(data.get("title", "Task")).strip()
    sections.append(f"# {title}")

    if context := _text(data.get("context")):
        sections.append(f"## Context\n\n{context}")

    if build := _text(data.get("build")):
        sections.append(f"## What to Build\n\n{build}")

    if constraints := _list(data.get("constraints")):
        items = "\n".join(f"- {c}" for c in constraints)
        sections.append(f"## Key Constraints\n\n{items}")

    if criteria := _list(data.get("criteria")):
        items = "\n".join(f"- [ ] {c}" for c in criteria)
        sections.append(f"## Completion Criteria\n\n{items}")

    if notes := _text(data.get("notes")):
        sections.append(f"## Notes\n\n{notes}")

    sections.append(
        "## Completion Signal\n\n"
        "When ALL criteria above are met, output exactly:\n"
        "LOOP_COMPLETE"
    )

    return "\n\n".join(sections)


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None]
    # Single string → wrap as one-item list
    return [str(value).strip()]
