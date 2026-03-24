from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig, detect_all
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.store import RunStore
from agent_native_workflow.strategies.triangulation import TriangulationStrategy


def run_triangular_verification(
    requirements_file: Path,
    store: RunStore,
    iteration: int,
    config: ProjectConfig | None = None,
    timeout: int = 300,
    max_retries: int = 2,
    logger: Logger | None = None,
    runner: AgentRunner | None = None,
    c_runner: AgentRunner | None = None,
    task_title: str = "",
) -> tuple[bool, str]:
    """Run triangular verification: B (Senior Dev) → C (PM) → B (Confirmation).

    Thin wrapper around :class:`TriangulationStrategy` for backward compatibility.

    Writes artifacts to store.iter_dir(iteration):
      - b-review.md, c-report.md, b-confirm.md (when C passes)

    Returns (passed, feedback) where feedback is empty on full success.
    """
    if runner is None:
        raise ValueError(
            "runner is required. Pass the same AgentRunner configured for the workflow."
        )

    if logger is None:
        logger = Logger()

    effective_config = config or detect_all()

    strategy = TriangulationStrategy(
        runner=runner,
        c_runner=c_runner,
        task_title=task_title,
    )
    result = strategy.run(
        requirements_file=requirements_file,
        store=store,
        iteration=iteration,
        config=effective_config,
        timeout=timeout,
        max_retries=max_retries,
        logger=logger,
        verification_session_id=None,
    )
    return result.passed, result.feedback
