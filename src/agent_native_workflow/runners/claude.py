from __future__ import annotations

import subprocess
import time
import uuid

from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import RunResult


class ClaudeCodeRunner:
    """Runner using Claude Code CLI (claude).

    Supports autonomous file editing, so supports_file_tools = True.
    Session: first call uses ``--session-id <uuid>``; later calls use ``--resume <id>``.
    """

    provider_name = "claude"
    supports_file_tools = True
    supports_resume = True

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
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
    ) -> RunResult:
        new_session_id: str | None = None

        for attempt in range(1, max_retries + 1):
            try:
                cmd = ["claude", "--print"]

                if session_id is not None:
                    cmd.extend(["--resume", session_id])
                else:
                    if new_session_id is None:
                        new_session_id = str(uuid.uuid4())
                    cmd.extend(["--session-id", new_session_id])

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
                    sid = session_id if session_id is not None else new_session_id
                    return RunResult(output=result.stdout, session_id=sid)
                if logger:
                    logger.warn(f"claude exited with code {result.returncode} (attempt {attempt})")
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
