from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig, detect_all
from agent_native_workflow.domain import TRIANGULAR_PASS_MARKER
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.store import RunStore


def run_triangular_verification(
    requirements_file: Path,
    store: RunStore,
    iteration: int,
    config: ProjectConfig | None = None,
    timeout: int = 300,
    max_retries: int = 2,
    logger: Logger | None = None,
    runner: AgentRunner | None = None,
    c_runner: AgentRunner | None = None,
) -> bool:
    """Run triangular verification (Agent B + Agent C).

    Writes artifacts to store.iter_dir(iteration):
      - b-review.md   ← Agent B blind review
      - c-report.md   ← Agent C discrepancy report

    Agent C receives the path to b-review.md written this iteration,
    not a hardcoded global path — so paths stay correct across runs.

    Returns True if TRIANGULAR_PASS found in Agent C's report.
    """
    if runner is None:
        raise ValueError(
            "runner is required. Pass the same AgentRunner configured for the workflow."
        )

    if logger is None:
        logger = Logger()

    cfg = config or detect_all()

    context_lines: list[str] = []
    if cfg.instruction_files:
        context_lines.append(f"Project rules/conventions: {' '.join(cfg.instruction_files)}")
    if cfg.design_docs:
        context_lines.append(f"Design documents: {' '.join(cfg.design_docs)}")
    context_section = "\n".join(context_lines)
    changed_section = "\n".join(cfg.changed_files)

    logger.info(f"Started triangular verification (iteration {iteration})")
    logger.info(f"Requirements: {requirements_file}")
    logger.info(f"Changed files: {len(cfg.changed_files)}")

    # ── Agent B: Blind Review ────────────────────────────────────────────────
    # Reads code only, never the requirements document.
    logger.info("Phase B: Blind review (code only — no requirements)")

    context_instruction = ""
    if context_section:
        context_instruction = (
            f"Read the following files for project context:\n{context_section}\n\n"
        )

    agent_b_prompt = f"""{context_instruction}\
Do NOT read the requirements document at `{requirements_file}` or any requirements file.

The following files were recently changed or created:
{changed_section}

For each file:
1. Describe what this code does (behavior and intent, not just structure)
2. List any convention/rule violations found in project rules
3. List potential issues, edge cases, or bugs

Output your analysis as structured markdown."""

    output_b = runner.run(agent_b_prompt, timeout=timeout, max_retries=max_retries, logger=logger)
    b_review_path = store.write_b_review(iteration, output_b)
    logger.info(f"Blind review saved → {b_review_path}")

    # ── Agent C: Discrepancy Report ──────────────────────────────────────────
    # Reads requirements + Agent B's review. Never reads code directly.
    logger.info("Phase C: Discrepancy report (requirements vs blind review)")

    agent_c_prompt = f"""\
You are Agent C in a triangular verification process.

Read these two documents carefully:
1. `{requirements_file}` — original requirements (source of truth)
2. `{b_review_path}` — blind code analysis written by Agent B this iteration

Do NOT read any code files directly.

Compare them and produce a discrepancy report with these sections:

## Requirements Met
List each requirement confirmed by the blind review, with evidence.

## Requirements Missed
Requirements in the requirements doc NOT reflected in the blind review.

## Extra Behavior
Behavior described in the blind review NOT in the requirements.

## Potential Bugs
Where the blind review contradicts or conflicts with requirements.

## Verdict
If ALL requirements are met and no critical issues found, output exactly on its own line:
{TRIANGULAR_PASS_MARKER}

Otherwise, list each issue that must be fixed."""

    _c_runner = c_runner or runner
    output_c = _c_runner.run(agent_c_prompt, timeout=timeout, max_retries=max_retries, logger=logger)
    c_report_path = store.write_c_report(iteration, output_c)
    logger.info(f"Discrepancy report saved → {c_report_path}")

    passed = TRIANGULAR_PASS_MARKER in output_c

    if passed:
        logger.info("RESULT: PASS")
    else:
        logger.info(f"RESULT: FAIL — issues found in {c_report_path}")

    return passed
