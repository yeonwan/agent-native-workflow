"""Tests for ClaudeCodeRunner command format, session behaviour, and stream-json parsing.

Mirrors test_copilot_runner.py so the two runners stay in sync.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

from agent_native_workflow.runners.base import RunResult
from agent_native_workflow.runners.claude import ClaudeCodeRunner

# ── Helpers ──────────────────────────────────────────────────────────────────


def _json_lines(*events: dict) -> list[str]:
    """Encode events as JSON strings (what stream-json emits)."""
    return [json.dumps(e) for e in events]


def _text_delta_event(text: str) -> dict:
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}


def _assistant_event(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool_use_event(name: str) -> dict:
    return {"type": "content_block_start", "content_block": {"type": "tool_use", "name": name}}


def _result_event(text: str) -> dict:
    return {"type": "result", "result": text}


# Default stream: an assistant message with text "ok"
_DEFAULT_STREAM = _json_lines(_assistant_event("ok"))


class _FakeStderr:
    def read(self) -> str:
        return ""


def _make_popen(
    captured: list[list[str]] | None = None,
    returncode: int = 0,
    lines: list[str] | None = None,
):
    """Return a fake Popen class that records the command and streams given lines."""

    class _FakePopen:
        def __init__(self, cmd: list[str], **_kwargs: object) -> None:
            if captured is not None:
                captured.append(cmd)
            self.returncode = returncode
            self.stdout = iter(f"{line}\n" for line in (lines or _DEFAULT_STREAM))
            self.stderr = _FakeStderr()

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    return _FakePopen


def _run_and_capture(runner: ClaudeCodeRunner, prompt: str = "do something") -> list[str]:
    captured: list[list[str]] = []
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen(captured)):
        runner.run(prompt, timeout=10, max_retries=1)
    return captured[0]


# ── Provider properties ──────────────────────────────────────────────────────


def test_claude_runner_provider_name() -> None:
    assert ClaudeCodeRunner().provider_name == "claude"


def test_claude_runner_supports_file_tools() -> None:
    assert ClaudeCodeRunner().supports_file_tools is True


def test_claude_runner_supports_resume() -> None:
    assert ClaudeCodeRunner().supports_resume is True


# ── Command format ───────────────────────────────────────────────────────────


def test_claude_uses_stream_json_output_format() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner())
    assert "--output-format" in cmd
    idx = cmd.index("--output-format")
    assert cmd[idx + 1] == "stream-json"


def test_claude_uses_verbose_flag() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner())
    assert "--verbose" in cmd


def test_claude_uses_p_flag_for_prompt() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(), "implement the feature")
    assert "-p" in cmd
    assert "implement the feature" in cmd


def test_claude_adds_model_flag_when_set() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(model="claude-opus-4-6"))
    assert "--model" in cmd
    assert "claude-opus-4-6" in cmd


def test_claude_omits_model_flag_when_empty() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(model=""))
    assert "--model" not in cmd


def test_claude_adds_permission_mode_flag() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(permission_mode="bypassPermissions"))
    assert "--permission-mode" in cmd
    assert "bypassPermissions" in cmd


def test_claude_omits_permission_mode_when_empty() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(permission_mode=""))
    assert "--permission-mode" not in cmd


def test_claude_adds_allowed_tools() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(allowed_tools=["Read", "Edit", "Bash"]))
    assert "--allowedTools" in cmd
    assert "Read" in cmd
    assert "Edit" in cmd
    assert "Bash" in cmd


def test_claude_omits_allowed_tools_when_empty() -> None:
    cmd = _run_and_capture(ClaudeCodeRunner(allowed_tools=[]))
    assert "--allowedTools" not in cmd


# ── Session management ───────────────────────────────────────────────────────


def test_claude_generates_session_id_on_first_run() -> None:
    """First run (no session_id passed) must use --session-id <uuid>."""
    captured: list[list[str]] = []
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen(captured)):
        ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    cmd = captured[0]
    assert "--session-id" in cmd
    idx = cmd.index("--session-id")
    assert re.match(r"[0-9a-f\-]{36}$", cmd[idx + 1])


def test_claude_uses_resume_when_session_id_given() -> None:
    """Subsequent run with existing session_id must use --resume, not --session-id."""
    captured: list[list[str]] = []
    sid = "existing-session-abc-123"
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen(captured)):
        ClaudeCodeRunner().run("p", session_id=sid, timeout=10, max_retries=1)
    cmd = captured[0]
    assert "--resume" in cmd
    assert sid in cmd
    assert "--session-id" not in cmd


def test_claude_returns_generated_session_id() -> None:
    """Return value must carry the uuid that was generated for --session-id."""
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen()):
        result = ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    assert result.session_id is not None
    assert re.match(r"[0-9a-f\-]{36}$", result.session_id)


def test_claude_returns_provided_session_id() -> None:
    """When caller passes session_id, result carries it back unchanged."""
    sid = "caller-session-id"
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen()):
        result = ClaudeCodeRunner().run("p", session_id=sid, timeout=10, max_retries=1)
    assert result.session_id == sid


def test_claude_same_session_id_reused_across_retries() -> None:
    """If the first attempt fails and retries, the same --session-id must be used."""
    fail_then_pass = [1, 0]
    captured: list[list[str]] = []

    class _Popen:
        def __init__(self, cmd: list[str], **_kw: object) -> None:
            captured.append(cmd)
            self.returncode = fail_then_pass.pop(0)
            self.stdout = iter(f"{line}\n" for line in _DEFAULT_STREAM)
            self.stderr = _FakeStderr()

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _Popen):
        with patch("agent_native_workflow.runners.claude.time.sleep"):
            ClaudeCodeRunner().run("p", timeout=10, max_retries=2)

    assert len(captured) == 2
    sid_attempt1 = captured[0][captured[0].index("--session-id") + 1]
    sid_attempt2 = captured[1][captured[1].index("--session-id") + 1]
    assert sid_attempt1 == sid_attempt2


# ── Output streaming (stream-json parsing) ───────────────────────────────────


def test_claude_parses_assistant_text_from_stream() -> None:
    """Assistant message events are collected as the final output text."""
    lines = _json_lines(_assistant_event("hello world"))
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        result = ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    assert result.output == "hello world"


def test_claude_streams_text_deltas_to_on_output() -> None:
    """content_block_delta text_delta events fire on_output in real-time."""
    received: list[str] = []
    lines = _json_lines(
        _text_delta_event("hel"),
        _text_delta_event("lo"),
        _assistant_event("hello"),
    )
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        ClaudeCodeRunner().run("p", timeout=10, max_retries=1, on_output=received.append)
    assert received == ["hel", "lo"]


def test_claude_streams_tool_use_to_on_output() -> None:
    """Tool use start events show the tool name via on_output."""
    received: list[str] = []
    lines = _json_lines(
        _tool_use_event("Read"),
        _tool_use_event("Edit"),
        _assistant_event("done"),
    )
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        ClaudeCodeRunner().run("p", timeout=10, max_retries=1, on_output=received.append)
    assert "→ Read" in received
    assert "→ Edit" in received


def test_claude_result_event_fallback() -> None:
    """If no assistant message, result event text is used as output."""
    lines = _json_lines(_result_event("fallback output"))
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        result = ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    assert result.output == "fallback output"


def test_claude_handles_non_json_lines_gracefully() -> None:
    """Non-JSON lines (e.g. CLI warnings) are forwarded as plain text."""
    received: list[str] = []
    lines = ["WARNING: some cli warning", json.dumps(_assistant_event("ok"))]
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        ClaudeCodeRunner().run("p", timeout=10, max_retries=1, on_output=received.append)
    assert "WARNING: some cli warning" in received


def test_claude_works_without_on_output_callback() -> None:
    lines = _json_lines(_assistant_event("some output"))
    FakePopen = _make_popen(lines=lines)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        result = ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    assert "some output" in result.output


# ── Error handling ───────────────────────────────────────────────────────────


def test_claude_raises_when_binary_missing() -> None:
    with patch(
        "agent_native_workflow.runners.claude.subprocess.Popen",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(RuntimeError, match="claude.*CLI not found"):
            ClaudeCodeRunner().run("p", timeout=10, max_retries=1)


def test_claude_raises_after_all_retries_exhausted() -> None:
    FakePopen = _make_popen(returncode=1)
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", FakePopen):
        with patch("agent_native_workflow.runners.claude.time.sleep"):
            with pytest.raises(RuntimeError, match="claude failed after 2 attempts"):
                ClaudeCodeRunner().run("p", timeout=10, max_retries=2)


def test_claude_returns_run_result_type() -> None:
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_popen()):
        result = ClaudeCodeRunner().run("p", timeout=10, max_retries=1)
    assert isinstance(result, RunResult)
