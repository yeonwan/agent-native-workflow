"""Tests for `anw init` Option-B provider update logic."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_native_workflow.commands.init import _update_cli_provider, cmd_init


def _args(cli: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(cli=cli)


# ── _update_cli_provider ──────────────────────────────────────────────────────


def test_update_cli_provider_replaces_line(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("cli-provider: claude\nverification: review\n")
    _update_cli_provider(cfg, "copilot")
    assert "cli-provider: copilot" in cfg.read_text()


def test_update_cli_provider_preserves_other_settings(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "cli-provider: claude\nverification: triangulation\nlint-cmd: ruff check src\n"
    )
    _update_cli_provider(cfg, "codex")
    text = cfg.read_text()
    assert "cli-provider: codex" in text
    assert "verification: triangulation" in text
    assert "lint-cmd: ruff check src" in text


def test_update_cli_provider_only_changes_first_occurrence(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("cli-provider: claude\n# cli-provider: example comment\n")
    _update_cli_provider(cfg, "copilot")
    lines = cfg.read_text().splitlines()
    assert lines[0] == "cli-provider: copilot"
    assert lines[1] == "# cli-provider: example comment"


# ── cmd_init fresh run ────────────────────────────────────────────────────────


def test_init_creates_config_with_specified_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    cmd_init(_args(cli="copilot"))
    text = (tmp_path / ".agent-native-workflow" / "config.yaml").read_text()
    assert "cli-provider: copilot" in text
    assert "agents:" in text
    assert "shell(" in text
    assert not (tmp_path / ".agent-native-workflow" / "agent-config.yaml").exists()


def test_init_creates_config_with_claude_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    cmd_init(_args(cli=None))
    text = (tmp_path / ".agent-native-workflow" / "config.yaml").read_text()
    assert "cli-provider: claude" in text
    assert "Bash(" in text


# ── cmd_init when files already exist ────────────────────────────────────────


def test_init_updates_provider_files_when_cli_explicitly_set(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    # First init with claude
    cmd_init(_args(cli="claude"))
    config_yaml = tmp_path / ".agent-native-workflow" / "config.yaml"
    assert "cli-provider: claude" in config_yaml.read_text()
    assert "Bash(" in config_yaml.read_text()

    # Re-init with copilot explicitly set
    cmd_init(_args(cli="copilot"))
    text = config_yaml.read_text()
    assert "cli-provider: copilot" in text
    assert "shell(" in text
    assert "Bash(" not in text


def test_init_skips_provider_files_without_cli_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    cmd_init(_args(cli="claude"))
    config_yaml = tmp_path / ".agent-native-workflow" / "config.yaml"
    mtime_before = config_yaml.stat().st_mtime

    # Re-init without --cli: files should not change
    cmd_init(_args(cli=None))
    assert config_yaml.stat().st_mtime == mtime_before


def test_init_never_overwrites_content_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    cmd_init(_args(cli="claude"))

    prompt = tmp_path / ".agent-native-workflow" / "PROMPT.yaml"
    reqs = tmp_path / ".agent-native-workflow" / "requirements.md"
    prompt.write_text("MY CUSTOM PROMPT")
    reqs.write_text("MY CUSTOM REQUIREMENTS")

    # Re-init with different provider — content files must be preserved
    cmd_init(_args(cli="copilot"))
    assert prompt.read_text() == "MY CUSTOM PROMPT"
    assert reqs.read_text() == "MY CUSTOM REQUIREMENTS"


def test_init_warns_when_legacy_agent_config_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "agent_native_workflow.detect.detect_all",
        lambda: _fake_detected(),
    )
    legacy = tmp_path / ".agent-native-workflow" / "agent-config.yaml"
    legacy.parent.mkdir()
    legacy.write_text("agent_a:\n  model: legacy\n")
    cmd_init(_args(cli=None))
    out = capsys.readouterr().out
    assert "legacy .agent-native-workflow/agent-config.yaml" in out


# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_detected():
    from agent_native_workflow.detect import ProjectConfig

    return ProjectConfig(
        project_type="python",
        lint_cmd="ruff check src",
        test_cmd="pytest",
    )
