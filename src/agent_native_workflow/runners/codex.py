from __future__ import annotations

import subprocess
import time

from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import RunResult


class OpenAICodexRunner:
    """Runner using OpenAI Codex CLI (codex).

    Codex CLI supports autonomous file editing, so supports_file_tools = True.
    Uses 'codex -q <prompt>' for non-interactive output.
    Session resume is not supported by this runner (CLI TBD).
    """

    provider_name = "codex"
    supports_file_tools = True
    supports_resume = False

    def __init__(self, *, model: str = "") -> None:
        self._model = model

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
    ) -> RunResult:
        _ = session_id  # not supported

        for attempt in range(1, max_retries + 1):
            try:
                cmd = ["codex", "-q", prompt]
                if self._model:
                    cmd.extend(["--model", self._model])

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    return RunResult(output=result.stdout, session_id=None)
                if logger:
                    logger.warn(f"codex exited with code {result.returncode} (attempt {attempt})")
                if result.stderr and logger:
                    logger.warn(f"stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                if logger:
                    logger.warn(f"codex timed out after {timeout}s (attempt {attempt})")
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "'codex' CLI not found in PATH. Install OpenAI Codex CLI first."
                ) from exc

            if attempt < max_retries:
                backoff = 2**attempt
                if logger:
                    logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(f"codex failed after {max_retries} attempts")
