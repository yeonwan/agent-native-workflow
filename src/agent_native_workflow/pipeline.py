from __future__ import annotations

import os
import signal
import subprocess as _sp
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent_native_workflow.config import WorkflowConfig
from agent_native_workflow.detect import (
    ProjectConfig,
    detect_all,
    files_changed_since,
    snapshot_working_tree,
)
from agent_native_workflow.domain import (
    GateStatus,
    IterationMetrics,
    IterationOutcome,
    PipelineMetrics,
)
from agent_native_workflow.gates import run_quality_gates
from agent_native_workflow.log import Logger
from agent_native_workflow.notify import send_notification
from agent_native_workflow.prompt_loader import load_prompt, load_prompt_title
from agent_native_workflow.requirements_loader import (
    is_text_format,
    load_requirements,
)
from agent_native_workflow.runners.base import AgentRunner
from agent_native_workflow.runners.copilot import apply_text_output
from agent_native_workflow.runners.factory import runner_for
from agent_native_workflow.store import RunStore
from agent_native_workflow.strategies.factory import (
    build_verification_strategy,
)
from agent_native_workflow.visualization.base import (
    PipelinePhase,
    Visualizer,
)
from agent_native_workflow.visualization.plain import PlainVisualizer

# Module-level shutdown event shared between pipeline, UI, and runners.
# When set, runners should terminate their subprocesses and the pipeline
# should stop after the current phase.
_shutdown_event = threading.Event()


def _get_head_hash() -> str:
    """Return current HEAD commit hash, or empty string."""
    try:
        r = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (_sp.SubprocessError, FileNotFoundError):
        return ""


def _audit_post_phase1(
    before_head: str,
    before_snapshot: dict[str, str],
    logger: Logger,
) -> None:
    """Post-execution audit: revert commits + detect file deletions."""
    # 1. Unauthorized commit detection
    after_head = _get_head_hash()
    if before_head and after_head and after_head != before_head:
        logger.warn(
            f"[Audit] Unauthorized commit detected: "
            f"{after_head[:8]}. Reverting to {before_head[:8]}."
        )
        _sp.run(
            ["git", "reset", "--soft", before_head],
            capture_output=True, timeout=10,
        )

    # 2. Unexpected file deletion detection
    # Snapshot keys are now file paths (not porcelain lines).
    after_snapshot = snapshot_working_tree()
    deleted = [
        fp for fp in before_snapshot
        if fp not in after_snapshot and before_snapshot[fp] != ""
    ]
    if deleted:
        files_str = ", ".join(deleted[:10])
        logger.warn(
            f"[Audit] {len(deleted)} unexpected file "
            f"deletion(s) detected: {files_str}"
        )

    # 3. Sensitive file modification detection
    _SENSITIVE_PATTERNS = (
        ".env", ".git/config", ".gitconfig",
        ".ssh/", ".npmrc", ".pypirc",
    )
    all_changed: set[str] = set()
    for filepath, hash_after in after_snapshot.items():
        hash_before = before_snapshot.get(filepath)
        if hash_before is None or hash_before != hash_after:
            all_changed.add(filepath)
    sensitive_touched = [
        f for f in all_changed
        if any(f == p or f.startswith(p) for p in _SENSITIVE_PATTERNS)
    ]
    if sensitive_touched:
        logger.warn(
            f"[Audit] Sensitive file(s) modified: "
            f"{', '.join(sensitive_touched)}"
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
    project_type: str = "",
    session_id: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> str | None:
    """Phase 1: Agent A implementation/fix.

    On iteration 1:
      - If prompt_file exists: reads it (.yaml rendered, .md as-is)
      - If prompt_file is None/missing: uses requirements_file directly as the prompt
        (Jira ticket workflow — requirements contain everything Agent A needs)
    On iteration 2+: receives structured context built from all previous iterations'
                     gate results, feedback, and failure reasons.

    Returns:
        ``session_id`` from the runner when the provider supports resume, else ``None``.
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

    # JVM projects: guide Agent A to introspect binary dependencies
    _JVM_DEPENDENCY_HINT = (
        "> **JVM Dependency Introspection** — When the requirements "
        "reference classes\n"
        "> from external libraries (JARs) that you cannot read as "
        "source code:\n"
        "> 1. Find the JAR: `find ~/.gradle/caches "
        "~/.m2/repository -name '<artifact>*.jar' "
        "-not -name '*sources*' 2>/dev/null | head -5`\n"
        "> 2. List classes: `jar tf <path>.jar "
        "| grep '\\.class$' | head -30`\n"
        "> 3. Inspect a class: `javap -public "
        "-classpath <path>.jar <ClassName>`\n"
        "> 4. Use these signatures to write correct code "
        "— do NOT guess field names or method signatures.\n"
        "> Do this BEFORE writing code that depends on "
        "external library classes.\n\n"
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

    system_text = _AGENT_A_SYSTEM.format(requirements_file=requirements_file)
    if project_type.startswith("java"):
        system_text += _JVM_DEPENDENCY_HINT
    prompt_text = system_text + prompt_text

    run_result = runner.run(
        prompt_text,
        session_id=session_id,
        timeout=timeout,
        max_retries=max_retries,
        logger=logger,
        on_output=on_output,
    )
    store.write_agent_output(iteration, run_result.output)

    # For text-only runners (e.g. Copilot), apply output to working directory
    if not runner.supports_file_tools:
        logger.info(
            f"[Phase 1] Runner '{runner.provider_name}' is text-only — applying output to files"
        )
        apply_text_output(run_result.output, logger=logger)

    return run_result.session_id


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
    review_runner: AgentRunner | None = None,
    c_runner: AgentRunner | None = None,
    visualizer: Visualizer | None = None,
    workflow_config: WorkflowConfig | None = None,
    tag: str | None = None,
) -> bool:
    """Run the AI-native workflow pipeline (A → quality gates → verification).

    Verification mode comes from ``WorkflowConfig.verification`` (none / review /
    triangulation).

    All agent communication goes through RunStore:
    - Each run is isolated in a timestamped directory
    - Each iteration has its own subdirectory with all artifacts
    - Agent A receives structured context built from all previous iterations
    - Agent B/C paths are resolved from the store (no hardcoded globals)

    Args:
        store: RunStore instance. If None, creates one at .agent-native-workflow/.
        runner: AgentRunner for Agent A; verify/review/c runners for verification phases.
        workflow_config: WorkflowConfig — determines cli_provider and other settings.

    Returns:
        True if the pipeline converged (gates + configured verification passed).
    """
    wcfg = workflow_config or WorkflowConfig.resolve()

    # Build store
    if store is None:
        store = RunStore(base_dir=Path(".agent-native-workflow"))

    # Build runner (all agents use same provider)
    from agent_native_workflow.domain import AgentConfig, agent_config_for

    # Deferred warnings — logged after logger is initialised (line ~354)
    _deferred_warnings: list[str] = []

    agent_cfg = wcfg.agent_config or AgentConfig()
    is_blacklist = bool(agent_cfg.agent_a.denied_tools)

    if is_blacklist and wcfg.cli_provider in ("codex", "cursor"):
        _deferred_warnings.append(
            f"[Config] {wcfg.cli_provider} does not support "
            "deny flags — blacklist rules will not be "
            "enforced at CLI level. Audit still active."
        )

    def _model_for(perms_model: str, global_override: str) -> dict[str, object]:
        """Resolve model: CLI flag > config.yaml agents > provider default."""
        m = global_override or perms_model
        return {"model": m} if m else {}

    if runner is None:
        runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_a.allowed_tools,
            denied_tools=agent_cfg.agent_a.denied_tools,
            permission_mode=agent_cfg.agent_a.permission_mode,
            **_model_for(agent_cfg.agent_a.model, wcfg.model),
        )

    if verify_runner is None:
        # Agent B: triangulation senior dev
        verify_runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_b.allowed_tools,
            permission_mode=agent_cfg.agent_b.permission_mode,
            **_model_for(agent_cfg.agent_b.model, wcfg.model_verify or wcfg.model),
        )

    if review_runner is None:
        # Agent R: review-mode requirements + code reviewer
        review_runner = runner_for(
            wcfg.cli_provider,
            allowed_tools=agent_cfg.agent_r.allowed_tools,
            permission_mode=agent_cfg.agent_r.permission_mode,
            **_model_for(agent_cfg.agent_r.model, wcfg.model_verify or wcfg.model),
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
        "verification": wcfg.verification,
        "model_a": agent_cfg.agent_a.model or wcfg.model,
        "model_r": agent_cfg.agent_r.model or wcfg.model_verify or wcfg.model,
        "model_b": agent_cfg.agent_b.model or wcfg.model_verify or wcfg.model,
        "model_c": agent_cfg.agent_c.model or wcfg.model_verify or wcfg.model,
    }
    run_dir = store.start_run(config_snapshot=config_snapshot, tag=tag)
    store.set_agent_session_resume(runner.supports_resume)

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

    # Flush deferred warnings (collected before logger was ready)
    for _w in _deferred_warnings:
        logger.warn(_w)

    cfg = config or detect_all(base_branch=wcfg.base_branch)
    # Apply explicit command overrides from config file / env vars
    if wcfg.lint_cmd:
        logger.info(f"[config] lint-cmd override: {wcfg.lint_cmd}")
        cfg.lint_cmd = wcfg.lint_cmd
    if wcfg.test_cmd:
        logger.info(f"[config] test-cmd override: {wcfg.test_cmd}")
        cfg.test_cmd = wcfg.test_cmd
    metrics = PipelineMetrics(started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    use_parallel = (
        parallel_gates
        if parallel_gates is not None
        else os.environ.get("PARALLEL_GATES", "").lower() in ("true", "1", "yes")
    )

    # Per-agent timeouts: config.yaml `agents:` value takes precedence over global agent_timeout.
    timeout_a = agent_cfg.agent_a.timeout or agent_timeout
    timeout_r = agent_cfg.agent_r.timeout or agent_timeout

    os.environ.pop("CLAUDECODE", None)

    _shutdown_event.clear()  # reset for this pipeline run
    _signals_installed = False

    def _signal_handler(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.warn(f"Received {sig_name}, shutting down...")
        _shutdown_event.set()

    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        _signals_installed = True

    start_time = time.time()

    if runner.supports_resume:
        logger.info("[Session] Agent A CLI session resume enabled for this provider")

    logger.info(f"=== agent-native-workflow (provider: {runner.provider_name}) ===")
    logger.info(cfg.print_config())
    logger.info(f"Max iterations: {max_iterations}")
    logger.info(f"Verification: {wcfg.verification}")
    if is_blacklist:
        logger.info(f"Permission: blacklist ({len(agent_cfg.agent_a.denied_tools)} deny rules)")
    else:
        logger.info(f"Permission: whitelist ({len(agent_cfg.agent_a.allowed_tools)} allow rules)")
    if wcfg.advisory_iterations > 0:
        logger.info(f"Advisory iterations: {wcfg.advisory_iterations}")
    logger.info(f"Prompt: {prompt_file}")
    logger.info(f"Requirements: {requirements_file}")
    logger.info(f"Run dir: {run_dir}")
    logger.info(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    visualizer.on_pipeline_start(wcfg)

    converged = False
    agent_a_session: str | None = None
    agent_r_session: str | None = None
    consecutive_no_change = 0
    advisory_count = 0
    pipeline_exception: Exception | None = None

    try:
        for iteration in range(1, max_iterations + 1):
            if _shutdown_event.is_set():
                logger.warn("Shutdown requested, stopping pipeline")
                break

            iter_start = time.time()
            iter_metrics = IterationMetrics(iteration=iteration)

            logger.info(f"--- Iteration {iteration} / {max_iterations} ---")
            visualizer.on_iteration_start(iteration, max_iterations)

            # ── Phase 1: Implementation ──────────────────────────────────────
            logger.phase_start("phase1_implement", iteration=iteration)
            visualizer.on_phase_start(PipelinePhase.IMPLEMENT)

            # Snapshot working tree + commit hash before Agent A
            before_snapshot = snapshot_working_tree()
            before_head = _get_head_hash()

            new_session_id = _run_implementation_phase(
                iteration=iteration,
                prompt_file=prompt_file,
                requirements_file=agents_requirements_file,
                store=store,
                runner=runner,
                timeout=timeout_a,
                max_retries=max_retries,
                logger=logger,
                project_type=cfg.project_type,
                session_id=agent_a_session,
                on_output=visualizer.on_agent_stream,
            )
            if runner.supports_resume and new_session_id is not None:
                agent_a_session = new_session_id
            store.write_session_state({"agent_a": agent_a_session, "agent_r": agent_r_session})

            # Update changed_files to only what Agent A touched this iteration
            agent_changed = files_changed_since(before_snapshot)
            if agent_changed:
                consecutive_no_change = 0
                cfg.changed_files = agent_changed
                files_str = ", ".join(agent_changed[:10])
                logger.info(f"[Phase 1] Agent A changed {len(agent_changed)} file(s): {files_str}")
            else:
                consecutive_no_change += 1
                logger.warn(
                    f"[Phase 1] No file changes detected from Agent A "
                    f"(consecutive: {consecutive_no_change})"
                )

            iter_metrics.phase1_done = True

            # ── Post-execution audit (blacklist safety net) ──────────────
            if is_blacklist:
                _audit_post_phase1(before_head, before_snapshot, logger)

            # ── No-progress handling ─────────────────────────────────────────
            if consecutive_no_change >= 2:
                logger.warn("[Phase 1] Two consecutive no-change iterations — aborting pipeline")
                iter_metrics.outcome = IterationOutcome.NO_PROGRESS
                iter_metrics.duration_s = round(time.time() - iter_start, 2)
                metrics.iterations.append(iter_metrics)
                logger.phase_end("phase1_implement", "no_progress", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.IMPLEMENT, "fail")
                break

            if consecutive_no_change == 1:
                # Drop session resume so next iteration starts a fresh CLI session
                if runner.supports_resume and agent_a_session is not None:
                    logger.warn("[Phase 1] Dropping session resume — fresh session next iteration")
                    agent_a_session = None
                store.write_feedback(
                    iteration,
                    "You produced no file changes this iteration.\n"
                    "You MUST use the Edit or Write tool to modify actual files.\n"
                    "Describing changes in text has no effect — the pipeline checks git status.",
                    outcome=IterationOutcome.NO_PROGRESS,
                    gate_results=[],
                )
                iter_metrics.outcome = IterationOutcome.NO_PROGRESS
                iter_metrics.duration_s = round(time.time() - iter_start, 2)
                metrics.iterations.append(iter_metrics)
                logger.phase_end("phase1_implement", "no_progress", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.IMPLEMENT, "fail")
                continue

            logger.phase_end("phase1_implement", "completed", iteration=iteration)
            visualizer.on_phase_end(PipelinePhase.IMPLEMENT, "pass")

            if _shutdown_event.is_set():
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

            gate_pass, gate_output, gate_results = run_quality_gates(
                gates=gates,
                callable_gates=callable_gates,
                use_parallel=use_parallel,
                timeout=agent_timeout,
                logger=logger,
                on_output=visualizer.on_agent_stream,
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

            if _shutdown_event.is_set():
                break

            # ── Phase 3: Verification (strategy: none / review / triangulation) ─
            logger.phase_start("phase3_triangular_verify", iteration=iteration)
            visualizer.on_phase_start(PipelinePhase.TRIANGULAR_VERIFY)

            # Safety net: if no files changed but we somehow reached Phase 3
            # (e.g. Agent A only touched non-code files), reuse the previous verdict.
            if not agent_changed and iteration > 1:
                prev_feedback = store.read_feedback(iteration - 1)
                logger.info(
                    "[Phase 3] No code changes since last review — reusing previous FAIL verdict"
                )
                store.write_feedback(
                    iteration,
                    prev_feedback or "No changes made. Previous review verdict (FAIL) reused.",
                    outcome=IterationOutcome.VERIFY_FAIL,
                    gate_results=gate_results,
                )
                iter_metrics.verification_status = GateStatus.FAIL
                iter_metrics.outcome = IterationOutcome.VERIFY_FAIL
                iter_metrics.duration_s = round(time.time() - iter_start, 2)
                metrics.iterations.append(iter_metrics)
                logger.phase_end("phase3_triangular_verify", "skip_unchanged", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "fail")
                continue

            task_title = ""
            if prompt_file:
                task_title = load_prompt_title(prompt_file)

            strategy = build_verification_strategy(
                wcfg.verification,
                verify_runner=verify_runner,
                c_runner=c_runner,
                review_runner=review_runner,
                task_title=task_title,
            )
            verif_result = strategy.run(
                requirements_file=agents_requirements_file,
                store=store,
                iteration=iteration,
                config=cfg,
                timeout=timeout_r,
                max_retries=max_retries,
                logger=logger,
                verification_session_id=agent_r_session,
                on_output=visualizer.on_agent_stream,
            )
            if verif_result.next_agent_r_session_id is not None:
                agent_r_session = verif_result.next_agent_r_session_id
            store.write_session_state({"agent_a": agent_a_session, "agent_r": agent_r_session})

            if verif_result.passed and verif_result.advisory_only and wcfg.advisory_iterations > 0:
                advisory_count += 1
                if advisory_count >= wcfg.advisory_iterations:
                    logger.info(
                        f"[Phase 3] Advisory iteration limit reached ({advisory_count}/{wcfg.advisory_iterations}) "
                        "— accepting with remaining advisory items"
                    )
                    iter_metrics.verification_status = GateStatus.PASS
                    logger.phase_end("phase3_triangular_verify", "pass", iteration=iteration)
                    visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "pass")
                else:
                    logger.info(
                        f"[Phase 3] Advisory feedback ({advisory_count}/{wcfg.advisory_iterations}) "
                        "— sending advisory to Agent A"
                    )
                    feedback_content = verif_result.feedback or "Advisory improvements requested."
                    store.write_feedback(
                        iteration,
                        feedback_content,
                        outcome=IterationOutcome.VERIFY_FAIL,
                        gate_results=gate_results,
                    )
                    iter_metrics.verification_status = GateStatus.FAIL
                    iter_metrics.outcome = IterationOutcome.VERIFY_FAIL
                    iter_metrics.duration_s = round(time.time() - iter_start, 2)
                    metrics.iterations.append(iter_metrics)
                    logger.phase_end("phase3_triangular_verify", "advisory", iteration=iteration)
                    visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "fail")
                    continue
            elif verif_result.passed:
                iter_metrics.verification_status = GateStatus.PASS
                logger.phase_end("phase3_triangular_verify", "pass", iteration=iteration)
                visualizer.on_phase_end(PipelinePhase.TRIANGULAR_VERIFY, "pass")
            else:
                iter_metrics.verification_status = GateStatus.FAIL
                feedback_content = verif_result.feedback or (
                    "Verification failed but no report was produced."
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

            if _shutdown_event.is_set():
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

    except KeyboardInterrupt:
        logger.warn("Pipeline interrupted — cleaning up...")
    except Exception as e:
        # Capture exception to send error notification in finally block
        pipeline_exception = e
        raise
    finally:
        if _signals_installed:
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

        total_time = round(time.time() - start_time, 2)
        metrics.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        metrics.total_duration_s = total_time
        metrics.total_iterations = len(metrics.iterations)
        metrics.converged = converged
        store.write_metrics(metrics)

        visualizer.on_pipeline_end(metrics)

        # Send desktop notification if enabled
        if wcfg.notify:
            try:
                if pipeline_exception:
                    # Error occurred during pipeline execution
                    error_msg = str(pipeline_exception)[:200]
                    send_notification(
                        title="anw: error",
                        body=f"Pipeline error: {error_msg}",
                    )
                elif converged:
                    send_notification(
                        title="anw: converged",
                        body=f"Pipeline converged in {metrics.total_iterations} iteration(s), {total_time}s total",
                    )
                elif _shutdown_event.is_set():
                    send_notification(
                        title="anw: interrupted",
                        body=f"Pipeline interrupted after {metrics.total_iterations} iteration(s), {total_time}s",
                    )
                else:
                    send_notification(
                        title="anw: did not converge",
                        body=f"Max iterations ({metrics.total_iterations}) reached, {total_time}s total",
                    )
            except Exception:
                # Notification failure should never block cleanup
                pass

    if not converged:
        logger.info("")
        logger.info("=== MAX ITERATIONS REACHED ===")
        logger.info(f"Completed {max_iterations} iterations without full convergence.")
        logger.info(f"Total time: {total_time}s")

    return converged
