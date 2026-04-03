from __future__ import annotations

from agent_native_workflow.domain import VerificationStrategy
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.strategies.none import NoneStrategy
from agent_native_workflow.strategies.review import ReviewStrategy
from agent_native_workflow.strategies.triangulation import TriangulationStrategy


def build_verification_strategy(
    mode: str,
    *,
    verify_runner: AgentRunner,
    c_runner: AgentRunner,
    review_runner: AgentRunner | None = None,
    task_title: str = "",
) -> VerificationStrategy:
    """Instantiate the post-gate verification strategy for ``mode``.

    ``mode`` is case-insensitive. Supported values: ``none``, ``review``,
    ``triangulation``.

    For ``review``, ``review_runner`` (Agent R from ``config.yaml`` `agents:`) is used
    when provided; otherwise ``verify_runner`` is used as a fallback.
    """
    m = (mode or "review").strip().lower()
    if m == "none":
        return NoneStrategy()
    if m == "review":
        return ReviewStrategy(review_runner or verify_runner)
    if m == "triangulation":
        return TriangulationStrategy(
            runner=verify_runner,
            c_runner=c_runner,
            task_title=task_title,
        )
    raise ValueError(
        f"Unknown verification mode {mode!r}; expected 'none', 'review', or 'triangulation'"
    )
