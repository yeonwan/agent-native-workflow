from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import VerificationResult
from agent_native_workflow.log import Logger
from agent_native_workflow.store import RunStore


class NoneStrategy:
    """Post-gate verification disabled: always pass after quality gates succeed."""

    def run(
        self,
        requirements_file: Path,
        store: RunStore,
        iteration: int,
        config: ProjectConfig,
        timeout: int,
        max_retries: int,
        logger: Logger,
    ) -> VerificationResult:
        return VerificationResult(passed=True, feedback="")
