from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="agent-native-workflow",
        formatter_class=RawDescriptionHelpFormatter,
        description=(
            "AI-native feature delivery pipeline: Agent A implements from your prompt and "
            "requirements, runs lint/tests, then optional verification (none, single-agent "
            "review, or triangulation). Backends: Claude Code, GitHub Copilot CLI, OpenAI "
            "Codex, Cursor."
        ),
        epilog=(
            "Examples:\n"
            "  agn init && agn run --cli claude\n"
            "  agn run --verification none --no-ui\n"
            "  agn verify --verification triangulation\n"
            "  agn status --list\n"
            "\n"
            "See README.md for verification modes and configuration."
        ),
    )
    parser.add_argument("--version", action="version", version="agent-native-workflow 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser(
        "run",
        formatter_class=RawDescriptionHelpFormatter,
        help="Full pipeline: implement → gates → verification; loop until done",
        description=(
            "Runs Agent A, then lint/test gates, then the verification strategy from "
            "config (or --verification). On failure, writes feedback and repeats up to "
            "--max-iterations."
        ),
    )
    run_p.add_argument(
        "--cli",
        default=None,
        metavar="PROVIDER",
        help="CLI backend: claude | copilot | codex | cursor (config default if omitted)",
    )
    run_p.add_argument(
        "--prompt",
        default=None,
        metavar="PATH",
        help="Agent A prompt file (default: .agent-native-workflow/PROMPT.yaml)",
    )
    run_p.add_argument(
        "--requirements",
        default=None,
        metavar="PATH",
        help="Requirements doc for verification (default: .agent-native-workflow/requirements.md)",
    )
    run_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Base dir for runs (default: .agent-native-workflow)",
    )
    run_p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        metavar="N",
        help="Max implement/verify cycles (default: from config, else 5)",
    )
    run_p.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SEC",
        help="Timeout per agent subprocess (default: from config)",
    )
    run_p.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retries per agent call on failure (default: from config)",
    )
    run_p.add_argument(
        "--base-branch",
        default=None,
        metavar="BRANCH",
        help="Git base branch for change detection (default: from config)",
    )
    run_p.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Model for Agent A (providers that support --model)",
    )
    run_p.add_argument(
        "--model-verify",
        default=None,
        metavar="NAME",
        help=(
            "Model for verification: agent_r (review mode) and agent_b/agent_c "
            "(triangulation); falls back to --model if unset"
        ),
    )
    run_p.add_argument(
        "--verification",
        choices=["none", "review", "triangulation"],
        default=None,
        metavar="MODE",
        help=(
            "Post-gate verification: none | review | triangulation "
            "(default: config.yaml verification, else review)"
        ),
    )
    run_p.add_argument(
        "--no-ui",
        action="store_true",
        help="Plain-text log output instead of Rich TUI",
    )
    run_p.add_argument(
        "--parallel-gates",
        action="store_true",
        default=None,
        help="Run lint and test gates concurrently (env PARALLEL_GATES also works)",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the Agent A prompt (header/footer) and exit; no agents or pipeline",
    )

    # verify
    verify_p = sub.add_parser(
        "verify",
        formatter_class=RawDescriptionHelpFormatter,
        help="Run only the verification step (no Agent A, no gates)",
        description=(
            "Uses the same verification mode as the full pipeline (config or "
            "--verification). Writes artifacts under .agent-native-workflow/runs/… "
            "Requires a requirements file and detected project context."
        ),
    )
    verify_p.add_argument(
        "--requirements",
        default=None,
        metavar="PATH",
        help="Requirements file (default: from config)",
    )
    verify_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Artifact base directory (default: .agent-native-workflow)",
    )
    verify_p.add_argument(
        "--base-branch",
        default=None,
        metavar="BRANCH",
        help="Git base branch for changed-files detection",
    )
    verify_p.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SEC",
        help="Per-agent timeout (default: from config)",
    )
    verify_p.add_argument(
        "--verification",
        choices=["none", "review", "triangulation"],
        default=None,
        metavar="MODE",
        help=("none | review | triangulation (default: config.yaml verification, else review)"),
    )

    # detect
    sub.add_parser(
        "detect",
        help="Print auto-detected project type, lint/test commands, and paths",
    )

    # providers
    sub.add_parser(
        "providers",
        help="List CLI providers (claude, copilot, …) and whether the binary is available",
    )

    # init
    init_p = sub.add_parser(
        "init",
        help="Create .agent-native-workflow/ with PROMPT.yaml, requirements, config, agent-config",
    )
    init_p.add_argument(
        "--cli",
        default=None,
        metavar="PROVIDER",
        help="Seed agent-config.yaml default models for this provider (claude, copilot, …)",
    )

    # status
    status_p = sub.add_parser(
        "status",
        help="Show verification mode, gates, and per-iteration summary for a run",
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
        metavar="DIR",
        help="Artifact base directory (default: .agent-native-workflow)",
    )

    # clean
    clean_p = sub.add_parser(
        "clean",
        help="Delete old run directories, keeping N most recent (default N=5)",
    )
    clean_p.add_argument(
        "--keep",
        type=int,
        default=None,
        metavar="N",
        help="Number of runs to keep (default: 5)",
    )
    clean_p.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Delete all runs",
    )
    clean_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Artifact base directory (default: .agent-native-workflow)",
    )

    # log
    log_p = sub.add_parser(
        "log",
        help="Print run artifact contents (Agent A output, review, feedback, etc.)",
    )
    log_p.add_argument(
        "--phase",
        default="agent",
        metavar="NAME",
        help=(
            "Which artifact to show: agent (default) | review | feedback | gates | "
            "b-review | c-report | b-confirm"
        ),
    )
    log_p.add_argument(
        "--iter",
        type=int,
        default=None,
        metavar="N",
        help="Show specific iteration N (default: latest)",
    )
    log_p.add_argument(
        "--run",
        default=None,
        metavar="RUN_ID",
        help="Show output from a specific run (default: latest)",
    )
    log_p.add_argument(
        "--all-iters",
        action="store_true",
        default=False,
        help="Print the phase artifact for every iteration in the run",
    )
    log_p.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Artifact base directory (default: .agent-native-workflow)",
    )

    return parser
