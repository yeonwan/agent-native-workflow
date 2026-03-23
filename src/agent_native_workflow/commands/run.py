from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_run(args: argparse.Namespace) -> int:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.pipeline import run_pipeline
    from agent_native_workflow.prompt_loader import load_prompt
    from agent_native_workflow.requirements_loader import load_requirements
    from agent_native_workflow.store import RunStore
    from agent_native_workflow.visualization import make_visualizer

    explicit: dict[str, object] = {}
    if args.cli:
        explicit["cli_provider"] = args.cli
    if args.max_iterations is not None:
        explicit["max_iterations"] = args.max_iterations
    if args.timeout is not None:
        explicit["timeout"] = args.timeout
    if args.max_retries is not None:
        explicit["max_retries"] = args.max_retries
    if args.base_branch:
        explicit["base_branch"] = args.base_branch
    if args.model:
        explicit["model"] = args.model
    if args.model_verify:
        explicit["model_verify"] = args.model_verify
    if getattr(args, "verification", None):
        explicit["verification"] = args.verification
    if args.no_ui:
        explicit["visualization"] = "plain"

    wcfg = WorkflowConfig.resolve(explicit=explicit)

    _prompt_arg = args.prompt or wcfg.prompt_file or ".agent-native-workflow/PROMPT.yaml"
    prompt_file = Path(_prompt_arg)
    requirements_file = Path(
        args.requirements or wcfg.requirements_file or ".agent-native-workflow/requirements.md"
    )

    if not requirements_file.is_file():
        print("ERROR: Requirements file not found", file=sys.stderr)
        return 1

    if getattr(args, "dry_run", False):
        effective_prompt: Path | None = prompt_file if prompt_file.is_file() else None
        if effective_prompt is None:
            try:
                prompt_text = load_requirements(requirements_file)
            except FileNotFoundError:
                print("ERROR: Requirements file not found", file=sys.stderr)
                return 1
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
        else:
            try:
                prompt_text = load_prompt(effective_prompt)
            except FileNotFoundError:
                print("ERROR: Requirements file not found", file=sys.stderr)
                return 1
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1

        print("=== Agent A Prompt (dry-run) ===")
        print(prompt_text)
        print("=== End of Prompt ===")
        return 0

    effective_prompt: Path | None = prompt_file if prompt_file.is_file() else None
    if effective_prompt is None:
        print(f"Note: No PROMPT file at {prompt_file} — using requirements as task spec.")

    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)
    visualizer = make_visualizer(wcfg.visualization)

    converged = run_pipeline(
        prompt_file=effective_prompt,
        requirements_file=requirements_file,
        store=store,
        max_iterations=wcfg.max_iterations,
        agent_timeout=wcfg.timeout,
        max_retries=wcfg.max_retries,
        visualizer=visualizer,
        workflow_config=wcfg,
        parallel_gates=args.parallel_gates if hasattr(args, "parallel_gates") else None,
    )

    return 0 if converged else 1
