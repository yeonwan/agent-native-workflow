"""Quality gate execution: subprocess, parallel/sequential orchestration, output digest."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent_native_workflow.domain import GateResult, GateStatus
from agent_native_workflow.gates.digesters.factory import build_digester
from agent_native_workflow.log import Logger

# Stored per gate in gates.json (avoid multi‑MB logs)
GATE_STORE_OUTPUT_MAX = 200_000

_UNSAFE_PATTERN = re.compile(r"\$\(|`|;\s*rm\s|&&\s*rm\s|>\s*/dev/")


def _is_safe_command(cmd: str) -> bool:
    return not _UNSAFE_PATTERN.search(cmd)


def _for_storage(raw: str) -> str:
    if len(raw) <= GATE_STORE_OUTPUT_MAX:
        return raw
    return raw[:GATE_STORE_OUTPUT_MAX] + "\n...[truncated for storage]"


def _failure_feedback(name: str, cmd: str, raw_output: str) -> str:
    digester = build_digester(name, cmd)
    body = digester.digest(raw_output, 1)
    if cmd:
        return f"{name} ({cmd}) FAILED:\n{body}"
    return f"{name} FAILED:\n{body}"


def run_gate_command(cmd: str, timeout: int = 300) -> tuple[bool, str]:
    if not _is_safe_command(cmd):
        return False, f"BLOCKED: command contains unsafe patterns: {cmd}"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {cmd}"
    except Exception as e:
        return False, f"Command failed: {e}"


def _emit(on_output: Callable[[str], None] | None, text: str) -> None:
    """Send each line of text to the on_output callback."""
    if on_output is None:
        return
    for line in text.splitlines():
        on_output(line)


def run_gates_sequential(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    timeout: int,
    logger: Logger,
    on_output: Callable[[str], None] | None = None,
) -> tuple[bool, str, list[GateResult]]:
    results: list[GateResult] = []
    gate_pass = True
    gate_output = ""

    for name, cmd in gates:
        if not gate_pass:
            break
        logger.info(f"[Phase 2] Running {name}: {cmd}")
        on_output and on_output(f"─── gate: {name} ───")
        passed, output = run_gate_command(cmd, timeout)
        _emit(on_output, output)
        status = GateStatus.PASS if passed else GateStatus.FAIL
        results.append(GateResult(name=name, status=status, output=_for_storage(output)))
        if passed:
            logger.info(f"[Phase 2] {name}: PASS")
        else:
            logger.info(f"[Phase 2] {name}: FAIL")
            gate_output = _failure_feedback(name, cmd, output)
            gate_pass = False

    for cname, cfunc in callable_gates:
        if not gate_pass:
            break
        logger.info(f"[Phase 2] Running callable:{cname}")
        on_output and on_output(f"─── gate: {cname} ───")
        try:
            passed, output = cfunc()
        except Exception as e:
            passed, output = False, f"Gate '{cname}' raised: {e}"
        _emit(on_output, output)
        status = GateStatus.PASS if passed else GateStatus.FAIL
        results.append(
            GateResult(
                name=f"callable:{cname}",
                status=status,
                output=_for_storage(output),
            )
        )
        if passed:
            logger.info(f"[Phase 2] callable:{cname}: PASS")
        else:
            logger.info(f"[Phase 2] callable:{cname}: FAIL")
            gate_output = _failure_feedback(f"callable:{cname}", "", output)
            gate_pass = False

    return gate_pass, gate_output, results


def run_gates_parallel(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    timeout: int,
    logger: Logger,
    on_output: Callable[[str], None] | None = None,
) -> tuple[bool, str, list[GateResult]]:
    total = len(gates) + len(callable_gates)
    logger.info(f"[Phase 2] Running {total} gates in parallel")
    results: list[GateResult] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max(total, 1)) as executor:
        future_map: dict[object, tuple[str, str]] = {}
        for name, cmd in gates:
            future_map[executor.submit(run_gate_command, cmd, timeout)] = (name, cmd)

        for cname, cfunc in callable_gates:

            def _wrapped(fn: Callable[[], tuple[bool, str]], n: str) -> tuple[bool, str]:
                try:
                    return fn()
                except Exception as e:
                    return False, f"Gate '{n}' raised: {e}"

            future_map[executor.submit(_wrapped, cfunc, cname)] = (f"callable:{cname}", "")

        for future in as_completed(future_map):
            name, cmd = future_map[future]
            passed, output = future.result()
            on_output and on_output(f"─── gate: {name} ───")
            _emit(on_output, output)
            status = GateStatus.PASS if passed else GateStatus.FAIL
            results.append(GateResult(name=name, status=status, output=_for_storage(output)))
            if passed:
                logger.info(f"[Phase 2] {name}: PASS")
            else:
                logger.info(f"[Phase 2] {name}: FAIL")
                failures.append(_failure_feedback(name, cmd, output))

    if failures:
        return False, "\n\n".join(failures), results
    return True, "", results


def run_quality_gates(
    *,
    gates: list[tuple[str, str]],
    callable_gates: list[tuple[str, Callable[[], tuple[bool, str]]]],
    use_parallel: bool,
    timeout: int,
    logger: Logger,
    on_output: Callable[[str], None] | None = None,
) -> tuple[bool, str, list[GateResult]]:
    total_gates = len(gates) + len(callable_gates)
    if not total_gates:
        logger.info("[Phase 2] No quality gates configured — skipping")
        return True, "", []
    if use_parallel:
        return run_gates_parallel(
            gates=gates, callable_gates=callable_gates, timeout=timeout, logger=logger,
            on_output=on_output,
        )
    return run_gates_sequential(
        gates=gates, callable_gates=callable_gates, timeout=timeout, logger=logger,
        on_output=on_output,
    )
