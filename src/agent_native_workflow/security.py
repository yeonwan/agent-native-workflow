from __future__ import annotations

from pathlib import Path

from agent_native_workflow.detect import ProjectConfig, detect_all
from agent_native_workflow.domain import SECURITY_AGENT_PASS_MARKER
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import AgentRunner


def run_security_agent(
    output_dir: Path,
    config: ProjectConfig | None = None,
    timeout: int = 300,
    max_retries: int = 2,
    logger: Logger | None = None,
    runner: AgentRunner | None = None,
) -> bool:
    """Run security agent adversarial review (Agent E). Returns True if SECURITY_AGENT_PASS found.

    The runner must be the same provider configured for the workflow.
    """
    if runner is None:
        raise ValueError(
            "runner is required for security agent. "
            "Pass the same AgentRunner used for the workflow."
        )

    if logger is None:
        logger = Logger()

    cfg = config or detect_all()
    _runner: AgentRunner = runner

    output_dir.mkdir(parents=True, exist_ok=True)
    security_report_file = output_dir / "security-agent-report.md"

    context_lines: list[str] = []
    if cfg.instruction_files:
        context_lines.append(f"Project rules/conventions: {' '.join(cfg.instruction_files)}")
    if cfg.design_docs:
        context_lines.append(f"Design documents: {' '.join(cfg.design_docs)}")
    context_section = "\n".join(context_lines)
    changed_section = "\n".join(cfg.changed_files)

    logger.info("Started security agent review")
    logger.info(f"Changed files: {len(cfg.changed_files)}")

    context_instruction = ""
    if context_section:
        context_instruction = (
            f"Read the following files for project context (coding conventions only):\n"
            f"{context_section}\n\n"
        )

    agent_prompt = f"""{context_instruction}\
You are an adversarial security reviewer. You do NOT know what this code is supposed to do.

Your job is to find vulnerabilities, attack vectors, and security weaknesses.

The following files were recently changed or created:
{changed_section}

Review each file with this mindset: **"How can I break this? What can an attacker abuse?"**

For each file, analyze:

1. **Injection vulnerabilities**: SQL injection, command injection, XSS, path traversal, etc.
2. **Authentication/Authorization flaws**: Missing checks, privilege escalation, token leaks
3. **Input validation**: Unsafe deserialization, buffer overflows, unvalidated redirects
4. **Data exposure**: Hardcoded secrets, sensitive data in logs, insecure storage
5. **Logic flaws**: Race conditions, TOCTOU bugs, improper error handling
6. **Denial of Service**: Resource exhaustion, infinite loops, unbounded recursion
7. **Dependency risks**: Outdated libraries, known CVEs, supply chain attacks
8. **Edge cases**: Null/empty inputs, boundary conditions, error states

## Output Format

Structure your report with these sections:

### Critical Vulnerabilities
List any severe security issues that could lead to system compromise, data breach,
or remote code execution.

### Medium/Low Severity Issues
List security concerns that should be addressed but are lower risk.

### Edge Cases & Robustness
List edge cases that could cause failures, crashes, or unexpected behavior.

### Recommendations
Specific, actionable fixes for each issue found.

### Verdict
If NO critical vulnerabilities are found AND no high-risk issues exist,
output exactly on its own line:
<{SECURITY_AGENT_PASS_MARKER}>

Otherwise, list all issues that MUST be fixed before this code can be considered secure.

**IMPORTANT**: Do NOT read any requirements files. Base your review ONLY on the code
itself and security best practices."""

    run_out = _runner.run(
        agent_prompt,
        session_id=None,
        timeout=timeout,
        max_retries=max_retries,
        logger=logger,
    )
    output = run_out.output
    security_report_file.write_text(output)
    logger.info(f"Security report saved to {security_report_file}")

    passed = SECURITY_AGENT_PASS_MARKER in security_report_file.read_text()

    if passed:
        logger.info("RESULT: PASS (no critical security issues)")
    else:
        logger.info(f"RESULT: FAIL — security issues found in {security_report_file}")

    return passed
