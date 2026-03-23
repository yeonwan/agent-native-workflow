from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agent_native_workflow.config import WorkflowConfig
from agent_native_workflow.detect import (
    ProjectConfig,
    detect_all,
    files_changed_since,
    snapshot_working_tree,
)
from agent_native_workflow.domain import (
    GateResult,
    GateStatus,
    IterationMetrics,
    IterationOutcome,
    PipelineMetrics,
)
from agent_native_workflow.log import Logger
from agent_native_workflow.prompt_loader import load_prompt, load_prompt_title
from agent_native_workflow.requirements_loader import is_text_format, load_requirements
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.runners.copilot import apply_text_output
from agent_native_workflow.runners.factory import runner_for
from agent_native_workflow.store import RunStore
from agent_native_workflow.verify import run_triangular_verification
from agent_native_workflow.visualization.base import PipelinePhase, Visualizer
from agent_native_workflow.visualization.plain import PlainVisualizer

_GATE_OUTPUT_LIMIT = 500
_UNSAFE_PATTERN = re.compile(r"\$\(|`|;\s*rm\s|&&\s*rm\s|>\s*/dev/")


def _is_safe_command(cmd: str) -> bool:
    return not _UNSAFE_PATTERN.search(cmd)


def _run_gate_command(cmd: str, timeout: int = 300) -> tuple[bool, str]:
    if not _is_safe_command(cmd):
        return False, f"BLOCKED: command contains unsafe patterns: {cmd}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {cmd}"
    except Exception as e:
        return False, f"Command failed: {e}"


def _run_gates_sequential(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    timeout: int,
    logger: Logger,
) -> tuple[bool, str, list[GateResult]]:
    results: list[GateResult] = []
    gate_pass = True
    gate_output = ""

    for name, cmd in gates:
        if not gate_pass:
            break
        logger.info(f"[Phase 2] Running {name}: {cmd}")
        passed, output = _run_gate_command(cmd, timeout)
        status = GateStatus.PASS if passed else GateStatus.FAIL
        results.append(GateResult(name=name, status=status, output=output[:_GATE_OUTPUT_LIMIT]))
        if passed:
            logger.info(f"[Phase 2] {name}: PASS")
        else:
            logger.info(f"[Phase 2] {name}: FAIL")
            gate_output = f"{name} ({cmd}) FAILED:\n{output}"
            gate_pass = False

    for cname, cfunc in callable_gates:
        if not gate_pass:
            break
        logger.info(f"[Phase 2] Running callable:{cname}")
        try:
            passed, output = cfunc()
        except Exception as e:
            passed, output = False, f"Gate '{cname}' raised: {e}"
        status = GateStatus.PASS if passed else GateStatus.FAIL
        results.append(
            GateResult(name=f"callable:{cname}", status=status, output=output[:_GATE_OUTPUT_LIMIT])
        )
        if passed:
            logger.info(f"[Phase 2] callable:{cname}: PASS")
        else:
            logger.info(f"[Phase 2] callable:{cname}: FAIL")
            gate_output = f"callable:{cname} FAILED:\n{output}"
            gate_pass = False

    return gate_pass, gate_output, results


def _run_gates_parallel(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    timeout: int,
    logger: Logger,
) -> tuple[bool, str, list[GateResult]]:
    total = len(gates) + len(callable_gates)
    logger.info(f"[Phase 2] Running {total} gates in parallel")
    results: list[GateResult] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max(total, 1)) as executor:
        future_map: dict[object, str] = {}
        for name, cmd in gates:
            future_map[executor.submit(_run_gate_command, cmd, timeout)] = name
        for cname, cfunc in callable_gates:

            def _wrapped(fn: Callable[[], tuple[bool, str]], n: str) -> tuple[bool, str]:
                try:
                    return fn()
                except Exception as e:
                    return False, f"Gate '{n}' raised: {e}"

            future_map[executor.submit(_wrapped, cfunc, cname)] = f"callable:{cname}"

        for future in as_completed(future_map):
            name = future_map[future]
            passed, output = future.result()
            status = GateStatus.PASS if passed else GateStatus.FAIL
            results.append(GateResult(name=name, status=status, output=output[:_GATE_OUTPUT_LIMIT]))
            if passed:
                logger.info(f"[Phase 2] {name}: PASS")
            else:
                logger.info(f"[Phase 2] {name}: FAIL")
                failures.append(f"{name} FAILED:\n{output}")

    if failures:
        return False, "\n\n".join(failures), results
    return True, "", results


def _run_quality_gates(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    use_parallel: bool,
    timeout: int,
    logger: Logger,
) -> tuple[bool, str, list[GateResult]]:
    total_gates = len(gates) + len(callable_gates)
    if not total_gates:
        logger.info("[Phase 2] No quality gates configured — skipping")
        return True, "", []
    if use_parallel:
        return _run_gates_parallel(
            gates=gates, callable_gates=callable_gates, timeout=timeout, logger=logger
        )
    return _run_gates_sequential(
        gates=gates, callable_gates=callable_gates, timeout=timeout, logger=logger
    )


def _run_implementation_phase(
    *,
    iteration: int,
    prompt_file: Path | None,
    requirements_file: Path,
    store: RunStore,
    runner: AgentRunner,
    timeout: int,
    max_retries: int,
    logger: Logger,
) -> None:
    """Phase 1: Agent A implementation/fix.

    On iteration 1:
      - If prompt_file exists: reads it (.yaml rendered, .md as-is)
      - If prompt_file is None/missing: uses requirements_file directly as the prompt
        (Jira ticket workflow — requirements contain everything Agent A needs)
    On iteration 2+: receives structured context built from all previous iterations'
                     gate results, feedback, and failure reasons.
    """
    # Injected into every Agent A prompt regardless of iteration
    _AGENT_A_SYSTEM = (
        "> **PIPELINE RULES — read before acting**\n"
        "> - Requirements file: `{requirements_file}`\n"
        "> - Do NOT run `git commit`, `git push`, or any git write command.\n"
        ">   The pipeline manages git state. Only read and edit files.\n"
        "> - Do NOT add tests unless the requirements explicitly ask for them.\n"
        "> - When done, output `LOOP_COMPLETE` on its own line.\n\n"
    )

    effective_prompt_file = prompt_file if (prompt_file and prompt_file.is_file()) else None

    if iteration == 1:
        if effective_prompt_file:
            prompt_text = load_prompt(effective_prompt_file)
        else:
            # No PROMPT file — use requirements as the full task spec for Agent A
            logger.info("[Phase 1] No PROMPT file found — using requirements as task spec")
            prompt_text = load_prompt(requirements_file)
    else:
        source_file = effective_prompt_file or requirements_file
        prompt_text = store.build_agent_a_context(iteration, source_file)

    prompt_text = _AGENT_A_SYSTEM.format(requirements_file=requirements_file) + prompt_text

    output = runner.run(prompt_text, timeout=timeout, max_retries=max_retries, logger=logger)
    store.write_agent_output(iteration, output)

    # For text-only runners (e.g. Copilot), apply output to working directory
    if not runner.supports_file_tools:
        logger.info(
            f"[Phase 1] Runner '{runner.provider_name}' is text-only — applying output to files"
        )
        apply_text_output(output, logger=logger)


def run_pipeline(
    prompt_file: Path | None,
    requirements_file: Path,
    store: RunStore | None = None,
    max_iterations: int = 5,
    agent_timeout: int = 300,
    max_retries: int = 2,
    parallel_gates: bool | None = None,
    config: ProjectConfig | None = None,
    logger: Logger | None = None,
    custom_gates: list[tuple[str, Callable[[], tuple[bool, str]]]] | None = None,
    runner: AgentRunner | None = None,
    verify_runner: AgentRunner | None = None,
    c_runner: AgentRunner | None = None,
    visualizer: Visualizer | None = None,
    workflow_config: WorkflowConfig | None = None,
) -> bool:
    """Run the AI-native workflow pipeline (A → quality gates → B+C triangulation).

    All agent communication goes through RunStore:
    - Each run is isolated in a timestamped directory
    - Each iteration has its own subdirectory with all artifacts
    - Agent A receives structured context built from all previous iterations
    - Agent B/C paths are resolved from the store (no hardcoded globals)

    Args:
        store: RunStore instance. If None, creates one at .agent-native-workflow/.
        runner: AgentRunner for ALL agents (A, B, C use the same CLI provider).
        workflow_config: WorkflowConfig — determines cli_provider and other settings.

    Returns:
        True if the pipeline converged (gates + triangulation passed).
    """
    wcfg = workflow_config or WorkflowConfig.resolve()

    # Build store
    if store is None:
        store = RunStore(base_dir=Path(".agent-native-workflow"))

    # Build runner (all agents use same provider)
    from agent_native_workflow.domain import AgentConfig
    agent_cfg = wcfg.agent_config or AgentConfig()

    def _model_for(perms_model: str, global_override: str) -> dict[str, object]:
        """Resolve model: CLI flag > agent-config.yaml > empty (provider default)."""
        m = global_override or perms_model
        return {"model": m} if m else {}

    if runner is None:
        runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_a.allowed_tools,
            permission_mode=agent_cfg.agent_a.permission_mode,
            **_model_for(agent_cfg.agent_a.model, wcfg.model),
        )

    if verify_runner is None:
        # Agent B: blind code reviewer
        verify_runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_b.allowed_tools,
            permission_mode=agent_cfg.agent_b.permission_mode,
            **_model_for(agent_cfg.agent_b.model, wcfg.model_verify or wcfg.model),
        )

    if c_runner is None:
        # Agent C: requirements vs review judge (read-only, can use cheaper model)
        c_runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_c.allowed_tools,
            permission_mode=agent_cfg.agent_c.permission_mode,
            **_model_for(agent_cfg.agent_c.model, wcfg.model_verify or wcfg.model),
        )

    # Start a new isolated run
    config_snapshot = {
        "cli_provider": wcfg.cli_provider,
        "max_iterations": max_iterations,
        "prompt_file": str(prompt_file),
        "requirements_file": str(requirements_file),
        "model_a": agent_cfg.agent_a.model or wcfg.model,
        "model_b": agent_cfg.agent_b.model or wcfg.model_verify or wcfg.model,
        "model_c": agent_cfg.agent_c.model or wcfg.model_verify or wcfg.model,
    }
    run_dir = store.start_run(config_snapshot=config_snapshot)

    # If requirements file is non-text (.docx, .pdf), convert to .md snapshot
    # so all agents can read it natively. agents_requirements_file points to
    # the canonical readable path used in all agent prompts.
    if not is_text_format(requirements_file):
        requirements_text = load_requirements(requirements_file)
        agents_requirements_file = store.write_requirements_snapshot(requirements_text)
    else:
        agents_requirements_file = requirements_file

    if logger is None:
        logger = Logger(log_file=run_dir / "execution.log")

    if visualizer is None:
        visualizer = PlainVisualizer()

    # Wire logger → visualizer so TUI log panel receives messages
    logger.set_log_callback(visualizer.on_log)

    cfg = config or detect_all(base_branch=wcfg.base_branch)
    # Apply explicit command overrides from config file / env vars
    if wcfg.lint_cmd:
        cfg.lint_cmd = wcfg.lint_cmd
    if wcfg.test_cmd:
        cfg.test_cmd = wcfg.test_cmd
    metrics = PipelineMetrics(started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    use_parallel = (
        parallel_gates
        if parallel_gates is not None
        else os.environ.get("PARALLEL_GATES", "").lower() in ("true", "1", "yes")
    )

    os.environ.pop("CLAUDECODE", None)

    shutdown_requested = False

    def _signal_handler(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        sig_name = signal.Signals(signum).name
        logger.warn(f"Received {sig_name}, will exit after current phase...")
        shutdown_requested = True

    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    start_time = time.time()

    logger.info(f"=== agent-native-workflow (provider: {runner.provider_name}) ===")
    logger.info(cfg.print_config())
    logger.info(f"Max iterations: {max_iterations}")
    logger.info(f"Prompt: {prompt_file}")
    logger.info(f"Requirements: {requirements_file}")
    logger.info(f"Run dir: {run_dir}")
    logger.info(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    visualizer.on_pipeline_start(wcfg)

    converged = False

    try:
        for iteration in range(1, max_iterations + 1):
            if shutdown_requested:
                logger.warn("Shutdown requested, stopping pipeline")
                break

            iter_start = time.time()
            iter_metrics = IterationMetrics(iteration=iteration)

            logger.info(f"--- Iteration {iteration} / {max_iterations} ---")
            visualizer.on_iteration_start(iteration, max_iterations)

            # ── Phase 1: Implementation ──────────────────────────────────────
            logger.phase_start("phase1_implement", iteration=iteration)
            visualizer.on_phase_start(PipelinePhase.IMPLEMENT)

            # Snapshot working tree before Agent A so we can track exactly what it changed
            before_snapshot = snapshot_working_tree()

            _run_implementation_phase(
                iteration=iteration,
                prompt_file=prompt_file,
                requirements_file=agents_requirements_file,
                store=store,
                runner=runner,
                timeout=agent_timeout,
                max_retries=max_retries,
                logger=logger,
            )

            # Update changed_files to only what Agent A touched this iteration
            agent_changed = files_changed_since(before_snapshot)
            if agent_changed:
                cfg.changed_files = agent_changed
                files_str = ", ".join(agent_changed[:10])
                logger.info(f"[Phase 1] Agent A changed {len(agent_changed)} file(s): {files_str}")
            else:
                logger.info("[Phase 1] No file changes detected from Agent A")

            iter_metrics.phase1_done = True
            logger.phase_end("phase1_implement", "completed", iteration=iteration)
            visualizer.on_phase_end(PipelinePhase.IMPLEMENT, "pass")

            if shutdown_requested:
                break

            # ── Phase 2: Quality Gates ────────────────────────────────────────
            logger.phase_start("phase2_quality_gates", iteration=iteration)
            visualizer.on_phase_start(PipelinePhase.QUALITY_GATES)

            gates: list[tuple[str, str]] = []
            if cfg.lint_cmd:
                gates.append(("lint", cfg.lint_cmd))
            if cfg.test_cmd:
                gates.append(("test", cfg.test_cmd))

            callable_gates = custom_gates or []

            gate_pass, gate_output, gate_results = _run_quality_gates(
                gates=gates,
                callable_gates=callable_gates,
                use_parallel=use_parallel,
                timeout=agent_timeout,
                logger=logger,
            )
            iter_metrics.gate_results = gate_results

            # Always persist structured gate results
            store.write_gate_results(iteration, gate_results)

            if not gate_pass:
                store.write_feedback(
                    iteration,
                    gate_output,
                    outcome=IterationOutcome.GATE_FAIL,
                    gate_results=gate_results,
                )
                iter_metrics.outcome = IterationOutcome.GATE_FAIL
                iter_metrics.duration_s = round(time.time() - iter_start, 2)
                metrics.iterations.append(iter_metrics)
                logger.phase_end("phase2_quality_gates", "fail", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.QUALITY_GATES, "fail")
                logger.info(f"[Phase 2] FAILED — looping back (took {iter_metrics.duration_s}s)")
                continue

            logger.phase_end("phase2_quality_gates", "pass", iteration=iteration)
            visualizer.on_phase_end(PipelinePhase.QUALITY_GATES, "pass")

            if shutdown_requested:
                break

            # ── Phase 3: Triangular Verification (B + C + B consensus) ────────
            logger.phase_start("phase3_triangular_verify", iteration=iteration)
            visualizer.on_phase_start(PipelinePhase.TRIANGULAR_VERIFY)

            task_title = ""
            if prompt_file:
                task_title = load_prompt_title(prompt_file)

            passed, verify_feedback = run_triangular_verification(
                requirements_file=agents_requirements_file,
                store=store,
                iteration=iteration,
                config=cfg,
                timeout=agent_timeout,
                max_retries=max_retries,
                logger=logger,
                runner=verify_runner,
                c_runner=c_runner,
                task_title=task_title,
            )

            if passed:
                iter_metrics.verification_status = GateStatus.PASS
                logger.phase_end("phase3_triangular_verify", "pass", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "pass")
            else:
                iter_metrics.verification_status = GateStatus.FAIL
                feedback_content = (
                    verify_feedback
                    or "Triangular verification failed but no report found."
                )
                store.write_feedback(
                    iteration,
                    feedback_content,
                    outcome=IterationOutcome.VERIFY_FAIL,
                    gate_results=gate_results,
                )
                iter_metrics.outcome = IterationOutcome.VERIFY_FAIL
                iter_metrics.duration_s = round(time.time() - iter_start, 2)
                metrics.iterations.append(iter_metrics)
                logger.phase_end("phase3_triangular_verify", "fail", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "fail")
                logger.info(f"[Phase 3] FAILED — looping back (took {iter_metrics.duration_s}s)")
                continue

            if shutdown_requested:
                break

            # ── Convergence ───────────────────────────────────────────────────
            iter_metrics.outcome = IterationOutcome.PASS
            iter_metrics.duration_s = round(time.time() - iter_start, 2)
            metrics.iterations.append(iter_metrics)

            total_time = round(time.time() - start_time, 2)
            logger.info("")
            logger.info("=== LOOP_COMPLETE ===")
            logger.info(f"Finished in {iteration} iteration(s), total {total_time}s")
            logger.info(f"Ended: {time.strftime('%Y-%m-%d %H:%M:%S')}")

            converged = True
            break

    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)

        total_time = round(time.time() - start_time, 2)
        metrics.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        metrics.total_duration_s = total_time
        metrics.total_iterations = len(metrics.iterations)
        metrics.converged = converged
        store.write_metrics(metrics)

        visualizer.on_pipeline_end(metrics)

    if not converged:
        logger.info("")
        logger.info("=== MAX ITERATIONS REACHED ===")
        logger.info(f"Completed {max_iterations} iterations without full convergence.")
        logger.info(f"Total time: {total_time}s")

    return converged
