from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import RunResult


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    """Terminate the Claude process tree so resume sessions do not linger."""
    pid = getattr(proc, "pid", None)
    if os.name != "nt" and pid is not None:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        return


def _stream_stdout(
    proc: subprocess.Popen[str],
    text_parts: list[str],
    on_output: Callable[[str], None] | None,
) -> None:
    """Parse stream-json NDJSON from Claude CLI, dispatching to on_output.

    Each stdout line is a JSON object.  We extract:
    - ``text_delta`` events  → real-time text for the live panel + accumulated output
    - ``tool_use`` starts    → show which tool the agent is invoking
    - ``result`` event       → final text (fallback if deltas were missed)

    Non-JSON lines (e.g. stderr leaking into stdout) are forwarded as-is.
    """
    if proc.stdout is None:
        return
    stream_state = {"saw_partial_text": False}
    for raw_line in proc.stdout:
        raw_line = raw_line.rstrip("\n")
        if not raw_line:
            continue

        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            # Not JSON — forward as plain text (e.g. early CLI warnings)
            text_parts.append(raw_line)
            if on_output:
                on_output(raw_line)
            continue

        _dispatch_event(event, text_parts, on_output, stream_state)


def _dispatch_event(
    event: dict[str, Any],
    text_parts: list[str],
    on_output: Callable[[str], None] | None,
    stream_state: dict[str, bool],
) -> None:
    """Route a single parsed JSON event."""
    # Newer Claude CLI stream-json wraps incremental events inside:
    # {"type": "stream_event", "event": {...}}
    if event.get("type") == "stream_event":
        inner = event.get("event")
        if isinstance(inner, dict):
            _dispatch_event(inner, text_parts, on_output, stream_state)
        return

    etype = event.get("type", "")

    # --- assistant text delta (real-time tokens) ---
    if etype == "assistant":
        # Complete assistant message — extract text blocks for the final output
        assistant_text: list[str] = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    assistant_text.append(text)
        if assistant_text:
            full_text = "".join(assistant_text)
            text_parts.append(full_text)
            if on_output and not stream_state["saw_partial_text"]:
                on_output(full_text)
        return

    if etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text", "")
            if text and on_output:
                stream_state["saw_partial_text"] = True
                on_output(text)
        return

    # --- tool use start (show the tool name in the live panel) ---
    if etype == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            name = block.get("name", "unknown")
            if on_output:
                on_output(f"→ {name}")
        return

    # --- final result (fallback for the complete output) ---
    if etype == "result":
        result = event.get("result", "")
        if result and not text_parts:
            text_parts.append(result)
            if on_output and not stream_state["saw_partial_text"]:
                on_output(result)
        return


class ClaudeCodeRunner:
    """Runner using Claude Code CLI (claude).

    Supports autonomous file editing, so supports_file_tools = True.
    Session: first call uses ``--session-id <uuid>``; later calls use ``--resume <id>``.

    Uses ``--output-format stream-json --verbose`` so that agent output is
    streamed in real-time via the ``on_output`` callback.
    """

    provider_name = "claude"
    supports_file_tools = True
    supports_resume = True

    def __init__(
        self,
        *,
        model: str = "",
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
        permission_mode: str = "bypassPermissions",
        **_kwargs: object,
    ) -> None:
        self._model = model
        self._allowed_tools = allowed_tools or []
        self._denied_tools = denied_tools or []
        self._permission_mode = permission_mode

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 600,
        max_retries: int = 2,
        logger: Logger | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> RunResult:
        new_session_id: str | None = None

        for attempt in range(1, max_retries + 1):
            try:
                cmd = [
                    "claude",
                    "-p", prompt,
                    "--output-format", "stream-json",
                    "--verbose",
                    "--include-partial-messages",
                ]

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
                if self._denied_tools:
                    cmd.extend(["--disallowedTools", *self._denied_tools])

                if self._model:
                    cmd.extend(["--model", self._model])

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=(os.name != "nt"),
                )

                text_parts: list[str] = []
                reader = threading.Thread(
                    target=_stream_stdout,
                    args=(proc, text_parts, on_output),
                    daemon=True,
                )
                reader.start()

                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _terminate_process(proc)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    reader.join(timeout=5)
                    if logger:
                        logger.warn(f"claude timed out after {timeout}s (attempt {attempt})")
                    if attempt < max_retries:
                        backoff = 2**attempt
                        if logger:
                            logger.info(f"Retrying in {backoff}s...")
                        time.sleep(backoff)
                    continue

                reader.join(timeout=5)
                full_output = "".join(text_parts)

                if proc.returncode == 0:
                    sid = session_id if session_id is not None else new_session_id
                    return RunResult(output=full_output, session_id=sid)
                if logger:
                    logger.warn(f"claude exited with code {proc.returncode} (attempt {attempt})")
                    stderr_out = proc.stderr.read() if proc.stderr else ""
                    if stderr_out:
                        logger.warn(f"stderr: {stderr_out[:500]}")

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
