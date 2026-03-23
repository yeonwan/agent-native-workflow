"""RunStore — structured shared filesystem for agent communication.

Instead of flat files overwritten each iteration, each run gets an isolated
timestamped directory with per-iteration subdirectories.

Structure:
    .agn/
    ├── agent-config.json              (static permissions, written by `agn init`)
    └── runs/
        └── run-20260322-120000/       (one dir per pipeline run)
            ├── manifest.json          (config snapshot at run start)
            ├── iter-001/
            │   ├── a-output.md        (Agent A raw output)
            │   ├── gates.json         (structured gate results)
            │   ├── b-review.md        (Agent B blind review)
            │   ├── c-report.md        (Agent C discrepancy report)
            │   └── feedback.md        (structured feedback → Agent A next iter)
            ├── iter-002/
            │   └── ...
            └── metrics.json           (written at run end)

Why this is better than a flat output directory:
- Every iteration is preserved → full audit trail
- Runs don't clobber each other → concurrent-safe, re-runnable
- Agent A builds rich structured context from all previous iterations
- manifest.json records exactly what config was active
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_native_workflow.domain import GateResult, GateStatus, IterationOutcome


@dataclass
class IterationContext:
    """Structured view of a past iteration — used to build Agent A's context."""

    iteration: int
    outcome: IterationOutcome | None
    gate_results: list[GateResult] = field(default_factory=list)
    feedback: str = ""

    def to_prompt_section(self) -> str:
        """Render as a concise section for Agent A's context prompt."""
        lines = [f"### Iteration {self.iteration}"]

        if self.gate_results:
            gate_lines = []
            for g in self.gate_results:
                symbol = "✓" if g.status == GateStatus.PASS else "✗"
                gate_lines.append(f"  {symbol} {g.name}: {g.status.value}")
                if g.status != GateStatus.PASS and g.output:
                    # Include first 300 chars of failure output
                    gate_lines.append(f"    → {g.output[:300].strip()}")
            lines.append("**Quality Gates:**")
            lines.extend(gate_lines)

        if self.feedback:
            lines.append("\n**Feedback (what must be fixed):**")
            lines.append(self.feedback.strip())

        return "\n".join(lines)


class RunStore:
    """Manages the shared filesystem for one pipeline run.

    Usage:
        store = RunStore(base_dir=Path(".agn"))
        store.start_run(config_snapshot={...})

        # Each phase writes to the store:
        store.write_agent_output(iteration=1, content="...")
        store.write_gate_results(iteration=1, results=[...])
        store.write_b_review(iteration=1, content="...")
        store.write_c_report(iteration=1, content="...")
        store.write_feedback(iteration=1, content="...", outcome=IterationOutcome.VERIFY_FAIL)

        # Pipeline reads back:
        ctx = store.build_agent_a_context(iteration=2)
        b_review_path = store.b_review_path(iteration=1)  # pass to Agent C's prompt
    """

    BASE_DIR_NAME = ".agn"

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or Path(self.BASE_DIR_NAME)
        self._run_dir: Path | None = None

    @property
    def run_dir(self) -> Path:
        if self._run_dir is None:
            raise RuntimeError("Call start_run() first.")
        return self._run_dir

    @property
    def base_dir(self) -> Path:
        return self._base

    def start_run(self, config_snapshot: dict[str, object] | None = None) -> Path:
        """Create a timestamped run directory and write the manifest."""
        run_id = time.strftime("run-%Y%m%d-%H%M%S")
        self._run_dir = self._base / "runs" / run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "run_id": run_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "config": config_snapshot or {},
        }
        (self._run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )

        # Update `latest` symlink to point to this run
        latest_link = self._base / "latest"
        if latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(self._run_dir.resolve())

        return self._run_dir

    def iter_dir(self, iteration: int) -> Path:
        path = self.run_dir / f"iter-{iteration:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ── Requirements Snapshot ─────────────────────────────────────────────────

    def write_requirements_snapshot(self, content: str) -> Path:
        """Write requirements text to run dir as a canonical .md snapshot.

        Called once at pipeline start when the original requirements file is
        non-text (e.g. .docx, .pdf). All agents read from this path instead
        of the original so they always see a readable markdown file.
        """
        path = self.run_dir / "requirements-snapshot.md"
        path.write_text(content)
        return path

    def requirements_snapshot_path(self) -> Path | None:
        """Return path to requirements snapshot if one was written."""
        p = self.run_dir / "requirements-snapshot.md"
        return p if p.is_file() else None

    # ── Agent A ──────────────────────────────────────────────────────────────

    def write_agent_output(self, iteration: int, content: str) -> Path:
        path = self.iter_dir(iteration) / "a-output.md"
        path.write_text(content)
        return path

    def build_agent_a_context(self, iteration: int, prompt_file: Path) -> str:
        """Build Agent A's prompt for iteration N (N >= 2).

        Includes structured history from all previous iterations so Agent A
        understands the full picture, not just the last failure.
        """
        history_sections: list[str] = []

        for i in range(1, iteration):
            ctx = self._load_iteration_context(i)
            if ctx:
                history_sections.append(ctx.to_prompt_section())

        history_text = "\n\n".join(history_sections)

        return f"""\
Read `{prompt_file}` for the full requirements.

## Previous Iterations Summary

{history_text}

---

You are now on **iteration {iteration}**. Fix ALL issues listed above.

Rules:
- Do NOT start from scratch — read existing code first, then make targeted fixes
- Address every item in the feedback above
- After fixing, verify your changes satisfy the requirements in `{prompt_file}`
"""

    # ── Quality Gates ─────────────────────────────────────────────────────────

    def write_gate_results(self, iteration: int, results: list[GateResult]) -> Path:
        path = self.iter_dir(iteration) / "gates.json"
        data = [
            {"name": r.name, "status": r.status.value, "output": r.output, "duration_s": r.duration_s}
            for r in results
        ]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return path

    # ── Agent B ───────────────────────────────────────────────────────────────

    def write_b_review(self, iteration: int, content: str) -> Path:
        path = self.iter_dir(iteration) / "b-review.md"
        path.write_text(content)
        return path

    def b_review_path(self, iteration: int) -> Path:
        return self.iter_dir(iteration) / "b-review.md"

    # ── Agent C ───────────────────────────────────────────────────────────────

    def write_c_report(self, iteration: int, content: str) -> Path:
        path = self.iter_dir(iteration) / "c-report.md"
        path.write_text(content)
        return path

    def c_report_path(self, iteration: int) -> Path:
        return self.iter_dir(iteration) / "c-report.md"

    # ── Feedback ──────────────────────────────────────────────────────────────

    def write_feedback(
        self,
        iteration: int,
        content: str,
        outcome: IterationOutcome | None = None,
        gate_results: list[GateResult] | None = None,
    ) -> Path:
        """Write structured feedback for what Agent A must fix next iteration."""
        lines = []

        if outcome:
            lines.append(f"**Failed phase:** {outcome.value}")
            lines.append("")

        if gate_results:
            failed = [g for g in gate_results if g.status != GateStatus.PASS]
            if failed:
                lines.append("**Failed quality gates:**")
                for g in failed:
                    lines.append(f"- {g.name}: {g.output[:400].strip()}")
                lines.append("")

        lines.append(content.strip())

        feedback_text = "\n".join(lines)
        path = self.iter_dir(iteration) / "feedback.md"
        path.write_text(feedback_text)
        return path

    def read_feedback(self, iteration: int) -> str:
        path = self.iter_dir(iteration) / "feedback.md"
        return path.read_text() if path.is_file() else ""

    # ── Metrics ───────────────────────────────────────────────────────────────

    def write_metrics(self, metrics: object) -> Path:
        path = self.run_dir / "metrics.json"
        if hasattr(metrics, "to_dict"):
            path.write_text(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))  # type: ignore[union-attr]
        return path

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_iteration_context(self, iteration: int) -> IterationContext | None:
        d = self.run_dir / f"iter-{iteration:03d}"
        if not d.is_dir():
            return None

        gate_results: list[GateResult] = []
        gates_path = d / "gates.json"
        if gates_path.is_file():
            try:
                raw = json.loads(gates_path.read_text())
                for item in raw:
                    gate_results.append(
                        GateResult(
                            name=item["name"],
                            status=GateStatus(item["status"]),
                            output=item.get("output", ""),
                            duration_s=item.get("duration_s", 0.0),
                        )
                    )
            except Exception:
                pass

        feedback = ""
        feedback_path = d / "feedback.md"
        if feedback_path.is_file():
            feedback = feedback_path.read_text()

        outcome: IterationOutcome | None = None
        # Infer outcome from what feedback exists
        if feedback:
            if "gate_fail" in feedback:
                outcome = IterationOutcome.GATE_FAIL
            elif "verify_fail" in feedback:
                outcome = IterationOutcome.VERIFY_FAIL

        return IterationContext(
            iteration=iteration,
            outcome=outcome,
            gate_results=gate_results,
            feedback=feedback,
        )
