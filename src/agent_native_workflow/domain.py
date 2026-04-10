from __future__ import annotations

import enum
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_native_workflow.detect import ProjectConfig
    from agent_native_workflow.log import Logger
    from agent_native_workflow.store import RunStore


class GateStatus(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class AgentPermissions:
    """Permissions for a specific agent (A, R, B, or C)."""

    allowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "bypassPermissions"
    model: str = ""
    timeout: int | None = None  # seconds; None = use global WorkflowConfig.timeout

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"allowed_tools": self.allowed_tools}
        if self.permission_mode:
            d["permission_mode"] = self.permission_mode
        # Always include model so users can see and edit it, even when empty.
        d["model"] = self.model
        if self.timeout is not None:
            d["timeout"] = self.timeout
        return d


# ── Claude Code tool definitions ──────────────────────────────────────────────

# Base tools every Agent A needs regardless of project type
_AGENT_A_BASE = [
    "Read",
    "Edit",
    "Write",
    "Bash(git:status)",
    "Bash(git:diff)",
    "Bash(git:log)",
    "Grep",
    "Glob",
]
_AGENT_B_TOOLS = ["Read", "Grep", "Glob", "Bash(git:diff)", "Bash(git:log)"]
# Agent R (review mode): read requirements + code; same tool set as B per REDESIGN.
_AGENT_R_TOOLS = ["Read", "Grep", "Glob", "Bash(git:diff)", "Bash(git:log)"]
_AGENT_C_TOOLS = ["Read"]

# Build tools added on top of _AGENT_A_BASE, keyed by project_type
_AGENT_A_BUILD_TOOLS: dict[str, list[str]] = {
    "python": ["Bash(uv:*)", "Bash(pytest:*)", "Bash(ruff:*)", "Bash(make:*)"],
    "node": ["Bash(npm:*)", "Bash(npx:*)", "Bash(yarn:*)", "Bash(make:*)"],
    "rust": ["Bash(cargo:*)", "Bash(make:*)"],
    "go": ["Bash(go:*)", "Bash(make:*)"],
    "java-maven": ["Bash(mvn:*)", "Bash(make:*)"],
    "java-gradle": ["Bash(./gradlew compileJava)"],
}

# ── GitHub Copilot CLI tool definitions ───────────────────────────────────────
# Copilot uses --allow-tool="shell(cmd:*)" format. File read/write are handled
# natively by copilot so only shell (subprocess) tools need explicit permission.

_COPILOT_AGENT_A_BASE = [
    "read",
    "write",
    "edit",
    "grep",
    "glob",
    "shell(git status)",
    "shell(git diff)",
    "shell(git log)",
]
_COPILOT_AGENT_A_BUILD_TOOLS: dict[str, list[str]] = {
    "python": ["shell(uv:*)", "shell(pytest:*)", "shell(ruff:*)", "shell(make:*)"],
    "node": ["shell(npm:*)", "shell(npx:*)", "shell(yarn:*)", "shell(make:*)"],
    "rust": ["shell(cargo:*)", "shell(make:*)"],
    "go": ["shell(go:*)", "shell(make:*)"],
    "java-maven": ["shell(mvn:*)", "shell(make:*)"],
    "java-gradle": ["shell(./gradlew compileJava)"],
}
_COPILOT_AGENT_R_TOOLS = ["read", "grep", "glob", "shell(git diff)", "shell(git log)"]
_COPILOT_AGENT_B_TOOLS = ["read", "grep", "glob", "shell(git diff)", "shell(git log)"]
_COPILOT_AGENT_C_TOOLS: list[str] = ["read"]

# Default models per CLI provider for each agent role.
# Empty string = let the provider choose its default.
_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "agent_a": "claude-sonnet-4-6",  # implementer
        "agent_r": "claude-sonnet-4-6",  # review mode: balanced
        "agent_b": "claude-sonnet-4-6",  # triangulation B
        "agent_c": "claude-haiku-4-5-20251001",  # triangulation C
    },
    "copilot": {
        "agent_a": "gpt-5.4",
        "agent_r": "gpt-5.4",
        "agent_b": "gpt-5.4",
        "agent_c": "gpt-5.4",
    },
    "codex": {
        "agent_a": "o4-mini",
        "agent_r": "o4-mini",
        "agent_b": "o4-mini",
        "agent_c": "o4-mini",
    },
    "cursor": {
        "agent_a": "",  # cursor manages its own model selection
        "agent_r": "",
        "agent_b": "",
        "agent_c": "",
    },
}


def agent_config_for(project_type: str, cli_provider: str = "claude") -> AgentConfig:
    """Build an AgentConfig with allowed tools and default models for the given CLI provider.

    Agent A gets base tools + build/test tools specific to the project type.
    Agent R gets read-only tools for ``verification: review``.
    Agent B/C get triangulation roles (blind review + PM judge).
    """
    models = _DEFAULT_MODELS.get(cli_provider, _DEFAULT_MODELS["claude"])

    if cli_provider == "copilot":
        build_tools = _COPILOT_AGENT_A_BUILD_TOOLS.get(project_type, ["shell(make:*)"])
        agent_a_tools = _COPILOT_AGENT_A_BASE + build_tools
        return AgentConfig(
            agent_a=AgentPermissions(
                allowed_tools=agent_a_tools,
                permission_mode="",
                model=models["agent_a"],
            ),
            agent_r=AgentPermissions(
                allowed_tools=_COPILOT_AGENT_R_TOOLS,
                permission_mode="",
                model=models["agent_r"],
            ),
            agent_b=AgentPermissions(
                allowed_tools=_COPILOT_AGENT_B_TOOLS,
                permission_mode="",
                model=models["agent_b"],
            ),
            agent_c=AgentPermissions(
                allowed_tools=_COPILOT_AGENT_C_TOOLS,
                permission_mode="",
                model=models["agent_c"],
            ),
        )

    build_tools = _AGENT_A_BUILD_TOOLS.get(project_type, ["Bash(make:*)"])
    agent_a_tools = _AGENT_A_BASE + build_tools

    return AgentConfig(
        agent_a=AgentPermissions(
            allowed_tools=agent_a_tools,
            permission_mode="bypassPermissions",
            model=models["agent_a"],
        ),
        agent_r=AgentPermissions(
            allowed_tools=_AGENT_R_TOOLS,
            permission_mode="bypassPermissions",
            model=models["agent_r"],
        ),
        agent_b=AgentPermissions(
            allowed_tools=_AGENT_B_TOOLS,
            permission_mode="bypassPermissions",
            model=models["agent_b"],
        ),
        agent_c=AgentPermissions(
            allowed_tools=_AGENT_C_TOOLS,
            permission_mode="bypassPermissions",
            model=models["agent_c"],
        ),
    )


@dataclass
class AgentConfig:
    """Configuration for all agents in the pipeline (A, R for review, B/C for triangulation)."""

    agent_a: AgentPermissions = field(
        default_factory=lambda: AgentPermissions(
            allowed_tools=_AGENT_A_BASE + _AGENT_A_BUILD_TOOLS["python"],
            permission_mode="bypassPermissions",
        )
    )
    agent_r: AgentPermissions = field(
        default_factory=lambda: AgentPermissions(
            allowed_tools=_AGENT_R_TOOLS,
            permission_mode="bypassPermissions",
        )
    )
    agent_b: AgentPermissions = field(
        default_factory=lambda: AgentPermissions(
            allowed_tools=_AGENT_B_TOOLS,
            permission_mode="bypassPermissions",
        )
    )
    agent_c: AgentPermissions = field(
        default_factory=lambda: AgentPermissions(
            allowed_tools=_AGENT_C_TOOLS, permission_mode="bypassPermissions"
        )
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_a": self.agent_a.to_dict(),
            "agent_r": self.agent_r.to_dict(),
            "agent_b": self.agent_b.to_dict(),
            "agent_c": self.agent_c.to_dict(),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._to_yaml(), encoding="utf-8")

    def to_embedded_yaml(self) -> str:
        """Render an `agents:` block for embedding in config.yaml."""
        lines = [
            "# Advanced per-agent settings.",
            "# Most users can leave this alone unless they want per-agent overrides.",
            "# BEGIN agents",
            "agents:",
        ]
        lines.extend(self._yaml_blocks(base_indent=2))
        lines.append("# END agents")
        return "\n".join(lines) + "\n"

    def _to_yaml(self) -> str:
        """Render agent-config.yaml with inline comments for discoverability."""
        return "\n".join(self._yaml_blocks(base_indent=0)) + "\n"

    def _yaml_blocks(self, base_indent: int) -> list[str]:
        """Render per-agent YAML blocks with the requested left padding."""
        prefix = " " * base_indent

        def _agent_block(name: str, perms: AgentPermissions, timeout_hint: str) -> str:
            tools_lines = "\n".join(f"{prefix}    - {t}" for t in perms.allowed_tools)
            model_line = (
                f"{prefix}  model: {perms.model}" if perms.model else f"{prefix}  # model: "
            )
            timeout_line = (
                f"{prefix}  timeout: {perms.timeout}"
                if perms.timeout is not None
                else (
                    f"{prefix}  # timeout: {timeout_hint}  "
                    "# seconds; overrides global timeout"
                )
            )
            return (
                f"{prefix}{name}:\n"
                f"{prefix}  allowed_tools:\n"
                f"{tools_lines}\n"
                f"{model_line}\n"
                f"{timeout_line}"
            )

        return [
            _agent_block("agent_a", self.agent_a, "300"),
            _agent_block("agent_r", self.agent_r, "180"),
            _agent_block("agent_b", self.agent_b, "180"),
            _agent_block("agent_c", self.agent_c, "120"),
        ]


class IterationOutcome(enum.Enum):
    PASS = "pass"
    GATE_FAIL = "gate_fail"
    VERIFY_FAIL = "verify_fail"
    SECURITY_FAIL = "security_fail"
    NO_PROGRESS = "no_progress"  # Agent A made no file changes


TRIANGULAR_PASS_MARKER = "TRIANGULAR_PASS"
CONSENSUS_AGREE_MARKER = "CONSENSUS_AGREE"
REVIEW_APPROVE_MARKER = "REVIEW_APPROVE"
REVIEW_APPROVE_WITH_ADVISORY_MARKER = "REVIEW_APPROVE_WITH_ADVISORY"
REVIEW_RESULT_BLOCK_START = "<<<ANW_REVIEW_RESULT>>>"
REVIEW_RESULT_BLOCK_END = "<<<END_ANW_REVIEW_RESULT>>>"
REVIEW_VERDICT_PASS = "pass"
REVIEW_VERDICT_PASS_WITH_ADVISORY = "pass_with_advisory"
REVIEW_VERDICT_FAIL = "fail"
SECURITY_AGENT_PASS_MARKER = "SECURITY_AGENT_PASS"


@dataclass
class VerificationResult:
    """Outcome of a post-gate verification strategy (review, triangulation, etc.).

    When ``passed`` is False, ``feedback`` is the text passed to Agent A on the
    next iteration (same role as prior ``c-report`` / verify feedback).

    ``next_agent_r_session_id`` is set by review mode when the Agent R runner
    supports CLI session resume; the pipeline carries it across iterations.
    """

    passed: bool
    advisory_only: bool = False
    feedback: str = ""
    next_agent_r_session_id: str | None = None


class VerificationStrategy(Protocol):
    """Pluggable verification after quality gates pass (Phase 1 redesign).

    Implementations may hold runners, prompts, etc. in ``__init__``; ``run`` only
    receives per-iteration inputs shared by the pipeline.
    """

    def run(
        self,
        requirements_file: Path,
        store: RunStore,
        iteration: int,
        config: ProjectConfig,
        timeout: int,
        max_retries: int,
        logger: Logger,
        verification_session_id: str | None = None,
    ) -> VerificationResult: ...


GateFunction = Callable[[], tuple[bool, str]]


@dataclass
class GateResult:
    name: str = ""
    status: GateStatus = GateStatus.SKIPPED
    output: str = ""
    duration_s: float = 0.0


@dataclass
class IterationMetrics:
    iteration: int = 0
    duration_s: float = 0.0
    phase1_done: bool = False
    gate_results: list[GateResult] = field(default_factory=list)
    verification_status: GateStatus = GateStatus.SKIPPED
    security_agent_status: GateStatus = GateStatus.SKIPPED
    outcome: IterationOutcome | None = None

    @property
    def lint_result(self) -> str:
        return self._gate_status("lint")

    @property
    def test_result(self) -> str:
        return self._gate_status("test")

    @property
    def security_result(self) -> str:
        return self._gate_status("security")

    @property
    def plugin_results(self) -> list[dict[str, object]]:
        return [
            {
                "name": g.name,
                "result": g.status.value,
                "output": g.output,
                "duration_s": g.duration_s,
            }
            for g in self.gate_results
            if g.name not in ("lint", "test", "security")
        ]

    @property
    def verification_result(self) -> str:
        return self.verification_status.value

    @property
    def security_agent_result(self) -> str:
        return self.security_agent_status.value

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "duration_s": self.duration_s,
            "phase1_done": self.phase1_done,
            "lint_result": self.lint_result,
            "test_result": self.test_result,
            "security_result": self.security_result,
            "plugin_results": self.plugin_results,
            "verification_result": self.verification_result,
            "security_agent_result": self.security_agent_result,
            "outcome": self.outcome.value if self.outcome else "",
        }

    def _gate_status(self, name: str) -> str:
        for g in self.gate_results:
            if g.name == name:
                return g.status.value
        return GateStatus.SKIPPED.value


@dataclass
class PipelineMetrics:
    started_at: str = ""
    ended_at: str = ""
    total_duration_s: float = 0.0
    total_iterations: int = 0
    converged: bool = False
    iterations: list[IterationMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total_duration_s": self.total_duration_s,
            "total_iterations": self.total_iterations,
            "converged": self.converged,
            "iterations": [it.to_dict() for it in self.iterations],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
