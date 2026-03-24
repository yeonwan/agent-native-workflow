from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agent_native_workflow.domain import AgentConfig, AgentPermissions

_KEY_MAP: dict[str, str] = {
    "prompt-file": "prompt_file",
    "requirements-file": "requirements_file",
    "max-iterations": "max_iterations",
    "max-retries": "max_retries",
    "base-branch": "base_branch",
    "cli-provider": "cli_provider",
    "model": "model",
    "model-verify": "model_verify",
    "security-agent-enabled": "security_agent_enabled",
    "visualization": "visualization",
    "lint-cmd": "lint_cmd",
    "test-cmd": "test_cmd",
    "verification": "verification",
}

_ENV_MAP: dict[str, str] = {
    "PROMPT_FILE": "prompt_file",
    "REQUIREMENTS_FILE": "requirements_file",
    "MAX_ITERATIONS": "max_iterations",
    "AGENT_TIMEOUT": "timeout",
    "MAX_RETRIES": "max_retries",
    "BASE_BRANCH": "base_branch",
    "CLI_PROVIDER": "cli_provider",
    "AGENT_MODEL": "model",
    "AGENT_MODEL_VERIFY": "model_verify",
    "SECURITY_AGENT_ENABLED": "security_agent_enabled",
    "VISUALIZATION": "visualization",
    "LINT_CMD": "lint_cmd",
    "TEST_CMD": "test_cmd",
    "VERIFICATION": "verification",
}

_INT_FIELDS = {"max_iterations", "timeout", "max_retries"}
_PATH_FIELDS = {"prompt_file", "requirements_file"}
_BOOL_FIELDS = {"security_agent_enabled"}


def _coerce(key: str, value: object) -> object:
    if key in _INT_FIELDS:
        return int(value)  # type: ignore[arg-type]
    if key in _PATH_FIELDS and value is not None:
        return Path(str(value))
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    return value


def _normalize_toml(raw: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for k, v in raw.items():
        field_name = _KEY_MAP.get(k, k.replace("-", "_"))
        result[field_name] = _coerce(field_name, v)
    return result


def _read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())  # type: ignore[return-value]
    except Exception:
        return {}


@dataclass
class WorkflowConfig:
    """Workflow configuration.

    Priority: explicit > pyproject.toml > .agent-native-workflow.toml > env vars > defaults.
    """

    prompt_file: Path | None = Path(".agent-native-workflow/PROMPT.yaml")
    requirements_file: Path | None = Path(".agent-native-workflow/requirements.md")
    max_iterations: int = 5
    timeout: int = 300
    max_retries: int = 2
    base_branch: str = "main"
    security_agent_enabled: bool = True

    # Multi-CLI settings
    cli_provider: str = "copilot"  # default: copilot
    model: str = ""  # used by providers that accept --model
    model_verify: str = ""

    # Visualization
    visualization: str = "rich"  # "rich" | "plain"

    # Quality gate commands — override auto-detected values from detect.py
    # Set in pyproject.toml [tool.agent-native-workflow], .agent-native-workflow.toml, or env vars
    lint_cmd: str = ""
    test_cmd: str = ""

    # Post-gate verification: none | review | triangulation
    verification: str = "review"

    agent_config: AgentConfig | None = field(default=None, repr=False)

    @staticmethod
    def load_agent_config(root: Path | None = None) -> AgentConfig:
        r = root or Path.cwd()
        config_file = r / ".agent-native-workflow" / "agent-config.yaml"

        if not config_file.is_file():
            return AgentConfig()

        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}

            blank = AgentConfig()

            def _merge_agent(raw: object, fallback: AgentPermissions) -> AgentPermissions:
                if not isinstance(raw, dict) or not raw:
                    return AgentPermissions(
                        allowed_tools=list(fallback.allowed_tools),
                        permission_mode=fallback.permission_mode,
                        model=fallback.model,
                    )
                tools = raw.get("allowed_tools")
                if tools is None:
                    tools = fallback.allowed_tools
                return AgentPermissions(
                    allowed_tools=list(tools),  # type: ignore[arg-type]
                    permission_mode=str(raw.get("permission_mode", fallback.permission_mode)),
                    model=str(raw.get("model", fallback.model)),
                )

            return AgentConfig(
                agent_a=_merge_agent(data.get("agent_a"), blank.agent_a),
                agent_r=_merge_agent(data.get("agent_r"), blank.agent_r),
                agent_b=_merge_agent(data.get("agent_b"), blank.agent_b),
                agent_c=_merge_agent(data.get("agent_c"), blank.agent_c),
            )
        except Exception:
            return AgentConfig()

    @staticmethod
    def from_pyproject(root: Path | None = None) -> dict[str, object]:
        r = root or Path.cwd()
        data = _read_toml(r / "pyproject.toml")
        section = data.get("tool", {})
        if isinstance(section, dict):
            raw = section.get("agent-native-workflow", {})
            if isinstance(raw, dict):
                return _normalize_toml(raw)
        return {}

    @staticmethod
    def from_config_dir(root: Path | None = None) -> dict[str, object]:
        """Read .agent-native-workflow/config.yaml — the primary user-facing config."""
        r = root or Path.cwd()
        config_path = r / ".agent-native-workflow" / "config.yaml"
        if not config_path.is_file():
            return {}
        try:
            import yaml  # type: ignore[import-untyped]

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                return {}
            return _normalize_toml(raw)  # reuse key normalisation (kebab → snake)
        except Exception:
            return {}

    @staticmethod
    def from_file(root: Path | None = None) -> dict[str, object]:
        """Legacy: read .agent-native-workflow.toml from project root (kept for compat)."""
        r = root or Path.cwd()
        raw = _read_toml(r / ".agent-native-workflow.toml")
        return _normalize_toml(raw)

    @staticmethod
    def from_env() -> dict[str, object]:
        result: dict[str, object] = {}
        for env_key, field_name in _ENV_MAP.items():
            val = os.environ.get(env_key)
            if val is not None:
                result[field_name] = _coerce(field_name, val)
        return result

    @classmethod
    def resolve(
        cls, explicit: dict[str, object] | None = None, project_root: Path | None = None
    ) -> WorkflowConfig:
        root = project_root or Path.cwd()

        env_layer = cls.from_env()  # env vars
        config_dir_layer = cls.from_config_dir(root)  # .agent-native-workflow/config.yaml
        explicit_layer = explicit or {}  # CLI args

        # Priority (lowest → highest): env < config.yaml < CLI args
        merged: dict[str, object] = {}
        for layer in (env_layer, config_dir_layer, explicit_layer):
            for k, v in layer.items():
                if v is not None:
                    merged[k] = v

        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in merged.items() if k in known}

        config = cls(**filtered)
        config.agent_config = cls.load_agent_config(root)
        return config
