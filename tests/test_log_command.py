"""Tests for 'anw log' command (commands/log.py).

Tests all flag combinations: --phase, --iter, --all-iters, --run,
conflict detection, and error cases for missing files/runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agent_native_workflow.commands.log import VALID_PHASES, cmd_log

# ── helpers ───────────────────────────────────────────────────────────────────


def _args(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "output_dir": None,
        "phase": None,
        "iter": None,
        "all_iters": False,
        "run": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_run(
    base_dir: Path,
    run_id: str,
    iters: dict[int, dict[str, str]] | None = None,
) -> Path:
    """Create a minimal run directory with iter subdirs and artifact files.

    iters: {iter_num: {phase_key: content}} e.g. {1: {"agent": "Agent A output"}}
    """
    from agent_native_workflow.commands.log import PHASE_TO_FILE

    run_dir = base_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": "2026-03-25T10:00:00",
        "config": {},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    for it_num, phases in (iters or {}).items():
        iter_dir = run_dir / f"iter-{it_num:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        for phase_key, content in phases.items():
            filename = PHASE_TO_FILE[phase_key]
            (iter_dir / filename).write_text(content)

    # Always update `latest` symlink so load_run_summary(run_id=None) works
    latest = base_dir / "latest"
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(run_dir.resolve())

    return run_dir


# ── basic output ──────────────────────────────────────────────────────────────


def test_log_default_shows_latest_iter_agent_output(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "Agent output iter 1"}, 2: {"agent": "Agent output iter 2"}})
    rc = cmd_log(_args(output_dir=str(base)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Agent output iter 2" in out
    assert "Agent output iter 1" not in out


def test_log_phase_review(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"review": "Review content here"}})
    rc = cmd_log(_args(output_dir=str(base), phase="review"))
    assert rc == 0
    assert "Review content here" in capsys.readouterr().out


def test_log_phase_feedback(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"feedback": "Feedback for agent"}})
    rc = cmd_log(_args(output_dir=str(base), phase="feedback"))
    assert rc == 0
    assert "Feedback for agent" in capsys.readouterr().out


def test_log_phase_gates(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    gates_data = json.dumps([{"name": "test", "status": "pass"}])
    _make_run(base, "run-001", {1: {"gates": gates_data}})
    rc = cmd_log(_args(output_dir=str(base), phase="gates"))
    assert rc == 0
    assert "pass" in capsys.readouterr().out


# ── --iter flag ───────────────────────────────────────────────────────────────


def test_log_iter_selects_specific_iteration(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(
        base,
        "run-001",
        {
            1: {"agent": "iter one output"},
            2: {"agent": "iter two output"},
            3: {"agent": "iter three output"},
        },
    )
    rc = cmd_log(_args(output_dir=str(base), iter=2))
    assert rc == 0
    out = capsys.readouterr().out
    assert "iter two output" in out
    assert "iter one output" not in out
    assert "iter three output" not in out


def test_log_iter_missing_returns_error(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "output"}})
    rc = cmd_log(_args(output_dir=str(base), iter=99))
    assert rc == 1
    assert capsys.readouterr().err != ""


# ── --all-iters flag ──────────────────────────────────────────────────────────


def test_log_all_iters_prints_each_iteration(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(
        base,
        "run-001",
        {
            1: {"agent": "iter1 output"},
            2: {"agent": "iter2 output"},
        },
    )
    rc = cmd_log(_args(output_dir=str(base), all_iters=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "iter1 output" in out
    assert "iter2 output" in out
    assert "Iteration 1" in out
    assert "Iteration 2" in out


def test_log_all_iters_skips_iters_without_file(tmp_path: Path, capsys) -> None:
    """--all-iters silently skips iterations that don't have the requested phase file."""
    base = tmp_path / ".anw"
    _make_run(
        base,
        "run-001",
        {
            1: {"agent": "iter1 output"},
            2: {},  # no agent file
            3: {"agent": "iter3 output"},
        },
    )
    rc = cmd_log(_args(output_dir=str(base), all_iters=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "iter1 output" in out
    assert "iter3 output" in out


# ── conflict detection ────────────────────────────────────────────────────────


def test_log_all_iters_and_iter_conflict(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "output"}})
    rc = cmd_log(_args(output_dir=str(base), all_iters=True, iter=1))
    assert rc == 1
    assert "--all-iters" in capsys.readouterr().err


# ── --run flag ────────────────────────────────────────────────────────────────


def test_log_run_selects_specific_run(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "run 1 output"}})
    _make_run(base, "run-002", {1: {"agent": "run 2 output"}})
    rc = cmd_log(_args(output_dir=str(base), run="run-001"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "run 1 output" in out
    assert "run 2 output" not in out


def test_log_run_not_found_returns_error(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "output"}})
    rc = cmd_log(_args(output_dir=str(base), run="run-does-not-exist"))
    assert rc == 1


# ── error cases ───────────────────────────────────────────────────────────────


def test_log_no_runs_returns_error(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    base.mkdir()
    rc = cmd_log(_args(output_dir=str(base)))
    assert rc == 1
    assert capsys.readouterr().err != ""


def test_log_invalid_phase_returns_error(tmp_path: Path, capsys) -> None:
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "output"}})
    rc = cmd_log(_args(output_dir=str(base), phase="not-a-real-phase"))
    assert rc == 1
    assert "not-a-real-phase" in capsys.readouterr().err


def test_log_missing_phase_file_returns_error(tmp_path: Path, capsys) -> None:
    """Phase file doesn't exist for the requested iteration — error, not silent skip."""
    base = tmp_path / ".anw"
    _make_run(base, "run-001", {1: {"agent": "output"}})  # no review file
    rc = cmd_log(_args(output_dir=str(base), phase="review"))
    assert rc == 1


# ── valid phases constant ─────────────────────────────────────────────────────


def test_valid_phases_contains_expected_keys() -> None:
    for expected in ("agent", "review", "feedback", "gates", "b-review", "c-report", "b-confirm"):
        assert expected in VALID_PHASES
