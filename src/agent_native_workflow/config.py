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
    "advisory-iterations": "advisory_iterations",
    "notify": "notify",
    "permission-strategy": "permission_strategy",
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
    "ADVISORY_ITERATIONS": "advisory_iterations",
    "ANW_NOTIFY": "notify",
    "PERMISSION_STRATEGY": "permission_strategy",
}

_INT_FIELDS = {"max_iterations", "timeout", "max_retries", "advisory_iterations"}
_PATH_FIELDS = {"prompt_file", "requirements_file"}
_BOOL_FIELDS = {"security_agent_enabled", "notify"}


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


def _clone_permissions(perms: AgentPermissions) -> AgentPermissions:
    return AgentPermissions(
        allowed_tools=list(perms.allowed_tools),
        denied_tools=list(perms.denied_tools),
        permission_mode=perms.permission_mode,
        model=perms.model,
        timeout=perms.timeout,
    )


def _merge_agent(raw: object, fallback: AgentPermissions) -> AgentPermissions:
    if not isinstance(raw, dict) or not raw:
        return _clone_permissions(fallback)
    tools = raw.get("allowed_tools")
    if tools is None:
        tools = fallback.allowed_tools
    denied = raw.get("denied_tools")
    if denied is None:
        denied = fallback.denied_tools
    raw_timeout = raw.get("timeout")
    timeout = int(raw_timeout) if raw_timeout is not None else fallback.timeout
    return AgentPermissions(
        allowed_tools=list(tools),  # type: ignore[arg-type]
        denied_tools=list(denied),  # type: ignore[arg-type]
        permission_mode=str(raw.get("permission_mode", fallback.permission_mode)),
        model=str(raw.get("model", fallback.model)),
        timeout=timeout,
    )


def _merge_agent_config(raw: object, fallback: AgentConfig | None = None) -> AgentConfig:
    base = fallback or AgentConfig()
    return AgentConfig(
        agent_a=_merge_agent(getattr(raw, "get", lambda *_args, **_kwargs: None)("agent_a"), base.agent_a),
        agent_r=_merge_agent(getattr(raw, "get", lambda *_args, **_kwargs: None)("agent_r"), base.agent_r),
        agent_b=_merge_agent(getattr(raw, "get", lambda *_args, **_kwargs: None)("agent_b"), base.agent_b),
        agent_c=_merge_agent(getattr(raw, "get", lambda *_args, **_kwargs: None)("agent_c"), base.agent_c),
    )


@dataclass
class WorkflowConfig:
    """Workflow configuration.

    Priority: explicit > pyproject.toml > .agent-native-workflow.toml > env vars > defaults.
    """

    prompt_file: Path | None = Path(".agent-native-workflow/PROMPT.yaml")
    requirements_file: Path | None = Path(".agent-native-workflow/requirements.md")
    max_iterations: int = 5
    timeout: int = 600
    max_retries: int = 2
    base_branch: str = "main"
    security_agent_enabled: bool = True

    # Multi-CLI settings
    cli_provider: str = "copilot"  # default: copilot
    model: str = ""  # used by providers that accept --model
    model_verify: str = ""

    # Visualization
    visualization: str = "textual"  # "textual" | "rich" | "plain"

    # Quality gate commands — override auto-detected values from detect.py
    # Set in pyproject.toml [tool.agent-native-workflow], .agent-native-workflow.toml, or env vars
    lint_cmd: str = ""
    test_cmd: str = ""

    # Post-gate verification: none | review | triangulation
    verification: str = "review"

    # Advisory convergence: 0 = ignore advisory, N = retry up to N times for advisory
    advisory_iterations: int = 1

    # Desktop notifications
    notify: bool = True

    # Permission strategy: "whitelist" (default) | "blacklist"
    permission_strategy: str = "whitelist"

    agent_config: AgentConfig | None = field(default=None, repr=False)

    @staticmethod
    def _load_raw_config_yaml(root: Path | None = None) -> dict[str, object]:
        r = root or Path.cwd()
        config_path = r / ".agent-native-workflow" / "config.yaml"
        if not config_path.is_file():
            return {}
        try:
            import yaml  # type: ignore[import-untyped]

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def load_embedded_agent_config(
        root: Path | None = None, fallback: AgentConfig | None = None
    ) -> AgentConfig | None:
        raw = WorkflowConfig._load_raw_config_yaml(root)
        agents = raw.get("agents")
        if agents is None:
            return None
        return _merge_agent_config(agents, fallback=fallback)

    @staticmethod
    def load_legacy_agent_config(root: Path | None = None) -> AgentConfig:
        r = root or Path.cwd()
        config_file = r / ".agent-native-workflow" / "agent-config.yaml"
        if not config_file.is_file():
            return AgentConfig()

        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            return _merge_agent_config(data, fallback=AgentConfig())
        except Exception:
            return AgentConfig()

    @staticmethod
    def load_agent_config(root: Path | None = None) -> AgentConfig:
        legacy = WorkflowConfig.load_legacy_agent_config(root)
        embedded = WorkflowConfig.load_embedded_agent_config(root, fallback=legacy)
        return embedded or legacy

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
        raw = WorkflowConfig._load_raw_config_yaml(root)
        if not raw:
            return {}
        raw.pop("agents", None)
        return _normalize_toml(raw)  # reuse key normalisation (kebab → snake)

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
        if config.agent_config is None:
            config.agent_config = cls.load_agent_config(root)
        return config
