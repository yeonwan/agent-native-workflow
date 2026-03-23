from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig, detect_all
from agent_native_workflow.domain import CONSENSUS_AGREE_MARKER, TRIANGULAR_PASS_MARKER
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
    task_title: str = "",
) -> tuple[bool, str]:
    """Run triangular verification: B (Senior Dev) → C (PM) → B (Confirmation).

    Flow:
      1. Agent B reviews changed code as a senior developer
      2. Agent C checks requirements as a product manager
      3. If C passes, Agent B confirms the PM's assessment (consensus)

    Writes artifacts to store.iter_dir(iteration):
      - b-review.md   ← Agent B senior dev review
      - c-report.md   ← Agent C PM acceptance report
      - b-confirm.md  ← Agent B confirmation (only when C passes)

    Returns (passed, feedback) where feedback is the relevant report
    content on failure, or empty string on success.
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

    # ── Phase B: Senior Developer Review ─────────────────────────────────────
    logger.info("Phase B: Senior developer review")

    context_instruction = ""
    if context_section:
        context_instruction = (
            f"\nRead the following files for project context:\n{context_section}\n"
        )

    task_hint = f'Task context: "{task_title}"' if task_title else "A task was implemented."
    # B knows what kind of change was made but NOT the acceptance criteria.
    agent_b_prompt = f"""\
You are a **Senior Developer** performing a code review.

{task_hint}
You will NOT see the detailed acceptance criteria — focus on what the code actually does.
{context_instruction}
Do NOT read `{requirements_file}` or any requirements/specification file.

The following files were recently changed or created:
{changed_section}

Review each changed file as a senior developer would:

1. **What it does**: Describe the behavior and intent of the changes. \
What feature or fix was implemented? Be specific — quote function names, \
parameters, return values, and output strings.
2. **Correctness**: Does the implementation look correct? Are there logic errors?
3. **Completeness**: Does the change feel complete, or are there obvious missing pieces?
4. **Code quality**: Naming, structure, error handling.
5. **Edge cases & risks**: What could go wrong?

Be concrete and specific. The more precisely you describe what the code does, \
the better the downstream review will be.
Output structured markdown."""

    output_b = runner.run(agent_b_prompt, timeout=timeout, max_retries=max_retries, logger=logger)
    b_review_path = store.write_b_review(iteration, output_b)
    logger.info(f"Senior dev review saved → {b_review_path}")

    # ── Phase C: Product Manager Acceptance ──────────────────────────────────
    logger.info("Phase C: PM acceptance review (requirements vs dev review)")

    _c_runner = c_runner or runner
    agent_c_prompt = f"""\
You are a **Product Manager** performing acceptance review.

Read these two documents carefully:
1. `{requirements_file}` — the requirements specification (source of truth)
2. `{b_review_path}` — a senior developer's code review of the implementation

Do NOT read any code files directly. Base your assessment entirely on the \
developer's review and the requirements.

## Your Task

Go through each requirement and assess whether the developer's review provides \
evidence that it is met.

### Requirement Status

For EACH requirement or acceptance criterion in the requirements document:

- **Requirement**: [quote or paraphrase the requirement]
- **Status**: MET / NOT MET / UNCLEAR
- **Evidence**: What in the developer's review supports this? If the developer \
described behavior that satisfies the requirement — even without using the exact \
same words — that counts as evidence.

### Concerns

Any issues raised in the developer's review that could affect requirements.

### Verdict

If ALL requirements have status MET and no blocking concerns exist, output \
exactly on its own line:
{TRIANGULAR_PASS_MARKER}

If any requirement is NOT MET, list what must be fixed.
If requirements are UNCLEAR, note what needs clarification — but do NOT fail \
solely because the developer did not use the exact wording from requirements."""

    output_c = _c_runner.run(
        agent_c_prompt, timeout=timeout, max_retries=max_retries, logger=logger
    )
    c_report_path = store.write_c_report(iteration, output_c)
    logger.info(f"PM acceptance report saved → {c_report_path}")

    c_passed = TRIANGULAR_PASS_MARKER in output_c

    if not c_passed:
        logger.info("RESULT: FAIL (PM found unmet requirements)")
        return False, output_c

    # ── Phase B2: Senior Developer Confirmation (consensus) ──────────────────
    logger.info("Phase B2: Senior dev confirmation (consensus round)")

    agent_b_confirm_prompt = f"""\
You are a **Senior Developer** providing final technical sign-off.

The Product Manager has reviewed the implementation against requirements and \
believes all requirements are met. Before we ship, you need to confirm.

Read these two documents:
1. `{c_report_path}` — the PM's acceptance report (with per-requirement status)
2. `{b_review_path}` — your own earlier technical review

Review the PM's assessment from a technical perspective:

- Did the PM correctly interpret your technical findings?
- Are there issues you flagged that the PM overlooked or dismissed?
- Is there anything technically incorrect in the PM's verdict?

## Your Decision

**CONFIRM** if the PM's assessment is technically sound — they correctly \
understood your review and the requirements are genuinely met.

**OBJECT** if the PM made incorrect technical assumptions, overlooked issues \
you raised, or marked something as MET when your review flagged problems.

If you CONFIRM, output exactly on its own line:
{CONSENSUS_AGREE_MARKER}

If you OBJECT, explain your technical objections. Do NOT output the marker."""

    output_b_confirm = runner.run(
        agent_b_confirm_prompt, timeout=timeout, max_retries=max_retries, logger=logger
    )
    store.write_b_confirmation(iteration, output_b_confirm)
    logger.info(f"Senior dev confirmation saved → {store.b_confirmation_path(iteration)}")

    b_confirmed = CONSENSUS_AGREE_MARKER in output_b_confirm

    if b_confirmed:
        logger.info("RESULT: PASS (PM + Senior Dev consensus)")
        return True, ""

    logger.info("RESULT: FAIL (Senior Dev objected to PM's assessment)")
    return False, output_b_confirm
