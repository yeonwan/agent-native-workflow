from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import RunResult

_SHARE_FILE = Path(".agent-native-workflow/copilot-session.md")


def _stream_stdout(
    proc: subprocess.Popen[str],
    output_lines: list[str],
    on_output: Callable[[str], None] | None,
) -> None:
    """Read proc.stdout line by line, appending to output_lines and calling on_output."""
    if proc.stdout is None:
        return
    for line in proc.stdout:
        stripped = line.rstrip("\n")
        output_lines.append(stripped)
        if on_output:
            on_output(stripped)


class GitHubCopilotRunner:
    """Runner using GitHub Copilot CLI (copilot).

    Session persistence: ``--share .agent-native-workflow/copilot-session.md``
    writes a session markdown file after each run; the session ID is parsed
    from it and passed back via ``RunResult.session_id``.  Subsequent calls
    pass ``--resume <session_id>`` to continue the same session.

    Note: permission_mode is a Claude-specific concept and is intentionally
    ignored by this runner.
    """

    provider_name = "copilot"
    supports_file_tools = True
    supports_resume = True

    def __init__(
        self,
        *,
        model: str = "",
        allowed_tools: list[str] | None = None,
        permission_mode: str | None = None,  # ignored, copilot-native
        **_kwargs: object,
    ) -> None:
        self._model = model
        self._allowed_tools = list(allowed_tools or [])

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger: Logger | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> RunResult:
        for attempt in range(1, max_retries + 1):
            try:
                cmd = ["copilot", "--prompt", prompt]
                cmd.extend(["--share", str(_SHARE_FILE)])

                if session_id:
                    cmd.extend(["--resume", session_id])

                if self._model:
                    cmd.extend(["--model", self._model])

                for tool in self._allowed_tools:
                    cmd.append(f"--allow-tool={tool}")

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                output_lines: list[str] = []
                reader = threading.Thread(
                    target=_stream_stdout,
                    args=(proc, output_lines, on_output),
                    daemon=True,
                )
                reader.start()

                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    reader.join(timeout=5)
                    if logger:
                        logger.warn(f"copilot timed out after {timeout}s (attempt {attempt})")
                    if attempt < max_retries:
                        backoff = 2**attempt
                        if logger:
                            logger.info(f"Retrying in {backoff}s...")
                        time.sleep(backoff)
                    continue

                reader.join(timeout=5)
                full_output = "\n".join(output_lines)

                if proc.returncode == 0:
                    parsed_id = _parse_session_id(_SHARE_FILE)
                    return RunResult(output=full_output, session_id=parsed_id)
                if logger:
                    logger.warn(f"copilot exited with code {proc.returncode} (attempt {attempt})")
                    stderr_out = proc.stderr.read() if proc.stderr else ""
                    if stderr_out:
                        logger.warn(f"stderr: {stderr_out[:500]}")

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


def _parse_session_id(share_file: Path) -> str | None:
    """Extract session ID from a copilot --share markdown file."""
    if not share_file.is_file():
        return None
    m = re.search(r"\*\*Session ID:\*\*\s*`([a-f0-9-]+)`", share_file.read_text())
    return m.group(1) if m else None


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
                code = block_content[path_match.end() :].lstrip("\n")
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
