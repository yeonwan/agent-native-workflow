"""Tests for WorkflowConfig — loaders, type coercion, and priority resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_native_workflow.config import WorkflowConfig, _coerce, _normalize_toml

# ── _coerce ───────────────────────────────────────────────────────────────────


def test_coerce_int_field_from_string() -> None:
    assert _coerce("max_iterations", "7") == 7


def test_coerce_int_field_from_int() -> None:
    assert _coerce("max_iterations", 7) == 7


def test_coerce_path_field_from_string() -> None:
    result = _coerce("prompt_file", "some/path.yaml")
    assert result == Path("some/path.yaml")
    assert isinstance(result, Path)


def test_coerce_path_field_none_stays_none() -> None:
    assert _coerce("prompt_file", None) is None


def test_coerce_bool_field_true_strings() -> None:
    for val in ("true", "True", "1", "yes"):
        assert _coerce("security_agent_enabled", val) is True


def test_coerce_bool_field_false_strings() -> None:
    for val in ("false", "False", "0", "no"):
        assert _coerce("security_agent_enabled", val) is False


def test_coerce_bool_field_from_bool() -> None:
    assert _coerce("security_agent_enabled", True) is True
    assert _coerce("security_agent_enabled", False) is False


def test_coerce_string_field_unchanged() -> None:
    assert _coerce("cli_provider", "copilot") == "copilot"


# ── _normalize_toml ───────────────────────────────────────────────────────────


def test_normalize_toml_converts_kebab_keys() -> None:
    raw = {"max-iterations": "3", "cli-provider": "claude"}
    result = _normalize_toml(raw)
    assert result["max_iterations"] == 3
    assert result["cli_provider"] == "claude"


def test_normalize_toml_leaves_snake_keys_unchanged() -> None:
    raw = {"max_iterations": 5}
    result = _normalize_toml(raw)
    assert result["max_iterations"] == 5


# ── from_env ─────────────────────────────────────────────────────────────────


def test_from_env_reads_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLI_PROVIDER", "claude")
    result = WorkflowConfig.from_env()
    assert result["cli_provider"] == "claude"


def test_from_env_reads_max_iterations_as_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_ITERATIONS", "10")
    result = WorkflowConfig.from_env()
    assert result["max_iterations"] == 10
    assert isinstance(result["max_iterations"], int)


def test_from_env_reads_security_agent_enabled_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECURITY_AGENT_ENABLED", "false")
    result = WorkflowConfig.from_env()
    assert result["security_agent_enabled"] is False


def test_from_env_returns_empty_when_no_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CLI_PROVIDER", "MAX_ITERATIONS", "AGENT_TIMEOUT", "MAX_RETRIES",
        "BASE_BRANCH", "AGENT_MODEL", "AGENT_MODEL_VERIFY", "SECURITY_AGENT_ENABLED",
        "VISUALIZATION", "LINT_CMD", "TEST_CMD", "VERIFICATION",
        "PROMPT_FILE", "REQUIREMENTS_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    result = WorkflowConfig.from_env()
    assert result == {}


# ── from_pyproject ────────────────────────────────────────────────────────────


def test_from_pyproject_reads_tool_section(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.agent-native-workflow]\n"
        'cli-provider = "claude"\n'
        "max-iterations = 3\n"
    )
    result = WorkflowConfig.from_pyproject(tmp_path)
    assert result["cli_provider"] == "claude"
    assert result["max_iterations"] == 3


def test_from_pyproject_returns_empty_when_section_absent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nfoo = 1\n")
    assert WorkflowConfig.from_pyproject(tmp_path) == {}


def test_from_pyproject_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert WorkflowConfig.from_pyproject(tmp_path) == {}


def test_from_pyproject_returns_empty_on_malformed_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this is not valid [[toml")
    assert WorkflowConfig.from_pyproject(tmp_path) == {}


# ── from_config_dir ───────────────────────────────────────────────────────────


def test_from_config_dir_reads_yaml(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("cli-provider: copilot\nmax-iterations: 4\n")
    result = WorkflowConfig.from_config_dir(tmp_path)
    assert result["cli_provider"] == "copilot"
    assert result["max_iterations"] == 4


def test_from_config_dir_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert WorkflowConfig.from_config_dir(tmp_path) == {}


def test_from_config_dir_returns_empty_on_malformed_yaml(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(": invalid: yaml: content:\n  - bad\n    indent")
    # should not raise — just return {}
    result = WorkflowConfig.from_config_dir(tmp_path)
    assert isinstance(result, dict)


# ── resolve priority ──────────────────────────────────────────────────────────


def test_resolve_defaults_when_nothing_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLI_PROVIDER", raising=False)
    cfg = WorkflowConfig.resolve(project_root=tmp_path)
    assert cfg.max_iterations == 5
    assert cfg.cli_provider == "copilot"
    assert cfg.verification == "review"


def test_resolve_explicit_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLI_PROVIDER", "codex")
    cfg = WorkflowConfig.resolve(explicit={"cli_provider": "claude"}, project_root=tmp_path)
    assert cfg.cli_provider == "claude"


def test_resolve_explicit_overrides_config_yaml(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("max-iterations: 8\n")
    cfg = WorkflowConfig.resolve(explicit={"max_iterations": 2}, project_root=tmp_path)
    assert cfg.max_iterations == 2


def test_resolve_config_yaml_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_ITERATIONS", "3")
    cfg_dir = tmp_path / ".agent-native-workflow"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("max-iterations: 9\n")
    cfg = WorkflowConfig.resolve(project_root=tmp_path)
    assert cfg.max_iterations == 9


def test_resolve_unknown_keys_are_ignored(tmp_path: Path) -> None:
    cfg = WorkflowConfig.resolve(explicit={"nonexistent_field": "value"}, project_root=tmp_path)
    assert not hasattr(cfg, "nonexistent_field")


def test_resolve_returns_workflow_config_instance(tmp_path: Path) -> None:
    cfg = WorkflowConfig.resolve(project_root=tmp_path)
    assert isinstance(cfg, WorkflowConfig)
