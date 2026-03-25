"""Agent A context builder — constructs iteration prompts from run history.

Separated from store.py so prompt templates are easy to find and edit
without navigating file I/O code.
"""

from __future__ import annotations

import json
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
                    gate_lines.append(f"    → {g.output[:300].strip()}")
            lines.append("**Quality Gates:**")
            lines.extend(gate_lines)

        if self.feedback:
            lines.append("\n**Feedback (what must be fixed):**")
            lines.append(self.feedback.strip())

        return "\n".join(lines)


def load_iteration_context(run_dir: Path, iteration: int) -> IterationContext | None:
    """Load gate results and feedback for a past iteration from disk."""
    d = run_dir / f"iter-{iteration:03d}"
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


def build_full_context(run_dir: Path, iteration: int, prompt_file: Path) -> str:
    """Build Agent A's prompt for a fresh-session iteration (all history included)."""
    history_sections: list[str] = []
    for i in range(1, iteration):
        ctx = load_iteration_context(run_dir, i)
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


def build_resume_context(run_dir: Path, iteration: int, prompt_file: Path) -> str:
    """Build Agent A's prompt for a resumed-session iteration (previous iteration only)."""
    ctx = load_iteration_context(run_dir, iteration - 1)
    latest = (
        ctx.to_prompt_section()
        if ctx
        else "(No structured history for the previous iteration.)"
    )
    return f"""Read `{prompt_file}` for the full requirements.

## Iteration {iteration} — continue in the same CLI session

> **CRITICAL — READ THIS BEFORE DOING ANYTHING ELSE:**
>
> 1. **Do NOT trust your session memory.** Your memory of "what you wrote" may not
>    reflect the actual state of files on disk. Read the relevant files first.
> 2. **You MUST call Edit or Write to make changes.** Describing fixes in text has
>    NO effect — the pipeline checks `git status`, not your words.
> 3. **LOOP_COMPLETE is only valid after you have called Edit or Write this iteration.**
>    Outputting LOOP_COMPLETE without any file edits causes the pipeline to drop your
>    session and force a full restart.

Start by reading the files that need to change. Then make targeted fixes with Edit or Write.

{latest}

When all fixes are applied (Edit/Write called at least once), output on its own line:
`LOOP_COMPLETE`
"""
