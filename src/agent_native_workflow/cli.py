from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_run(args: argparse.Namespace) -> int:
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
    if args.no_ui:
        explicit["visualization"] = "plain"

    wcfg = WorkflowConfig.resolve(explicit=explicit)

    _prompt_arg = args.prompt or wcfg.prompt_file or ".agent-native-workflow/PROMPT.yaml"
    prompt_file = Path(_prompt_arg)
    requirements_file = Path(
        args.requirements or wcfg.requirements_file or ".agent-native-workflow/requirements.md"
    )

    # requirements is mandatory; prompt is optional (falls back to requirements if absent)
    if not requirements_file.is_file():
        print("ERROR: Requirements file not found", file=sys.stderr)
        return 1

    # ── Handle dry-run mode ───────────────────────────────────────────────────────
    if getattr(args, "dry_run", False):
        effective_prompt: Path | None = prompt_file if prompt_file.is_file() else None
        if effective_prompt is None:
            # Use requirements as task spec
            try:
                prompt_text = load_requirements(requirements_file)
            except FileNotFoundError:
                print("ERROR: Requirements file not found", file=sys.stderr)
                return 1
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
        else:
            # Use PROMPT.yaml
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


def _cmd_verify(args: argparse.Namespace) -> int:
    from agent_native_workflow.config import WorkflowConfig
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.log import Logger
    from agent_native_workflow.runners.factory import runner_for
    from agent_native_workflow.store import RunStore
    from agent_native_workflow.verify import run_triangular_verification

    wcfg = WorkflowConfig.resolve()
    requirements_file = Path(args.requirements or wcfg.requirements_file or "requirements.md")

    if not requirements_file.is_file():
        print(f"ERROR: Requirements file not found: {requirements_file}", file=sys.stderr)
        return 1

    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)
    store.start_run(config_snapshot={"cli_provider": wcfg.cli_provider})

    logger = Logger()
    cfg = detect_all(base_branch=args.base_branch or wcfg.base_branch)
    runner = runner_for(wcfg.cli_provider, **({"model": wcfg.model} if wcfg.model else {}))

    passed, _ = run_triangular_verification(
        requirements_file=requirements_file,
        store=store,
        iteration=1,
        config=cfg,
        timeout=args.timeout or wcfg.timeout,
        max_retries=wcfg.max_retries,
        logger=logger,
        runner=runner,
    )
    return 0 if passed else 1


def _cmd_status(args: argparse.Namespace) -> int:
    """Print a human-readable summary of a past pipeline run.

    Uses RunStore.load_run_summary() to read the structured data and then
    formats it as plain text with no Rich dependency.
    """
    from agent_native_workflow.store import RunStore

    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)

    # ── --list mode ───────────────────────────────────────────────────────────
    if getattr(args, "list", False):
        runs = store.list_runs()
        if not runs:
            print("No runs found.")
            return 0
        print(f"{'Run ID':<30} {'Started At':<25} {'Converged':<12} {'Iterations'}")
        print("-" * 80)
        for r in runs:
            print(
                f"{r['run_id']:<30} {str(r['started_at']):<25} "
                f"{r['converged']:<12} {r['total_iterations']}"
            )
        return 0

    # ── single-run mode ───────────────────────────────────────────────────────
    run_id: str | None = getattr(args, "run", None) or None
    summary = store.load_run_summary(run_id=run_id)

    if summary is None:
        if run_id:
            print(f"Run '{run_id}' not found in {base_dir}/runs/", file=sys.stderr)
        else:
            print("No runs found. Run 'agn run' first.", file=sys.stderr)
        return 1

    manifest: dict = summary.get("manifest") or {}  # type: ignore[assignment]
    metrics: dict | None = summary.get("metrics")  # type: ignore[assignment]
    iterations: list = summary.get("iterations") or []  # type: ignore[assignment]
    config_snap: dict = manifest.get("config") or {}  # type: ignore[assignment]

    # Header
    print(f"Run ID    : {summary['run_id']}")
    print(f"Started   : {manifest.get('started_at', 'unknown')}")

    # Config snapshot details
    if config_snap:
        cli_provider = config_snap.get("cli_provider", "")
        if cli_provider:
            print(f"Provider  : {cli_provider}")
        model_a = config_snap.get("model", "") or config_snap.get("model_a", "")
        model_b = config_snap.get("model_verify", "") or config_snap.get("model_b", "")
        if model_a:
            print(f"Model A   : {model_a}")
        if model_b:
            print(f"Model B/C : {model_b}")

    # Metrics summary
    if metrics:
        converged = "yes" if metrics.get("converged") else "no"
        total_iters = metrics.get("total_iterations", len(iterations))
        total_dur = metrics.get("total_duration_s", 0)
        print(f"Converged : {converged}")
        print(f"Iterations: {total_iters}")
        print(f"Duration  : {total_dur:.1f}s")
    else:
        print("Converged : incomplete (run may not have finished)")
        print(f"Iterations: {len(iterations)} (from iter dirs)")

    # Per-iteration table
    if iterations:
        print()
        print(f"{'Iter':<6} {'Lint':<8} {'Test':<8} {'Verify':<10} {'Outcome'}")
        print("-" * 50)
        for it in iterations:
            gate_map: dict[str, str] = {}
            for g in it.get("gate_results") or []:
                gate_map[g.get("name", "")] = g.get("status", "")
            lint_s = gate_map.get("lint", "skipped")
            test_s = gate_map.get("test", "skipped")

            # verification result from metrics if available
            verify_s = "skipped"
            if metrics:
                for iter_m in metrics.get("iterations") or []:
                    if iter_m.get("iteration") == it["iteration"]:
                        verify_s = iter_m.get("verification_result", "skipped")
                        break

            outcome = it.get("outcome") or ""
            print(f"{it['iteration']:<6} {lint_s:<8} {test_s:<8} {verify_s:<10} {outcome}")

    return 0


def _cmd_detect(_args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all

    cfg = detect_all()
    print(cfg.print_config())
    return 0


def _cmd_providers(_args: argparse.Namespace) -> int:
    from agent_native_workflow.runners.factory import available_providers

    providers = available_providers()
    print(f"{'Provider':<12} {'CLI Command':<10} {'File Tools':<12} {'Status'}")
    print("-" * 60)
    for p in providers:
        experimental_tag = " [experimental]" if p["experimental"] else ""
        file_tools = "Yes" if p["file_tools"] else "No"
        print(
            f"{p['provider']:<12} {p['cli_cmd']:<10} {file_tools:<12} "
            f"{p['status']}{experimental_tag}"
        )
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.domain import agent_config_for

    config_dir = Path(".agent-native-workflow")
    config_dir.mkdir(exist_ok=True)

    prompt_file = config_dir / "PROMPT.yaml"
    requirements_file = config_dir / "requirements.md"
    agent_config_file = config_dir / "agent-config.yaml"
    workflow_config_file = config_dir / "config.yaml"

    # ── PROMPT.yaml ───────────────────────────────────────────────────────────
    if not prompt_file.exists():
        prompt_file.write_text("""\
# PROMPT.yaml — Agent A task definition
#
# HOW THIS WORKS:
#   - `title`, `build`, `criteria` are used by Agent A (implementer)
#   - `requirements.md` (or --requirements <file>) is used by Agent B/C (verifiers)
#   - If you delete this file, Agent A will read requirements.md directly instead
#
# TIP: If your Jira ticket already describes everything, you can skip this file
#      and just run: agn run --requirements path/to/ticket.docx
#
# Run `agn run` when ready. Only `title` is required; everything else is optional.

title: "Implement requirements"

build: |
  Read requirements.md and implement everything listed there.
  Read existing code first before making any changes.

# Add more context when the requirements file alone isn't enough for Agent A:
#
# context: |
#   FastAPI + SQLAlchemy ORM. Follow route patterns in src/api/routes/.
#   Tests use pytest + testcontainers — no mocking of DB layer.
#
# constraints:
#   - Do not change existing database schema
#   - Reuse existing service classes — no logic duplication
#   - All existing tests must continue to pass
#
# notes: |
#   See docs/architecture.md for system overview.

# Completion checklist — checked by quality gates and triangular verification.
criteria:
  - All requirements in requirements.md implemented
  - Lint passes
  - All existing tests pass
# Add test criteria only if the requirements explicitly ask for tests:
# - New tests cover the happy path and at least one error case
""")
        print(f"Created {prompt_file}")
    else:
        print(f"Skipped {prompt_file} (already exists)")

    # ── requirements.md ───────────────────────────────────────────────────────
    if not requirements_file.exists():
        requirements_file.write_text("""\
# Requirements: <Feature Title>

<!--
  This is the source of truth for Agent B (blind reviewer) and Agent C (judge).
  Write each requirement as a testable statement.
  Tip: you can replace this file with a Jira ticket (.docx or .pdf) using:
       agn run --requirements path/to/PROJ-123.docx
-->

## Functional Requirements

### FR-1: <Short Name>

**What**: <One sentence describing the behavior>

**Acceptance criteria**:
- Given <precondition>, when <action>, then <expected result>
- Error case: <what happens when input is invalid / resource not found>

### FR-2: <Short Name>

**What**: ...

**Acceptance criteria**:
- ...

## Non-Functional Requirements

### NFR-1: Code Quality
- Follow existing project patterns and naming conventions
- No new public function left without a docstring
- No commented-out code in final output

### NFR-2: Test Coverage
- New logic must have unit tests
- Tests must be deterministic (no sleep, no real network calls)
""")
        print(f"Created {requirements_file}")
    else:
        print(f"Skipped {requirements_file} (already exists)")

    # ── detect project once for both config files ─────────────────────────────
    detected = detect_all()
    project_type = detected.project_type

    # ── agent-config.json — tools scoped to detected project type ────────────
    if not agent_config_file.exists():
        cli_provider = getattr(args, "cli", None) or "claude"
        agent_config_for(project_type, cli_provider=cli_provider).save(agent_config_file)
        print(f"Created {agent_config_file} (project type: {project_type})")
    else:
        print(f"Skipped {agent_config_file} (already exists)")

    # ── config.yaml — user-facing workflow settings ───────────────────────────
    if not workflow_config_file.exists():
        lint_hint = (
            f"# lint-cmd: {detected.lint_cmd}" if detected.lint_cmd else "# lint-cmd: make lint"
        )
        test_hint = (
            f"# test-cmd: {detected.test_cmd}" if detected.test_cmd else "# test-cmd: make test"
        )
        workflow_config_file.write_text(f"""\
# agent-native-workflow configuration
# Edit this file to customize the workflow for this project.
# All settings are optional — defaults are auto-detected from the project.

# CLI provider to use for all agents (A, B, C).
# Options: claude, copilot, codex, cursor
cli-provider: claude

# Quality gate commands.
# Auto-detected from project type ({project_type}):
{lint_hint}
{test_hint}
# Uncomment and edit to override:
# lint-cmd: make lint
# test-cmd: make test

# Pipeline limits
# max-iterations: 5
# timeout: 300    # seconds per agent call
# max-retries: 2
""")
        print(f"Created {workflow_config_file}")
    else:
        print(f"Skipped {workflow_config_file} (already exists)")

    # ── .gitignore hint ───────────────────────────────────────────────────────
    gitignore = Path(".gitignore")
    agn_entry = ".agent-native-workflow/runs/"
    if gitignore.is_file():
        if agn_entry not in gitignore.read_text():
            with gitignore.open("a") as f:
                f.write(f"\n# agent-native-workflow runtime artifacts\n{agn_entry}\n")
            print(f"Added '{agn_entry}' to .gitignore")
    else:
        gitignore.write_text(f"# agent-native-workflow runtime artifacts\n{agn_entry}\n")
        print(f"Created .gitignore with '{agn_entry}'")

    print()
    print("Next steps:")
    print(f"  1. Edit {prompt_file} — describe what to build")
    print(f"  2. Edit {requirements_file} — list testable requirements")
    print("  3. Run: agn run --cli <provider>")
    print("     Or with a Jira ticket: agn run --requirements PROJ-123.docx")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-native-workflow",
        description="AI-native triangulation workflow — multi-CLI, real-time visualization",
    )
    parser.add_argument("--version", action="version", version="agent-native-workflow 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser("run", help="Run the full A→B→C pipeline")
    run_p.add_argument(
        "--cli",
        default=None,
        help="CLI provider: copilot (default), claude, codex, cursor",
    )
    run_p.add_argument("--prompt", default=None, help="Path to Agent A prompt (PROMPT.md)")
    run_p.add_argument(
        "--requirements", default=None, help="Path to requirements doc (requirements.md)"
    )
    run_p.add_argument("--output-dir", default=None, help="Artifacts directory")
    run_p.add_argument("--max-iterations", type=int, default=None)
    run_p.add_argument("--timeout", type=int, default=None, help="Per-agent timeout in seconds")
    run_p.add_argument("--max-retries", type=int, default=None)
    run_p.add_argument("--base-branch", default=None)
    run_p.add_argument("--model", default=None, help="Model name (for providers that accept it)")
    run_p.add_argument("--model-verify", default=None, help="Model for verification agents B+C")
    run_p.add_argument("--no-ui", action="store_true", help="Disable Rich TUI, use plain output")
    run_p.add_argument(
        "--parallel-gates", action="store_true", default=None, help="Run quality gates in parallel"
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the exact prompt that would be sent to Agent A, then exit",
    )

    # verify
    verify_p = sub.add_parser("verify", help="Run triangular verification only (B+C)")
    verify_p.add_argument("--requirements", default=None)
    verify_p.add_argument("--output-dir", default=None)
    verify_p.add_argument("--base-branch", default=None)
    verify_p.add_argument("--timeout", type=int, default=None)

    # detect
    sub.add_parser("detect", help="Print detected project configuration")

    # providers
    sub.add_parser("providers", help="List available CLI providers and their status")

    # init
    init_p = sub.add_parser("init", help="Scaffold PROMPT.md and requirements.md templates")
    init_p.add_argument(
        "--cli",
        default=None,
        help="CLI provider (claude, copilot, codex, cursor) — default models in agent-config.yaml",
    )

    # status
    status_p = sub.add_parser(
        "status", help="Show summary of the last (or a specific) pipeline run"
    )
    status_p.add_argument(
        "--run",
        default=None,
        metavar="RUN_ID",
        help="Show summary for a specific run ID (e.g. run-20260322-120000)",
    )
    status_p.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="List all runs newest-first",
    )
    status_p.add_argument(
        "--output-dir",
        default=None,
        help="Artifacts directory (default: .agent-native-workflow)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "run": _cmd_run,
        "verify": _cmd_verify,
        "detect": _cmd_detect,
        "providers": _cmd_providers,
        "init": _cmd_init,
        "status": _cmd_status,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args))


if __name__ == "__main__":
    main()
