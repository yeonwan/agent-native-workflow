from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _gate_status(gate_results: list[dict], name: str) -> str:
    """Return the status string for a named gate, or 'skipped' if absent."""
    for g in gate_results:
        if g.get("name") == name:
            return str(g.get("status", "skipped"))
    return "skipped"


def _read_file(path: Path) -> str | None:
    """Read a file and return its text, or None if it does not exist."""
    if path.is_file():
        return path.read_text()
    return None


def _details_block(summary: str, content: str) -> str:
    """Wrap content in a GitHub-flavored Markdown <details> block."""
    return (
        "<details>\n"
        f"<summary>{summary}</summary>\n"
        "\n"
        f"{content.strip()}\n"
        "\n"
        "</details>"
    )


def _build_report(summary: dict, base_dir: Path) -> str:
    """Build the full Markdown report string from a run summary dict."""
    manifest: dict = summary.get("manifest") or {}
    metrics: dict | None = summary.get("metrics")  # type: ignore[assignment]
    iterations: list = summary.get("iterations") or []
    config_snap: dict = manifest.get("config") or {}
    run_id = str(summary.get("run_id", "unknown"))
    verification_mode = str(summary.get("verification_mode") or "unknown")

    started_at = str(manifest.get("started_at", "unknown"))
    provider = str(config_snap.get("cli_provider", "unknown") or "unknown")

    if metrics:
        converged = "yes" if metrics.get("converged") else "no"
        total_iterations = int(metrics.get("total_iterations", len(iterations)))
        total_duration = float(metrics.get("total_duration_s", 0))
        duration_str = f"{total_duration:.1f}s"
    else:
        converged = "incomplete"
        total_iterations = len(iterations)
        duration_str = "unknown"

    lines: list[str] = []

    # Header
    lines.append(f"# Run Report: {run_id}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Run ID | `{run_id}` |")
    lines.append(f"| Started At | {started_at} |")
    lines.append(f"| Provider | {provider} |")
    lines.append(f"| Verification Mode | {verification_mode} |")
    lines.append(f"| Converged | {converged} |")
    lines.append(f"| Total Iterations | {total_iterations} |")
    lines.append(f"| Total Duration | {duration_str} |")
    lines.append("")

    # Per-iteration sections
    # Resolve run_dir for reading artifact files
    run_dir: Path | None = None
    latest = base_dir / "latest"
    run_id_val = summary.get("run_id")
    if run_id_val:
        candidate = base_dir / "runs" / str(run_id_val)
        if candidate.is_dir():
            run_dir = candidate
    if run_dir is None and latest.exists():
        run_dir = latest.resolve()

    for it in iterations:
        iter_num = int(it.get("iteration", 0))
        gate_results: list[dict] = it.get("gate_results") or []
        outcome = str(it.get("outcome") or "—")
        if not outcome or outcome == "":
            outcome = "—"

        lint_s = _gate_status(gate_results, "lint")
        test_s = _gate_status(gate_results, "test")

        lines.append(f"## Iteration {iter_num}")
        lines.append("")
        lines.append(f"- **Lint**: {lint_s}")
        lines.append(f"- **Test**: {test_s}")
        lines.append(f"- **Outcome**: {outcome}")
        lines.append("")

        if run_dir is not None:
            idir = run_dir / f"iter-{iter_num:03d}"

            # Review artifact (review mode or triangulation)
            review_content: str | None = None
            review_label: str | None = None
            for fname, label in [
                ("review.md", "Review"),
                ("b-review.md", "B-Review"),
                ("c-report.md", "C-Report"),
            ]:
                content = _read_file(idir / fname)
                if content is not None:
                    review_content = content
                    review_label = label
                    break

            if review_content is not None and review_label is not None:
                lines.append(_details_block(f"{review_label} — click to expand", review_content))
                lines.append("")

            # Feedback artifact (only if iteration did not converge)
            if outcome != "pass":
                feedback_content = _read_file(idir / "feedback.md")
                if feedback_content is not None:
                    lines.append(_details_block("Feedback — click to expand", feedback_content))
                    lines.append("")

    return "\n".join(lines)


def cmd_export(args: argparse.Namespace) -> int:
    """Export a run as a structured Markdown report."""
    from agent_native_workflow.store import RunStore

    output_dir = getattr(args, "output_dir", None)
    base_dir = Path(output_dir) if output_dir else Path(".agent-native-workflow")
    store = RunStore(base_dir=base_dir)

    run_id: str | None = getattr(args, "run", None) or None
    summary = store.load_run_summary(run_id=run_id)

    if summary is None:
        if run_id:
            print(f"Run '{run_id}' not found.", file=sys.stderr)
        else:
            print("No runs found. Run 'anw run' first.", file=sys.stderr)
        return 1

    report = _build_report(summary, base_dir)

    output_file: str | None = getattr(args, "output", None) or None
    if output_file:
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        print(f"Report written to {output_file}")
    else:
        print(report)

    return 0
