"""Pluggable verification after quality gates (REDESIGN Phase 1)."""

from agent_native_workflow.strategies.factory import build_verification_strategy
from agent_native_workflow.strategies.none import NoneStrategy
from agent_native_workflow.strategies.review import ReviewStrategy
from agent_native_workflow.strategies.triangulation import TriangulationStrategy

__all__ = [
    "NoneStrategy",
    "ReviewStrategy",
    "TriangulationStrategy",
    "build_verification_strategy",
]
