from __future__ import annotations

import subprocess
import time

from agent_native_workflow.log import Logger


class CursorRunner:
    """Runner using Cursor CLI (cursor).

    Cursor supports autonomous file editing, so supports_file_tools = True.
    Note: Cursor headless/CLI mode is experimental. Behavior may vary.
    """

    provider_name = "cursor"
    supports_file_tools = True
    experimental = True

    def __init__(self, *, model: str = "") -> None:
        self._model = model

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
                cmd = ["cursor", "--headless", "--prompt", prompt]
                if self._model:
                    cmd.extend(["--model", self._model])

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
                        f"cursor exited with code {result.returncode} (attempt {attempt})"
                    )
                if result.stderr and logger:
                    logger.warn(f"stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                if logger:
                    logger.warn(f"cursor timed out after {timeout}s (attempt {attempt})")
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "'cursor' CLI not found in PATH. Cursor headless mode is experimental."
                ) from exc

            if attempt < max_retries:
                backoff = 2**attempt
                if logger:
                    logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(f"cursor failed after {max_retries} attempts")
