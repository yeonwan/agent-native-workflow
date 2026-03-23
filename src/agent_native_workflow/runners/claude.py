from __future__ import annotations

import subprocess
import time

from agent_native_workflow.log import Logger


class ClaudeCodeRunner:
    """Runner using Claude Code CLI (claude).

    Supports autonomous file editing, so supports_file_tools = True.
    """

    provider_name = "claude"
    supports_file_tools = True

    def __init__(
        self,
        *,
        model: str = "",
        allowed_tools: list[str] | None = None,
        permission_mode: str = "bypassPermissions",
        **_kwargs: object,
    ) -> None:
        self._model = model
        self._allowed_tools = allowed_tools or []
        self._permission_mode = permission_mode

    def run(
        self,
        prompt: str,
        *,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
    ) -> str:
        for attempt in range(1, max_retries + 1):
            try:
                cmd = ["claude", "--print"]

                if self._permission_mode:
                    cmd.extend(["--permission-mode", self._permission_mode])

                if self._allowed_tools:
                    cmd.extend(["--allowedTools", *self._allowed_tools])

                if self._model:
                    cmd.extend(["--model", self._model])

                cmd.extend(["-p", prompt])

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    return result.stdout
                if logger:
                    logger.warn(
                        f"claude exited with code {result.returncode} (attempt {attempt})"
                    )
                if result.stderr and logger:
                    logger.warn(f"stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                if logger:
                    logger.warn(f"claude timed out after {timeout}s (attempt {attempt})")
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "'claude' CLI not found in PATH. Install Claude Code first."
                ) from exc

            if attempt < max_retries:
                backoff = 2**attempt
                if logger:
                    logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(f"claude failed after {max_retries} attempts")
