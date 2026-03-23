from __future__ import annotations

import enum
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class GateStatus(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class AgentPermissions:
    """Permissions for a specific agent (A, B, or C)."""

    allowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "bypassPermissions"
    model: str = ""

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "allowed_tools": self.allowed_tools,
            "permission_mode": self.permission_mode,
        }
        if self.model:
            d["model"] = self.model
        return d


# Base tools every Agent A needs regardless of project type
_AGENT_A_BASE = [
    "Read", "Edit", "Write", "Bash(git:status)", "Bash(git:diff)",
    "Bash(git:log)", "Grep", "Glob",
]
_AGENT_B_TOOLS = ["Read", "Grep", "Glob", "Bash(git:diff)", "Bash(git:log)"]
_AGENT_C_TOOLS = ["Read"]

# Build tools added on top of _AGENT_A_BASE, keyed by project_type
_AGENT_A_BUILD_TOOLS: dict[str, list[str]] = {
    "python": ["Bash(uv:*)", "Bash(pytest:*)", "Bash(ruff:*)", "Bash(make:*)"],
    "node":   ["Bash(npm:*)", "Bash(npx:*)", "Bash(yarn:*)", "Bash(make:*)"],
    "rust":   ["Bash(cargo:*)", "Bash(make:*)"],
    "go":     ["Bash(go:*)", "Bash(make:*)"],
    "java-maven":  ["Bash(mvn:*)", "Bash(make:*)"],
    "java-gradle": ["Bash(./gradlew:*)", "Bash(gradle:*)", "Bash(make:*)"],
}

# Default models per CLI provider for each agent role.
# Empty string = let the provider choose its default.
_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "agent_a": "claude-opus-4-6",       # implementer: most capable
        "agent_b": "claude-sonnet-4-6",     # reviewer: balanced
        "agent_c": "claude-haiku-4-5-20251001",  # judge: fast, cheap
    },
    "copilot": {
        "agent_a": "",  # copilot manages its own model selection
        "agent_b": "",
        "agent_c": "",
    },
    "codex": {
        "agent_a": "o4-mini",
        "agent_b": "o4-mini",
        "agent_c": "o4-mini",
    },
    "cursor": {
        "agent_a": "",  # cursor manages its own model selection
        "agent_b": "",
        "agent_c": "",
    },
}


def agent_config_for(project_type: str, cli_provider: str = "claude") -> AgentConfig:
    """Build an AgentConfig with allowed tools and default models for the given CLI provider.

    Agent A gets base tools + build/test tools specific to the project type.
    Agent B gets read-only tools (code review, git history).
    Agent C gets Read only (compares requirements vs review, never touches code).
    """
    build_tools = _AGENT_A_BUILD_TOOLS.get(project_type, ["Bash(make:*)"])
    agent_a_tools = _AGENT_A_BASE + build_tools

    models = _DEFAULT_MODELS.get(cli_provider, _DEFAULT_MODELS["claude"])

    return AgentConfig(
        agent_a=AgentPermissions(
            allowed_tools=agent_a_tools,
            permission_mode="bypassPermissions",
            model=models["agent_a"],
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
    """Configuration for all agents in the pipeline."""

    agent_a: AgentPermissions = field(
        default_factory=lambda: AgentPermissions(
            allowed_tools=_AGENT_A_BASE + _AGENT_A_BUILD_TOOLS["python"],
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
            "agent_b": self.agent_b.to_dict(),
            "agent_c": self.agent_c.to_dict(),
        }

    def save(self, path: Path) -> None:
        import yaml  # type: ignore[import-untyped]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(self.to_dict(), allow_unicode=True, sort_keys=False))


class IterationOutcome(enum.Enum):
    PASS = "pass"
    GATE_FAIL = "gate_fail"
    VERIFY_FAIL = "verify_fail"
    SECURITY_FAIL = "security_fail"


TRIANGULAR_PASS_MARKER = "TRIANGULAR_PASS"
SECURITY_AGENT_PASS_MARKER = "SECURITY_AGENT_PASS"

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
