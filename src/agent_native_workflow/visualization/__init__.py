from agent_native_workflow.visualization.base import PipelinePhase, Visualizer
from agent_native_workflow.visualization.multiplex import MultiplexVisualizer
from agent_native_workflow.visualization.plain import PlainVisualizer

__all__ = ["PipelinePhase", "Visualizer", "PlainVisualizer", "MultiplexVisualizer"]


def make_visualizer(mode: str) -> PlainVisualizer:
    """Create a visualizer by mode name ('textual', 'rich', or 'plain').

    Falls back gracefully: textual → rich → plain.
    """
    if mode == "textual":
        try:
            from agent_native_workflow.visualization.textual_ui import TextualVisualizer

            return TextualVisualizer()  # type: ignore[return-value]
        except ImportError:
            pass
    if mode in ("rich", "textual"):
        try:
            from agent_native_workflow.visualization.rich_ui import RichVisualizer

            return RichVisualizer()  # type: ignore[return-value]
        except ImportError:
            pass
    return PlainVisualizer()
