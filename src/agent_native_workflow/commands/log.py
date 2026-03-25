"""Print run artifact contents to stdout."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Phase to file mapping
PHASE_TO_FILE = {
    "agent": "a-output.md",
    "review": "review.md",
    "feedback": "feedback.md",
    "gates": "gates.json",
    "b-review": "b-review.md",
    "c-report": "c-report.md",
    "b-confirm": "b-confirm.md",
}

VALID_PHASES = list(PHASE_TO_FILE.keys())


def cmd_log(args: argparse.Namespace) -> int:
    """Print artifact contents from a pipeline run.

    Supports:
      - Default: show Agent A output for latest iteration of latest run
      - --phase: select which artifact to show
      - --iter: target specific iteration
      - --run: target specific run
      - --all-iters: print all iterations for a phase
      - --output-dir: use custom artifact directory
    """
    from agent_native_workflow.store import RunStore

    # Resolve base directory
    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)

    # Resolve phase (default: "agent")
    phase: str = getattr(args, "phase", None) or "agent"
    if phase not in PHASE_TO_FILE:
        valid_str = ", ".join(VALID_PHASES)
        print(f"Invalid phase '{phase}'. Valid phases: {valid_str}", file=sys.stderr)
        return 1

    filename = PHASE_TO_FILE[phase]

    # Check for conflicting flags
    all_iters: bool = getattr(args, "all_iters", False) or False
    iter_num: int | None = getattr(args, "iter", None)

    if all_iters and iter_num is not None:
        print("--all-iters is incompatible with --iter", file=sys.stderr)
        return 1

    # Resolve run
    run_id: str | None = getattr(args, "run", None) or None
    summary = store.load_run_summary(run_id=run_id)

    if summary is None:
        if run_id:
            print(f"Run '{run_id}' not found.", file=sys.stderr)
        else:
            print("No runs found. Run 'anw run' first.", file=sys.stderr)
        return 1

    # Get the run directory
    if run_id is None:
        # Use the latest run from summary
        run_id = summary["run_id"]

    run_dir = base_dir / "runs" / run_id

    # Determine which iterations to show
    iterations_to_show: list[int] = []

    if all_iters:
        # Collect all iteration numbers
        iter_dirs = sorted(run_dir.glob("iter-[0-9][0-9][0-9]"))
        for iter_dir in iter_dirs:
            iter_name = iter_dir.name
            try:
                num = int(iter_name.replace("iter-", ""))
                iterations_to_show.append(num)
            except ValueError:
                continue

        if not iterations_to_show:
            print(f"No iterations found in run {run_id}.", file=sys.stderr)
            return 1
    else:
        # Determine the iteration number
        if iter_num is not None:
            iterations_to_show = [iter_num]
        else:
            # Find the latest iteration
            iter_dirs = sorted(run_dir.glob("iter-[0-9][0-9][0-9]"))
            if not iter_dirs:
                print(f"No iterations found in run {run_id}.", file=sys.stderr)
                return 1

            latest_iter_dir = iter_dirs[-1]
            iter_name = latest_iter_dir.name
            try:
                latest_num = int(iter_name.replace("iter-", ""))
                iterations_to_show = [latest_num]
            except ValueError:
                print(f"Could not parse iteration number from {iter_name}.", file=sys.stderr)
                return 1

    # Read and print files
    found_any = False
    for it_num in iterations_to_show:
        iter_dir = run_dir / f"iter-{it_num:03d}"

        # Check if iteration directory exists
        if not iter_dir.is_dir():
            if all_iters:
                continue
            else:
                print(f"Iteration {it_num} not found in run {run_id}.", file=sys.stderr)
                return 1

        file_path = iter_dir / filename

        if not file_path.is_file():
            # For --all-iters, skip silently; otherwise error
            if all_iters:
                continue
            else:
                msg = f"No output found for iter-{it_num:03d} ({filename} missing)."
                print(msg, file=sys.stderr)
                return 1

        # Show header for --all-iters (only once we know we have content)
        if all_iters:
            if found_any:
                print()  # blank line between iterations
            print(f"=== Iteration {it_num} ===")

        try:
            content = file_path.read_text()
            print(content, end="")
            # Add newline if content doesn't already end with one
            if content and not content.endswith("\n"):
                print()
            found_any = True
        except Exception as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

    if all_iters and not found_any:
        print(f"No {filename} files found for any iteration in run {run_id}.", file=sys.stderr)
        return 1

    return 0
