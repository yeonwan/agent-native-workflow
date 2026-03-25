"""Tests for GitHubCopilotRunner command format and domain copilot tool definitions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_native_workflow.domain import agent_config_for
from agent_native_workflow.runners.base import RunResult
from agent_native_workflow.runners.copilot import GitHubCopilotRunner, _parse_session_id

# ── GitHubCopilotRunner properties ───────────────────────────────────────────


def test_copilot_runner_supports_resume() -> None:
    runner = GitHubCopilotRunner()
    assert runner.supports_resume is True


def test_copilot_runner_supports_file_tools() -> None:
    runner = GitHubCopilotRunner()
    assert runner.supports_file_tools is True


# ── Command format ────────────────────────────────────────────────────────────


def _run_and_capture(runner: GitHubCopilotRunner, prompt: str = "do something") -> list[str]:
    """Run the runner with a mocked subprocess and return the captured command."""
    captured: list[list[str]] = []

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "ok"

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return fake_result

    with patch("agent_native_workflow.runners.copilot.subprocess.run", side_effect=fake_run):
        runner.run(prompt, timeout=10, max_retries=1)

    return captured[0]


def test_copilot_uses_prompt_flag() -> None:
    runner = GitHubCopilotRunner()
    cmd = _run_and_capture(runner, "implement the feature")
    assert "--prompt" in cmd
    assert "implement the feature" in cmd
    # Must NOT pass prompt as positional argument
    assert cmd[1] == "--prompt"


def test_copilot_adds_model_flag_when_set() -> None:
    runner = GitHubCopilotRunner(model="gpt-4o")
    cmd = _run_and_capture(runner)
    assert "--model" in cmd
    assert "gpt-4o" in cmd


def test_copilot_omits_model_flag_when_empty() -> None:
    runner = GitHubCopilotRunner(model="")
    cmd = _run_and_capture(runner)
    assert "--model" not in cmd


def test_copilot_adds_allow_tool_for_shell_tools() -> None:
    runner = GitHubCopilotRunner(allowed_tools=["shell(pytest:*)", "shell(git:status)"])
    cmd = _run_and_capture(runner)
    assert "--allow-tool=shell(pytest:*)" in cmd
    assert "--allow-tool=shell(git:status)" in cmd


def test_copilot_filters_out_non_shell_tools() -> None:
    runner = GitHubCopilotRunner(
        allowed_tools=["Read", "Edit", "Write", "Grep", "Glob", "shell(uv:*)"]
    )
    cmd = _run_and_capture(runner)
    assert "--allow-tool=shell(uv:*)" in cmd
    # Claude-specific tools must not appear
    for tool in ("Read", "Edit", "Write", "Grep", "Glob"):
        assert tool not in cmd


def test_copilot_returns_none_session_id_when_share_file_missing(tmp_path) -> None:
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "output"

    missing = tmp_path / "no-such-file.md"
    with patch("agent_native_workflow.runners.copilot.subprocess.run", return_value=fake_result):
        with patch("agent_native_workflow.runners.copilot._SHARE_FILE", missing):
            runner = GitHubCopilotRunner()
            result = runner.run("prompt", timeout=10, max_retries=1)

    assert isinstance(result, RunResult)
    assert result.session_id is None


def test_copilot_returns_parsed_session_id(tmp_path) -> None:
    share_file = tmp_path / "copilot-session.md"
    share_file.write_text(
        "# 🤖 Copilot CLI Session\n\n"
        "> [!NOTE]\n"
        "> - **Session ID:** `90579805-92ca-444a-b34c-603fab1111ff`  \n"
        "> - **Started:** 2026-03-24, 11:15:09 p.m.  \n"
    )

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "output"

    with patch("agent_native_workflow.runners.copilot.subprocess.run", return_value=fake_result):
        with patch("agent_native_workflow.runners.copilot._SHARE_FILE", share_file):
            runner = GitHubCopilotRunner()
            result = runner.run("prompt", timeout=10, max_retries=1)

    assert result.session_id == "90579805-92ca-444a-b34c-603fab1111ff"


def test_copilot_adds_share_flag() -> None:
    runner = GitHubCopilotRunner()
    cmd = _run_and_capture(runner)
    assert "--share" in cmd


def test_copilot_adds_resume_flag_when_session_id_given() -> None:
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "output"
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return fake_result

    sid = "abc-123"
    with patch("agent_native_workflow.runners.copilot.subprocess.run", side_effect=fake_run):
        GitHubCopilotRunner().run("prompt", session_id=sid, timeout=10, max_retries=1)

    cmd = captured[0]
    assert "--resume" in cmd
    assert sid in cmd


def test_copilot_omits_resume_flag_when_no_session_id() -> None:
    runner = GitHubCopilotRunner()
    cmd = _run_and_capture(runner)
    assert "--resume" not in cmd


# ── _parse_session_id ─────────────────────────────────────────────────────────


def test_parse_session_id_returns_id(tmp_path: Path) -> None:
    f = tmp_path / "session.md"
    f.write_text("- **Session ID:** `deadbeef-1234-5678-abcd-000000000000`\n")
    assert _parse_session_id(f) == "deadbeef-1234-5678-abcd-000000000000"


def test_parse_session_id_returns_none_when_missing(tmp_path: Path) -> None:
    assert _parse_session_id(tmp_path / "no-file.md") is None


def test_parse_session_id_returns_none_when_no_match(tmp_path: Path) -> None:
    f = tmp_path / "session.md"
    f.write_text("# no session info here\n")
    assert _parse_session_id(f) is None


def test_copilot_ignores_permission_mode() -> None:
    # permission_mode is a Claude Code concept — must not raise and must not appear in cmd
    runner = GitHubCopilotRunner(permission_mode="bypassPermissions")
    cmd = _run_and_capture(runner)
    assert "bypassPermissions" not in cmd
    assert "--permission-mode" not in cmd


def test_copilot_raises_when_binary_missing() -> None:
    runner = GitHubCopilotRunner()
    with patch(
        "agent_native_workflow.runners.copilot.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(RuntimeError, match="copilot.*CLI not found"):
            runner.run("prompt", timeout=10, max_retries=1)


# ── domain.py copilot tool definitions ───────────────────────────────────────


def test_agent_config_for_copilot_agent_a_has_file_and_shell_tools() -> None:
    cfg = agent_config_for("python", cli_provider="copilot")
    tools = cfg.agent_a.allowed_tools
    # File operation tools
    assert "read" in tools
    assert "write" in tools
    assert "edit" in tools
    assert "grep" in tools
    assert "glob" in tools
    # Shell tools
    assert any(t.startswith("shell(") for t in tools)
    # No Claude-specific capitalized tools
    assert "Read" not in tools
    assert "Edit" not in tools
    assert "Write" not in tools


def test_agent_config_for_copilot_agent_r_has_read_and_shell_tools() -> None:
    cfg = agent_config_for("python", cli_provider="copilot")
    tools = cfg.agent_r.allowed_tools
    assert "read" in tools
    assert "grep" in tools
    assert any(t.startswith("shell(git:") for t in tools)


def test_agent_config_for_copilot_has_no_permission_mode() -> None:
    cfg = agent_config_for("python", cli_provider="copilot")
    assert cfg.agent_a.permission_mode == ""
    assert cfg.agent_r.permission_mode == ""


def test_agent_config_for_copilot_omits_permission_mode_in_yaml(tmp_path) -> None:
    cfg = agent_config_for("python", cli_provider="copilot")
    out = tmp_path / "agent-config.yaml"
    cfg.save(out)
    text = out.read_text()
    assert "permission_mode" not in text


def test_agent_config_for_claude_still_uses_bash_tools() -> None:
    cfg = agent_config_for("python", cli_provider="claude")
    tools = cfg.agent_a.allowed_tools
    assert any(t.startswith("Bash(") for t in tools)
    assert "Read" in tools
    assert "Edit" in tools


def test_agent_config_for_copilot_includes_build_tools_for_project_type() -> None:
    py_cfg = agent_config_for("python", cli_provider="copilot")
    assert "shell(pytest:*)" in py_cfg.agent_a.allowed_tools

    node_cfg = agent_config_for("node", cli_provider="copilot")
    assert "shell(npm:*)" in node_cfg.agent_a.allowed_tools
