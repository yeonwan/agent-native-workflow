from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import REVIEW_APPROVE_MARKER, VerificationResult
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.store import RunStore


class ReviewStrategy:
    """Single-agent review: requirements + changed files → APPROVE or feedback."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

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
        if config.changed_files:
            changed_section = "\n".join(config.changed_files)
        else:
            changed_section = (
                "(none listed — use git diff / Read to inspect recent changes if needed)"
            )

        prompt = f"""You are a code reviewer checking whether an implementation meets \
its requirements.

## Requirements
Read `{requirements_file}` — this is the source of truth.

## Changed Files
The following files were changed in this implementation:
{changed_section}

Read each changed file and verify the implementation against requirements.

## Your Review

For each requirement or acceptance criterion:
- **Requirement**: [quote it]
- **Status**: MET / NOT MET / PARTIAL
- **Evidence**: specific code references (function names, line behavior) that confirm or deny

## Issues
List anything that must be fixed, with specific file and location.

## Verdict
If all requirements are MET and no blocking issues exist, output on its own line:
{REVIEW_APPROVE_MARKER}

Otherwise, list exactly what Agent A must fix."""

        logger.info("Phase R: Requirements-based code review")
        output = self._runner.run(
            prompt, timeout=timeout, max_retries=max_retries, logger=logger
        )
        review_path = store.write_review(iteration, output)
        logger.info(f"Review saved → {review_path}")

        if REVIEW_APPROVE_MARKER in output:
            logger.info("RESULT: PASS (review)")
            return VerificationResult(passed=True, feedback="")

        logger.info("RESULT: FAIL (review — changes requested)")
        return VerificationResult(passed=False, feedback=output)
