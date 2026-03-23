from agent_native_workflow.visualization.base import PipelinePhase, Visualizer
from agent_native_workflow.visualization.plain import PlainVisualizer

__all__ = ["PipelinePhase", "Visualizer", "PlainVisualizer"]


def make_visualizer(mode: str) -> PlainVisualizer:
    """Create a visualizer by mode name ('rich' or 'plain').

    Falls back to PlainVisualizer if rich is not installed.
    """
    if mode == "rich":
        try:
            from agent_native_workflow.visualization.rich_ui import RichVisualizer

            return RichVisualizer()  # type: ignore[return-value]
        except ImportError:
            pass
    return PlainVisualizer()
