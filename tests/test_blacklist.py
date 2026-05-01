"""Tests for blacklist permission model.

Covers: domain deny lists, runner CLI flag wiring, config resolution,
pipeline audit, and agent_config_for() blacklist mode.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_native_workflow.domain import (
    AgentConfig,
    AgentPermissions,
    agent_config_for,
    default_denied_tools,
)
from agent_native_workflow.runners.claude import ClaudeCodeRunner
from agent_native_workflow.runners.copilot import GitHubCopilotRunner
from agent_native_workflow.runners.factory import runner_for


# ── default_denied_tools() ───────────────────────────────────────────────────


def test_default_denied_claude_not_empty() -> None:
    denied = default_denied_tools("claude")
    assert len(denied) > 10


def test_default_denied_copilot_not_empty() -> None:
    denied = default_denied_tools("copilot")
    assert len(denied) > 10


def test_default_denied_codex_empty() -> None:
    assert default_denied_tools("codex") == []


def test_default_denied_cursor_empty() -> None:
    assert default_denied_tools("cursor") == []


def test_default_denied_unknown_provider_empty() -> None:
    assert default_denied_tools("nonexistent") == []


def test_default_denied_returns_copy() -> None:
    a = default_denied_tools("claude")
    b = default_denied_tools("claude")
    assert a == b
    a.append("extra")
    assert "extra" not in default_denied_tools("claude")


# ── Deny list content ────────────────────────────────────────────────────────


def test_claude_denied_blocks_git_write_ops() -> None:
    denied = default_denied_tools("claude")
    assert "Bash(git commit:*)" in denied
    assert "Bash(git push:*)" in denied
    assert "Bash(git reset:*)" in denied


def test_claude_denied_blocks_destructive_ops() -> None:
    denied = default_denied_tools("claude")
    assert "Bash(rm:*)" in denied
    assert "Bash(rmdir:*)" in denied


def test_claude_denied_blocks_network() -> None:
    denied = default_denied_tools("claude")
    assert "Bash(curl:*)" in denied
    assert "Bash(wget:*)" in denied
    assert "Bash(ssh:*)" in denied


def test_claude_denied_blocks_privilege_escalation() -> None:
    denied = default_denied_tools("claude")
    assert "Bash(sudo:*)" in denied
    assert "Bash(chmod:*)" in denied
    assert "Bash(chown:*)" in denied


def test_claude_denied_blocks_env_manipulation() -> None:
    denied = default_denied_tools("claude")
    assert "Bash(env:*)" in denied
    assert "Bash(export:*)" in denied


def test_claude_denied_does_not_block_git_branch_read() -> None:
    """git branch (list) should NOT be denied — only -d/-D."""
    denied = default_denied_tools("claude")
    assert "Bash(git branch:*)" not in denied
    assert "Bash(git branch -d:*)" in denied
    assert "Bash(git branch -D:*)" in denied


def test_copilot_denied_uses_shell_prefix_no_colon() -> None:
    """Copilot patterns must use shell() without colon — not shell(:*)."""
    denied = default_denied_tools("copilot")
    for pattern in denied:
        assert pattern.startswith("shell("), f"Bad prefix: {pattern}"
        assert ":*)" not in pattern, f"Colon in copilot pattern: {pattern}"


def test_claude_denied_uses_bash_prefix_with_colon() -> None:
    """Claude patterns must use Bash() with colon:glob."""
    denied = default_denied_tools("claude")
    for pattern in denied:
        assert pattern.startswith("Bash("), f"Bad prefix: {pattern}"
        assert ":*)" in pattern, f"Missing colon glob: {pattern}"


def test_claude_copilot_deny_lists_same_length() -> None:
    """Both lists should cover the same categories."""
    assert len(default_denied_tools("claude")) == len(default_denied_tools("copilot"))


# ── agent_config_for() blacklist mode ────────────────────────────────────────


def test_agent_config_for_blacklist_claude_agent_a_has_denied_tools() -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="blacklist")
    assert cfg.agent_a.denied_tools == default_denied_tools("claude")
    # Broad categories allow all tools; deny list carves out exceptions
    assert "Bash" in cfg.agent_a.allowed_tools
    assert "Read" in cfg.agent_a.allowed_tools


def test_agent_config_for_blacklist_copilot_agent_a_has_denied_tools() -> None:
    cfg = agent_config_for("python", "copilot", permission_strategy="blacklist")
    assert cfg.agent_a.denied_tools == default_denied_tools("copilot")
    assert "shell" in cfg.agent_a.allowed_tools
    assert "read" in cfg.agent_a.allowed_tools


def test_agent_config_for_blacklist_agent_r_keeps_whitelist() -> None:
    """Verification agents must stay on whitelist regardless of strategy."""
    cfg = agent_config_for("python", "claude", permission_strategy="blacklist")
    assert cfg.agent_r.allowed_tools != []
    assert cfg.agent_r.denied_tools == []


def test_agent_config_for_blacklist_agent_b_keeps_whitelist() -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="blacklist")
    assert cfg.agent_b.allowed_tools != []
    assert cfg.agent_b.denied_tools == []


def test_agent_config_for_blacklist_agent_c_keeps_whitelist() -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="blacklist")
    assert cfg.agent_c.allowed_tools != []
    assert cfg.agent_c.denied_tools == []


def test_agent_config_for_whitelist_has_no_denied_tools() -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="whitelist")
    assert cfg.agent_a.denied_tools == []
    assert cfg.agent_a.allowed_tools != []
    # Whitelist should have specific tool patterns, not broad "Bash"
    assert "Bash" not in cfg.agent_a.allowed_tools


def test_agent_config_for_blacklist_codex_empty_denied() -> None:
    """Codex has no deny flag support — denied_tools should be empty."""
    cfg = agent_config_for("python", "codex", permission_strategy="blacklist")
    assert cfg.agent_a.denied_tools == []


# ── ClaudeCodeRunner: --disallowedTools flag ─────────────────────────────────


class _FakeStderr:
    def read(self) -> str:
        return ""


def _claude_stream():
    import json
    return [json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}})]


def _make_claude_popen(captured: list[list[str]] | None = None):
    class _FakePopen:
        def __init__(self, cmd: list[str], **_kw: object) -> None:
            if captured is not None:
                captured.append(cmd)
            self.returncode = 0
            self.pid = 12345
            self.stdout = iter(f"{line}\n" for line in _claude_stream())
            self.stderr = _FakeStderr()

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    return _FakePopen


def _run_claude_and_capture(runner: ClaudeCodeRunner) -> list[str]:
    captured: list[list[str]] = []
    with patch("agent_native_workflow.runners.claude.subprocess.Popen", _make_claude_popen(captured)):
        runner.run("test", timeout=10, max_retries=1)
    return captured[0]


def test_claude_adds_disallowed_tools_flag() -> None:
    runner = ClaudeCodeRunner(denied_tools=["Bash(rm:*)", "Bash(curl:*)"])
    cmd = _run_claude_and_capture(runner)
    assert "--disallowedTools" in cmd
    assert "Bash(rm:*)" in cmd
    assert "Bash(curl:*)" in cmd


def test_claude_omits_disallowed_tools_when_empty() -> None:
    runner = ClaudeCodeRunner(denied_tools=[])
    cmd = _run_claude_and_capture(runner)
    assert "--disallowedTools" not in cmd


def test_claude_both_allowed_and_denied_coexist() -> None:
    """if/if pattern: both flags should appear when both are set."""
    runner = ClaudeCodeRunner(
        allowed_tools=["Read", "Edit"],
        denied_tools=["Bash(rm:*)"],
    )
    cmd = _run_claude_and_capture(runner)
    assert "--allowedTools" in cmd
    assert "--disallowedTools" in cmd


def test_claude_denied_only_no_allowed() -> None:
    """Blacklist mode: denied_tools set, allowed_tools empty."""
    runner = ClaudeCodeRunner(allowed_tools=[], denied_tools=["Bash(rm:*)"])
    cmd = _run_claude_and_capture(runner)
    assert "--allowedTools" not in cmd
    assert "--disallowedTools" in cmd


# ── GitHubCopilotRunner: --deny-tool flag ────────────────────────────────────


def _make_copilot_popen(captured: list[list[str]] | None = None):
    class _FakePopen:
        def __init__(self, cmd: list[str], **_kw: object) -> None:
            if captured is not None:
                captured.append(cmd)
            self.returncode = 0
            self.stdout = iter(["ok\n"])
            self.stderr = _FakeStderr()

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    return _FakePopen


def _run_copilot_and_capture(runner: GitHubCopilotRunner) -> list[str]:
    captured: list[list[str]] = []
    with patch("agent_native_workflow.runners.copilot.subprocess.Popen", _make_copilot_popen(captured)):
        runner.run("test", timeout=10, max_retries=1)
    return captured[0]


def test_copilot_adds_deny_tool_flags() -> None:
    runner = GitHubCopilotRunner(denied_tools=["shell(rm)", "shell(curl)"])
    cmd = _run_copilot_and_capture(runner)
    assert "--deny-tool=shell(rm)" in cmd
    assert "--deny-tool=shell(curl)" in cmd


def test_copilot_omits_deny_tool_when_empty() -> None:
    runner = GitHubCopilotRunner(denied_tools=[])
    cmd = _run_copilot_and_capture(runner)
    deny_flags = [c for c in cmd if c.startswith("--deny-tool")]
    assert deny_flags == []


def test_copilot_both_allow_and_deny_coexist() -> None:
    runner = GitHubCopilotRunner(
        allowed_tools=["shell(pytest:*)"],
        denied_tools=["shell(rm)"],
    )
    cmd = _run_copilot_and_capture(runner)
    assert "--allow-tool=shell(pytest:*)" in cmd
    assert "--deny-tool=shell(rm)" in cmd


def test_copilot_allow_all_tools_fallback_when_no_lists() -> None:
    """No allow or deny → --allow-all-tools."""
    runner = GitHubCopilotRunner(allowed_tools=[], denied_tools=[])
    cmd = _run_copilot_and_capture(runner)
    assert "--allow-all-tools" in cmd


def test_copilot_no_allow_all_when_deny_set() -> None:
    """With deny list, --allow-all-tools must NOT appear."""
    runner = GitHubCopilotRunner(denied_tools=["shell(rm)"])
    cmd = _run_copilot_and_capture(runner)
    assert "--allow-all-tools" not in cmd


# ── runner_for factory: denied_tools pass-through ────────────────────────────


def test_runner_for_passes_denied_tools_to_claude() -> None:
    denied = ["Bash(rm:*)"]
    runner = runner_for("claude", denied_tools=denied)
    assert isinstance(runner, ClaudeCodeRunner)
    assert runner._denied_tools == denied


def test_runner_for_passes_denied_tools_to_copilot() -> None:
    denied = ["shell(rm)"]
    runner = runner_for("copilot", denied_tools=denied)
    assert isinstance(runner, GitHubCopilotRunner)
    assert runner._denied_tools == denied


def test_runner_for_codex_ignores_denied_tools() -> None:
    """Codex runner accepts but ignores denied_tools (via **_kwargs)."""
    runner = runner_for("codex", denied_tools=["Bash(rm:*)"])
    assert not hasattr(runner, "_denied_tools")


def test_runner_for_cursor_ignores_denied_tools() -> None:
    runner = runner_for("cursor", denied_tools=["Bash(rm:*)"])
    assert not hasattr(runner, "_denied_tools")


# ── Config: blacklist inferred from denied_tools ─────────────────────────────


def test_config_blacklist_inferred_when_denied_tools_present(tmp_path: Path) -> None:
    """No permission-strategy field needed — denied_tools presence = blacklist."""
    from agent_native_workflow.config import WorkflowConfig
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "agents:\n"
        "  agent_a:\n"
        "    denied_tools:\n"
        '      - "Bash(rm:*)"\n'
    )
    cfg = WorkflowConfig.resolve(project_root=tmp_path)
    assert cfg.agent_config is not None
    assert cfg.agent_config.agent_a.denied_tools == ["Bash(rm:*)"]


def test_config_whitelist_when_no_denied_tools(tmp_path: Path) -> None:
    from agent_native_workflow.config import WorkflowConfig
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "agents:\n"
        "  agent_a:\n"
        "    allowed_tools:\n"
        "      - Read\n"
    )
    cfg = WorkflowConfig.resolve(project_root=tmp_path)
    assert cfg.agent_config is not None
    assert cfg.agent_config.agent_a.denied_tools == []
    assert cfg.agent_config.agent_a.allowed_tools == ["Read"]


# ── Config: denied_tools in agent config ─────────────────────────────────────


def test_config_loads_denied_tools_from_embedded_yaml(tmp_path: Path) -> None:
    from agent_native_workflow.config import WorkflowConfig
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "agents:\n"
        "  agent_a:\n"
        "    denied_tools:\n"
        '      - "Bash(rm:*)"\n'
        '      - "Bash(curl:*)"\n'
    )
    agent_cfg = WorkflowConfig.load_embedded_agent_config(tmp_path)
    assert agent_cfg is not None
    assert "Bash(rm:*)" in agent_cfg.agent_a.denied_tools
    assert "Bash(curl:*)" in agent_cfg.agent_a.denied_tools


# ── AgentConfig YAML serialization ───────────────────────────────────────────


def test_agent_config_save_includes_denied_tools(tmp_path: Path) -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="blacklist")
    out = tmp_path / "agent-config.yaml"
    cfg.save(out)
    text = out.read_text()
    assert "denied_tools:" in text
    assert "Bash(rm:*)" in text


def test_agent_config_save_whitelist_no_denied_section(tmp_path: Path) -> None:
    cfg = agent_config_for("python", "claude", permission_strategy="whitelist")
    out = tmp_path / "agent-config.yaml"
    cfg.save(out)
    text = out.read_text()
    assert "denied_tools:" not in text
    assert "allowed_tools:" in text


# ── Audit helpers ────────────────────────────────────────────────────────────


def test_audit_detects_unauthorized_commit() -> None:
    from agent_native_workflow.pipeline import _audit_post_phase1, _get_head_hash
    from agent_native_workflow.log import Logger

    warnings: list[str] = []

    class _FakeLogger:
        def warn(self, msg: str) -> None:
            warnings.append(msg)
        def info(self, msg: str) -> None:
            pass

    with patch(
        "agent_native_workflow.pipeline._get_head_hash",
        return_value="deadbeef12345678",
    ), patch(
        "agent_native_workflow.pipeline.snapshot_working_tree",
        return_value={},
    ), patch(
        "agent_native_workflow.pipeline._sp.run",
    ) as mock_run:
        _audit_post_phase1("abcd1234original", {}, _FakeLogger())  # type: ignore[arg-type]

    assert any("Unauthorized commit" in w for w in warnings)
    # Should have called git reset --soft
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "git" in args
    assert "reset" in args
    assert "--soft" in args


def test_audit_detects_file_deletion() -> None:
    from agent_native_workflow.pipeline import _audit_post_phase1

    warnings: list[str] = []

    class _FakeLogger:
        def warn(self, msg: str) -> None:
            warnings.append(msg)
        def info(self, msg: str) -> None:
            pass

    before = {"src/app.py": "hash1"}
    after = {}  # file disappeared from git status

    with patch(
        "agent_native_workflow.pipeline._get_head_hash",
        return_value="same_hash",
    ), patch(
        "agent_native_workflow.pipeline.snapshot_working_tree",
        return_value=after,
    ):
        _audit_post_phase1("same_hash", before, _FakeLogger())  # type: ignore[arg-type]

    assert any("deletion" in w for w in warnings)


def test_audit_detects_sensitive_file_modification() -> None:
    from agent_native_workflow.pipeline import _audit_post_phase1

    warnings: list[str] = []

    class _FakeLogger:
        def warn(self, msg: str) -> None:
            warnings.append(msg)
        def info(self, msg: str) -> None:
            pass

    before: dict[str, str] = {}
    after = {".env": "hash_new"}

    with patch(
        "agent_native_workflow.pipeline._get_head_hash",
        return_value="same_hash",
    ), patch(
        "agent_native_workflow.pipeline.snapshot_working_tree",
        return_value=after,
    ):
        _audit_post_phase1("same_hash", before, _FakeLogger())  # type: ignore[arg-type]

    assert any("Sensitive" in w for w in warnings)
    assert any(".env" in w for w in warnings)


def test_audit_no_warnings_when_clean() -> None:
    from agent_native_workflow.pipeline import _audit_post_phase1

    warnings: list[str] = []

    class _FakeLogger:
        def warn(self, msg: str) -> None:
            warnings.append(msg)
        def info(self, msg: str) -> None:
            pass

    with patch(
        "agent_native_workflow.pipeline._get_head_hash",
        return_value="same_hash",
    ), patch(
        "agent_native_workflow.pipeline.snapshot_working_tree",
        return_value={},
    ):
        _audit_post_phase1("same_hash", {}, _FakeLogger())  # type: ignore[arg-type]

    assert warnings == []
