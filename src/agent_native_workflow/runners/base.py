from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_native_workflow.log import Logger


@runtime_checkable
class AgentRunner(Protocol):
    """Strategy interface for all CLI-backed agent runners.

    To add a new provider:
    1. Create a new file in runners/ implementing this Protocol
    2. Add one entry to _REGISTRY in runners/factory.py
    """

    @property
    def provider_name(self) -> str:
        """Human-readable provider name, e.g. 'copilot', 'claude', 'codex'."""
        ...

    @property
    def supports_file_tools(self) -> bool:
        """True if the CLI autonomously reads/writes files (Claude Code, Codex, Cursor).
        False if the CLI returns text only (Copilot explain) — the pipeline will
        attempt to apply Agent A output as a patch/code blocks in this case.
        """
        ...

    def run(
        self,
        prompt: str,
        *,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
    ) -> str:
        """Execute the prompt and return the raw text output."""
        ...
