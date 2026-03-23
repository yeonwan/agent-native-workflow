from __future__ import annotations

from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.runners.claude import ClaudeCodeRunner
from agent_native_workflow.runners.codex import OpenAICodexRunner
from agent_native_workflow.runners.copilot import GitHubCopilotRunner
from agent_native_workflow.runners.cursor import CursorRunner

_REGISTRY: dict[str, type] = {
    "copilot": GitHubCopilotRunner,
    "claude": ClaudeCodeRunner,
    "codex": OpenAICodexRunner,
    "cursor": CursorRunner,
}


def runner_for(provider: str, **kwargs: object) -> AgentRunner:
    """Instantiate a runner by provider name.

    Args:
        provider: One of 'copilot', 'claude', 'codex', 'cursor'.
        **kwargs: Passed to the runner's __init__ (e.g. model='claude-sonnet-4-6').

    Raises:
        ValueError: If the provider name is not registered.
    """
    cls = _REGISTRY.get(provider.lower())
    if cls is None:
        valid = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown CLI provider '{provider}'. Valid providers: {valid}"
        )
    return cls(**kwargs)  # type: ignore[return-value]


def available_providers() -> list[dict[str, object]]:
    """Return info about all registered providers for the 'providers' subcommand."""
    import shutil

    cli_map = {
        "copilot": "copilot",
        "claude": "claude",
        "codex": "codex",
        "cursor": "cursor",
    }
    experimental = {"cursor"}

    result = []
    for name, cls in _REGISTRY.items():
        cli_cmd = cli_map.get(name, name)
        found = shutil.which(cli_cmd) is not None
        instance = cls.__new__(cls)
        result.append(
            {
                "provider": name,
                "cli_cmd": cli_cmd,
                "file_tools": getattr(cls, "supports_file_tools", False),
                "status": "available" if found else f"not found ({cli_cmd} not in PATH)",
                "experimental": name in experimental,
            }
        )
    return result
