from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_status(args: argparse.Namespace) -> int:
    """Print a human-readable summary of a past pipeline run."""
    from agent_native_workflow.store import RunStore

    base_dir = Path(args.output_dir) if args.output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)

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
    verification_mode = str(summary.get("verification_mode") or "unknown")

    print(f"Run ID    : {summary['run_id']}")
    print(f"Started   : {manifest.get('started_at', 'unknown')}")
    print(f"Verification: {verification_mode}")

    if config_snap:
        cli_provider = config_snap.get("cli_provider", "")
        if cli_provider:
            print(f"Provider  : {cli_provider}")
        model_a = config_snap.get("model", "") or config_snap.get("model_a", "")
        model_r = config_snap.get("model_r", "")
        model_b = config_snap.get("model_verify", "") or config_snap.get("model_b", "")
        if model_a:
            print(f"Model A   : {model_a}")
        if model_r:
            print(f"Model R   : {model_r}")
        if model_b:
            print(f"Model B/C : {model_b}")

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

    if iterations:
        print()
        hdr = f"{'Iter':<6} {'Lint':<8} {'Test':<8} {'Verify':<10} {'Kind':<14} {'Outcome'}"
        print(hdr)
        print("-" * len(hdr))
        for it in iterations:
            gate_map: dict[str, str] = {}
            for g in it.get("gate_results") or []:
                gate_map[g.get("name", "")] = g.get("status", "")
            lint_s = gate_map.get("lint", "skipped")
            test_s = gate_map.get("test", "skipped")

            verify_s = str(it.get("verification_result") or "").strip() or "skipped"
            kind_s = str(it.get("verification_kind") or "").strip() or "—"

            outcome = it.get("outcome") or ""
            row = (
                f"{it['iteration']:<6} {lint_s:<8} {test_s:<8} "
                f"{verify_s:<10} {kind_s:<14} {outcome}"
            )
            print(row)

    return 0
