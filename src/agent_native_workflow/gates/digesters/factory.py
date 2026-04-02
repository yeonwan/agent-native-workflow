from __future__ import annotations

from agent_native_workflow.gates.digesters.base import GateDigester
from agent_native_workflow.gates.digesters.cargo_digester import CargoTestDigester
from agent_native_workflow.gates.digesters.generic import GenericDigester
from agent_native_workflow.gates.digesters.go_test_digester import GoTestDigester
from agent_native_workflow.gates.digesters.gradle_digester import GradleDigester
from agent_native_workflow.gates.digesters.jest_digester import JestDigester
from agent_native_workflow.gates.digesters.pytest_digester import PytestDigester


def build_digester(gate_name: str, cmd: str) -> GateDigester:
    """Pick a digester from the gate name and command string."""
    _ = gate_name
    cmd_lower = cmd.lower()

    if "pytest" in cmd_lower or "py.test" in cmd_lower:
        return PytestDigester()
    if "jest" in cmd_lower or "vitest" in cmd_lower:
        return JestDigester()
    if any(kw in cmd_lower for kw in ("gradlew", "gradle", "mvn ", "mvnw", "maven")):
        return GradleDigester()
    if "cargo test" in cmd_lower:
        return CargoTestDigester()
    if "go test" in cmd_lower:
        return GoTestDigester()

    return GenericDigester()
