"""Fluent Python API for programmatic pipeline usage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_native_workflow.config import WorkflowConfig
from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.log import Logger
from agent_native_workflow.pipeline import run_pipeline
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.store import RunStore
from agent_native_workflow.visualization.base import Visualizer


class Workflow:
    """Fluent builder for running the agent-native pipeline programmatically.

    Example:
        from pathlib import Path
        from agent_native_workflow.api import Workflow

        converged = (
            Workflow()
            .with_provider("copilot")
            .with_prompt(Path("PROMPT.md"))
            .with_requirements(Path("requirements.md"))
            .with_max_iterations(3)
            .run()
        )
    """

    def __init__(self) -> None:
        self._config = WorkflowConfig()
        self._prompt_file: Path | None = None
        self._requirements_file: Path | None = None
        self._store: RunStore | None = None
        self._runner: AgentRunner | None = None
        self._verify_runner: AgentRunner | None = None
        self._visualizer: Visualizer | None = None
        self._logger: Logger | None = None
        self._project_config: ProjectConfig | None = None
        self._custom_gates: list[tuple[str, Callable[[], tuple[bool, str]]]] = []
        self._parallel_gates: bool | None = None

    def with_provider(self, provider: str) -> Workflow:
        self._config.cli_provider = provider
        return self

    def with_prompt(self, path: Path) -> Workflow:
        self._prompt_file = path
        return self

    def with_requirements(self, path: Path) -> Workflow:
        self._requirements_file = path
        return self

    def with_store(self, store: RunStore) -> Workflow:
        self._store = store
        return self

    def with_base_dir(self, path: Path) -> Workflow:
        self._store = RunStore(base_dir=path)
        return self

    def with_max_iterations(self, n: int) -> Workflow:
        self._config.max_iterations = n
        return self

    def with_timeout(self, seconds: int) -> Workflow:
        self._config.timeout = seconds
        return self

    def with_model(self, model: str) -> Workflow:
        self._config.model = model
        return self

    def with_model_verify(self, model: str) -> Workflow:
        self._config.model_verify = model
        return self

    def with_runner(self, runner: AgentRunner) -> Workflow:
        self._runner = runner
        return self

    def with_verify_runner(self, runner: AgentRunner) -> Workflow:
        self._verify_runner = runner
        return self

    def with_visualizer(self, visualizer: Visualizer) -> Workflow:
        self._visualizer = visualizer
        return self

    def with_logger(self, logger: Logger) -> Workflow:
        self._logger = logger
        return self

    def with_gate(self, name: str, fn: Callable[[], tuple[bool, str]]) -> Workflow:
        self._custom_gates.append((name, fn))
        return self

    def with_parallel_gates(self, parallel: bool = True) -> Workflow:
        self._parallel_gates = parallel
        return self

    def run(self) -> bool:
        _pf = self._prompt_file or Path(".agent-native-workflow/PROMPT.yaml")
        prompt_file: Path | None = _pf if _pf.is_file() else None
        requirements_file = (
            self._requirements_file or Path(".agent-native-workflow/requirements.md")
        )

        return run_pipeline(
            prompt_file=prompt_file,
            requirements_file=requirements_file,
            store=self._store,
            max_iterations=self._config.max_iterations,
            agent_timeout=self._config.timeout,
            max_retries=self._config.max_retries,
            parallel_gates=self._parallel_gates,
            config=self._project_config,
            logger=self._logger,
            custom_gates=self._custom_gates or None,
            runner=self._runner,
            verify_runner=self._verify_runner,
            visualizer=self._visualizer,
            workflow_config=self._config,
        )
