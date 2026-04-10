from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re

from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import (
    REVIEW_RESULT_BLOCK_END,
    REVIEW_RESULT_BLOCK_START,
    REVIEW_VERDICT_FAIL,
    REVIEW_VERDICT_PASS,
    REVIEW_VERDICT_PASS_WITH_ADVISORY,
    VerificationResult,
)
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.store import RunStore


_RESULT_BLOCK_RE = re.compile(
    rf"(?ms){re.escape(REVIEW_RESULT_BLOCK_START)}\s*\n(.*?)\n{re.escape(REVIEW_RESULT_BLOCK_END)}\s*$"
)


def _parse_review_result(output: str) -> tuple[bool, bool] | None:
    """Parse the machine-readable trailer at the end of a review output.

    Returns:
        tuple[passed, advisory_only] when the trailer is valid, else ``None``.
    """
    blocks = list(_RESULT_BLOCK_RE.finditer(output.strip()))
    if len(blocks) != 1:
        return None

    fields: dict[str, str] = {}
    for line in blocks[0].group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()

    verdict = fields.get("verdict", "")
    try:
        blocking_count = int(fields.get("blocking_count", "-1"))
        advisory_count = int(fields.get("advisory_count", "-1"))
    except ValueError:
        return None

    if verdict == REVIEW_VERDICT_FAIL and blocking_count > 0 and advisory_count >= 0:
        return (False, False)
    if verdict == REVIEW_VERDICT_PASS and blocking_count == 0 and advisory_count == 0:
        return (True, False)
    if (
        verdict == REVIEW_VERDICT_PASS_WITH_ADVISORY
        and blocking_count == 0
        and advisory_count > 0
    ):
        return (True, True)
    return None


class ReviewStrategy:
    """Single-agent review: requirements + changed files → APPROVE or feedback."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    def run(
        self,
        requirements_file: Path,
        store: RunStore,
        iteration: int,
        config: ProjectConfig,
        timeout: int,
        max_retries: int,
        logger: Logger,
        verification_session_id: str | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> VerificationResult:
        if config.changed_files:
            changed_section = "\n".join(config.changed_files)
        else:
            changed_section = (
                "(none listed — use git diff / Read to inspect recent changes if needed)"
            )

        consistency_section = ""
        if iteration > 1:
            consistency_section = f"""
## Consistency Check
Previous reviews for this run are saved at:
`{store.run_dir}/iter-*/review.md`

Read your previous review(s) before deciding. Your verdict must be consistent
with prior reviews unless the code has actually changed since then. If the same
issues you flagged before are still present, your verdict must remain FAIL.
"""

        codereview_path = store.base_dir / "codereview.md"
        codereview_section = ""
        if codereview_path.is_file():
            codereview_section = f"""
## Code Quality Guidelines (Advisory)
Read `{codereview_path}` for project-specific conventions and patterns.
Violations of these guidelines do NOT block approval. List them in a
separate "Suggestions" section.
"""

        prompt = f"""You are a senior developer reviewing code for correctness AND quality.

## Important: What This Review Covers
The pipeline has already run all configured quality gates (lint, tests, etc.) and
they have passed. **Do not attempt to re-run linters or test suites.** You do not
have permission to execute arbitrary shell commands here, and doing so is unnecessary.
Your job is to review the *code and requirements* — not to re-verify what the gates
already confirmed.

## Part 1: Requirements Check (Blocking)
Read `{requirements_file}` — this is the source of truth.

For each requirement:
- **Requirement**: [quote it]
- **Status**: MET / NOT MET / PARTIAL
- **Evidence**: specific code references (function names, line behavior) that confirm or deny
{codereview_section}
## Changed Files
The following files were changed in this implementation:
{changed_section}

Read each changed file thoroughly.
{consistency_section}
## Your Review

### Blocking Issues
List anything where requirements are NOT MET or there are bugs/security issues.
These MUST be fixed before approval.

### Suggestions (Advisory)
Code quality improvements, convention violations, naming, patterns.
These do NOT block approval but are recommended.

## Final Output Contract
At the very end of your response, output exactly one machine-readable result block:

{REVIEW_RESULT_BLOCK_START}
verdict: pass | pass_with_advisory | fail
blocking_count: <integer>
advisory_count: <integer>
{REVIEW_RESULT_BLOCK_END}

Rules for the result block:
- Use `pass` only when blocking_count=0 and advisory_count=0
- Use `pass_with_advisory` only when blocking_count=0 and advisory_count>0
- Use `fail` only when blocking_count>0
- Do NOT use these sentinel strings anywhere else in the response
- The result block must be the final thing in the response

Otherwise (blocking issues exist), list exactly what Agent A must fix (blocking issues only)."""

        logger.info("Phase R: Requirements-based code review")
        run_out = self._runner.run(
            prompt,
            session_id=verification_session_id,
            timeout=timeout,
            max_retries=max_retries,
            logger=logger,
            on_output=on_output,
        )
        output = run_out.output
        review_path = store.write_review(iteration, output)
        logger.info(f"Review saved → {review_path}")

        next_sid = run_out.session_id if self._runner.supports_resume else None

        parsed = _parse_review_result(output)
        if parsed is None:
            logger.warn("RESULT: FAIL-CLOSED (review produced malformed verdict block)")
            return VerificationResult(
                passed=False,
                feedback=output,
                next_agent_r_session_id=next_sid,
            )

        passed, advisory_only = parsed
        if advisory_only:
            logger.info("RESULT: PASS with advisory (review)")
            return VerificationResult(
                passed=True,
                advisory_only=True,
                feedback=output,
                next_agent_r_session_id=next_sid,
            )

        if passed:
            logger.info("RESULT: PASS (review)")
            return VerificationResult(
                passed=True,
                feedback="",
                next_agent_r_session_id=next_sid,
            )

        logger.info("RESULT: FAIL (review — changes requested)")
        return VerificationResult(
            passed=False,
            feedback=output,
            next_agent_r_session_id=next_sid,
        )
