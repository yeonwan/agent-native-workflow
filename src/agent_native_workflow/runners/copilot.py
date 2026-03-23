from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from agent_native_workflow.log import Logger


class GitHubCopilotRunner:
    """Runner using GitHub Copilot CLI (copilot).

    Uses 'copilot <prompt>' for non-interactive text output.
    supports_file_tools = False: returns text only, so the pipeline
    will parse Agent A output as markdown code blocks and apply them
    to the working directory.

    Note: allowed_tools and permission_mode are Claude-specific concepts
    and are intentionally ignored by this runner.
    """

    provider_name = "copilot"
    supports_file_tools = False

    def __init__(
        self,
        *,
        allowed_tools: list[str] | None = None,  # ignored, copilot-native
        permission_mode: str | None = None,        # ignored, copilot-native
        **_kwargs: object,
    ) -> None:
        pass

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
                result = subprocess.run(
                    ["copilot", prompt],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    return result.stdout
                if logger:
                    logger.warn(
                        f"copilot exited with code {result.returncode} (attempt {attempt})"
                    )
                if result.stderr and logger:
                    logger.warn(f"stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                if logger:
                    logger.warn(f"copilot timed out after {timeout}s (attempt {attempt})")
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "'copilot' CLI not found in PATH. Install GitHub Copilot CLI first."
                ) from exc

            if attempt < max_retries:
                backoff = 2**attempt
                if logger:
                    logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        raise RuntimeError(f"copilot failed after {max_retries} attempts")


def apply_text_output(output: str, logger: Logger | None = None) -> None:
    """Apply Agent A text output (markdown code blocks) to working directory.

    Parses fenced code blocks with a file path comment on the first line:
        ```python
        # path/to/file.py
        <code>
        ```
    or with filename in the fence:
        ```python path/to/file.py
        <code>
        ```

    Falls back to attempting git apply if output looks like a unified diff.
    """
    applied = _apply_code_blocks(output, logger=logger)
    if not applied:
        _try_git_apply(output, logger=logger)


def _apply_code_blocks(output: str, logger: Logger | None = None) -> bool:
    """Parse and apply markdown fenced code blocks. Returns True if any block was applied."""
    # Match: ```lang path/to/file or ```lang\n# path/to/file
    fence_with_path = re.compile(
        r"```[^\n]*?(\S+\.[a-zA-Z0-9]+)\n(.*?)```",
        re.DOTALL,
    )
    comment_path = re.compile(r"^(?:#|//)\s*(\S+\.[a-zA-Z0-9]+)\s*$", re.MULTILINE)

    applied = False
    for match in fence_with_path.finditer(output):
        file_path = match.group(1)
        content = match.group(2)

        # Skip if it looks like a URL or shell command
        if file_path.startswith(("http", "npm", "pip", "brew")):
            continue

        path = Path(file_path)
        # Only apply if path looks like a relative source file
        if ".." in path.parts or path.is_absolute():
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if logger:
            logger.info(f"[copilot] Applied code block → {file_path}")
        applied = True

    if not applied:
        # Try comment-style path annotation on first line of each block
        block_pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
        for block_match in block_pattern.finditer(output):
            block_content = block_match.group(1)
            path_match = comment_path.match(block_content)
            if path_match:
                file_path = path_match.group(1)
                code = block_content[path_match.end():].lstrip("\n")
                path = Path(file_path)
                if ".." not in path.parts and not path.is_absolute():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(code)
                    if logger:
                        logger.info(f"[copilot] Applied code block → {file_path}")
                    applied = True

    return applied


def _try_git_apply(output: str, logger: Logger | None = None) -> None:
    """Attempt git apply if output looks like a unified diff."""
    if not (output.startswith("diff --git") or output.startswith("---")):
        return

    try:
        result = subprocess.run(
            ["git", "apply", "--check"],
            input=output,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "apply"],
                input=output,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            if logger:
                logger.info("[copilot] Applied output as git patch")
        elif logger:
            logger.warn("[copilot] Output looked like a diff but git apply --check failed")
    except (subprocess.SubprocessError, FileNotFoundError):
        if logger:
            logger.warn("[copilot] git apply failed, output not applied to working directory")
