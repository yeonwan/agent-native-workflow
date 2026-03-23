from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_verify(args: argparse.Namespace) -> int:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.domain import AgentConfig
    from agent_native_workflow.log import Logger
    from agent_native_workflow.runners.factory import runner_for
    from agent_native_workflow.store import RunStore
    from agent_native_workflow.strategies.factory import build_verification_strategy

    explicit: dict[str, object] = {}
    if getattr(args, "verification", None):
        explicit["verification"] = args.verification
    wcfg = WorkflowConfig.resolve(explicit=explicit)
    requirements_file = Path(args.requirements or wcfg.requirements_file or "requirements.md")

    if not requirements_file.is_file():
        print(f"ERROR: Requirements file not found: {requirements_file}", file=sys.stderr)
        return 1

    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)
    store.start_run(
        config_snapshot={
            "cli_provider": wcfg.cli_provider,
            "verification": wcfg.verification,
        }
    )

    logger = Logger()
    cfg = detect_all(base_branch=args.base_branch or wcfg.base_branch)
    agent_cfg = wcfg.agent_config or AgentConfig()

    def _model_for(perms_model: str, global_override: str) -> dict[str, object]:
        m = global_override or perms_model
        return {"model": m} if m else {}

    verify_runner = runner_for(
        wcfg.cli_provider,
        allowed_tools=agent_cfg.agent_b.allowed_tools,
        permission_mode=agent_cfg.agent_b.permission_mode,
        **_model_for(agent_cfg.agent_b.model, wcfg.model_verify or wcfg.model),
    )
    review_runner = runner_for(
        wcfg.cli_provider,
        allowed_tools=agent_cfg.agent_r.allowed_tools,
        permission_mode=agent_cfg.agent_r.permission_mode,
        **_model_for(agent_cfg.agent_r.model, wcfg.model_verify or wcfg.model),
    )
    c_runner = runner_for(
        wcfg.cli_provider,
        allowed_tools=agent_cfg.agent_c.allowed_tools,
        permission_mode=agent_cfg.agent_c.permission_mode,
        **_model_for(agent_cfg.agent_c.model, wcfg.model_verify or wcfg.model),
    )

    strategy = build_verification_strategy(
        wcfg.verification,
        verify_runner=verify_runner,
        c_runner=c_runner,
        review_runner=review_runner,
        task_title="",
    )
    result = strategy.run(
        requirements_file=requirements_file,
        store=store,
        iteration=1,
        config=cfg,
        timeout=args.timeout or wcfg.timeout,
        max_retries=wcfg.max_retries,
        logger=logger,
    )
    return 0 if result.passed else 1
