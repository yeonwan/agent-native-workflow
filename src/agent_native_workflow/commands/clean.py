from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def cmd_clean(args: argparse.Namespace) -> int:
    """Delete old run directories, keeping N most recent (default N=5)."""
    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    runs_dir = base_dir / "runs"

    # Check if runs directory exists
    if not runs_dir.exists():
        print("No runs directory found.")
        return 0

    # Get all run directories, sorted lexicographically (chronologically in ISO format)
    run_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])

    # Determine how many to keep
    keep_count = 5  # default
    if hasattr(args, "keep") and args.keep is not None:
        keep_count = args.keep
    if getattr(args, "all", False):
        keep_count = 0

    # If we should delete all
    if keep_count == 0:
        deleted_count = len(run_dirs)
        for run_dir in run_dirs:
            shutil.rmtree(run_dir)

        # Remove latest symlink if it exists
        latest_link = base_dir / "latest"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()

        print(f"Deleted {deleted_count} run(s).")
        return 0

    # If fewer than or equal to keep_count runs exist, nothing to delete
    if len(run_dirs) <= keep_count:
        print(f"Nothing to clean ({len(run_dirs)} run(s), keep={keep_count}).")
        return 0

    # Delete oldest runs, keeping the N most recent
    to_delete = run_dirs[: len(run_dirs) - keep_count]
    deleted_count = len(to_delete)

    for run_dir in to_delete:
        shutil.rmtree(run_dir)

    print(f"Deleted {deleted_count} run(s). Kept {keep_count}.")
    return 0
