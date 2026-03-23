"""Tests for 'agn status' command — RunStore.load_run_summary / list_runs and _cmd_status."""

from __future__ import annotations

import json
from pathlib import Path

from agent_native_workflow.cli import _cmd_status, build_parser
from agent_native_workflow.store import RunStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    base_dir: Path,
    run_id: str,
    manifest: dict | None = None,
    metrics: dict | None = None,
    iterations: list[dict] | None = None,
) -> Path:
    """Create a fake run directory tree under base_dir/runs/<run_id>."""
    run_dir = base_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if manifest is None:
        manifest = {
            "run_id": run_id,
            "started_at": "2026-03-22T12:00:00",
            "config": {"cli_provider": "claude"},
        }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    if metrics is not None:
        (run_dir / "metrics.json").write_text(json.dumps(metrics))

    for it in iterations or []:
        iter_dir = run_dir / f"iter-{it['iteration']:03d}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        if "gate_results" in it:
            (iter_dir / "gates.json").write_text(json.dumps(it["gate_results"]))
        if "feedback" in it:
            (iter_dir / "feedback.md").write_text(it["feedback"])

    return run_dir


# ---------------------------------------------------------------------------
# RunStore.load_run_summary
# ---------------------------------------------------------------------------


class TestLoadRunSummary:
    def test_returns_none_when_no_latest(self, tmp_path):
        store = RunStore(base_dir=tmp_path)
        assert store.load_run_summary() is None

    def test_returns_none_for_unknown_run_id(self, tmp_path):
        store = RunStore(base_dir=tmp_path)
        assert store.load_run_summary(run_id="run-99999999-000000") is None

    def test_happy_path_with_latest_symlink(self, tmp_path):
        """load_run_summary() follows the 'latest' symlink and returns structured data."""
        run_id = "run-20260322-120000"
        run_dir = _make_run(
            tmp_path,
            run_id,
            metrics={
                "converged": True,
                "total_iterations": 2,
                "total_duration_s": 42.0,
                "iterations": [],
            },
            iterations=[
                {
                    "iteration": 1,
                    "gate_results": [
                        {"name": "lint", "status": "pass"},
                        {"name": "test", "status": "fail"},
                    ],
                    "feedback": "**Failed phase:** verify_fail\nSome feedback",
                },
                {
                    "iteration": 2,
                    "gate_results": [
                        {"name": "lint", "status": "pass"},
                        {"name": "test", "status": "pass"},
                    ],
                },
            ],
        )
        # Create 'latest' symlink
        latest = tmp_path / "latest"
        latest.symlink_to(run_dir.resolve())

        store = RunStore(base_dir=tmp_path)
        summary = store.load_run_summary()

        assert summary is not None
        assert summary["run_id"] == run_id
        assert summary["manifest"]["config"]["cli_provider"] == "claude"
        assert summary["metrics"]["converged"] is True
        assert len(summary["iterations"]) == 2
        # verify outcome is inferred from feedback
        assert summary["iterations"][0]["outcome"] == "verify_fail"
        assert summary["iterations"][1]["outcome"] == ""

    def test_summary_by_explicit_run_id(self, tmp_path):
        run_id = "run-20260322-130000"
        _make_run(tmp_path, run_id)
        store = RunStore(base_dir=tmp_path)
        summary = store.load_run_summary(run_id=run_id)
        assert summary is not None
        assert summary["run_id"] == run_id

    def test_metrics_absent_marks_incomplete(self, tmp_path):
        """When metrics.json does not exist the summary still returns with metrics=None."""
        run_id = "run-20260322-140000"
        run_dir = _make_run(tmp_path, run_id)
        latest = tmp_path / "latest"
        latest.symlink_to(run_dir.resolve())

        store = RunStore(base_dir=tmp_path)
        summary = store.load_run_summary()
        assert summary is not None
        assert summary["metrics"] is None


# ---------------------------------------------------------------------------
# RunStore.list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_empty_when_no_runs_dir(self, tmp_path):
        store = RunStore(base_dir=tmp_path)
        assert store.list_runs() == []

    def test_returns_runs_newest_first(self, tmp_path):
        _make_run(tmp_path, "run-20260322-100000")
        _make_run(tmp_path, "run-20260322-110000")
        _make_run(tmp_path, "run-20260322-120000")

        store = RunStore(base_dir=tmp_path)
        runs = store.list_runs()
        assert len(runs) == 3
        assert runs[0]["run_id"] == "run-20260322-120000"
        assert runs[-1]["run_id"] == "run-20260322-100000"

    def test_converged_yes_when_metrics_converged(self, tmp_path):
        _make_run(
            tmp_path,
            "run-20260322-120000",
            metrics={"converged": True, "total_iterations": 1},
        )
        store = RunStore(base_dir=tmp_path)
        runs = store.list_runs()
        assert runs[0]["converged"] == "yes"
        assert runs[0]["total_iterations"] == 1

    def test_converged_incomplete_when_no_metrics(self, tmp_path):
        _make_run(tmp_path, "run-20260322-120000")
        store = RunStore(base_dir=tmp_path)
        runs = store.list_runs()
        assert runs[0]["converged"] == "incomplete"


# ---------------------------------------------------------------------------
# _cmd_status via argparse
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def _run(self, tmp_path: Path, extra_args: list[str], capsys) -> int:
        """Parse args and call _cmd_status, return exit code."""
        parser = build_parser()
        args = parser.parse_args(["status", "--output-dir", str(tmp_path)] + extra_args)
        return _cmd_status(args)

    def test_no_runs_prints_message_and_exits_1(self, tmp_path, capsys):
        rc = self._run(tmp_path, [], capsys)
        assert rc == 1
        captured = capsys.readouterr()
        assert "No runs found" in captured.err

    def test_unknown_run_id_exits_1(self, tmp_path, capsys):
        rc = self._run(tmp_path, ["--run", "run-99999999-000000"], capsys)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_happy_path_prints_run_id(self, tmp_path, capsys):
        run_id = "run-20260322-120000"
        run_dir = _make_run(
            tmp_path,
            run_id,
            metrics={
                "converged": True,
                "total_iterations": 1,
                "total_duration_s": 5.0,
                "iterations": [],
            },
        )
        latest = tmp_path / "latest"
        latest.symlink_to(run_dir.resolve())

        rc = self._run(tmp_path, [], capsys)
        assert rc == 0
        captured = capsys.readouterr()
        assert run_id in captured.out
        assert "yes" in captured.out  # converged

    def test_explicit_run_id_prints_correct_run(self, tmp_path, capsys):
        run_id = "run-20260322-130000"
        _make_run(tmp_path, run_id)

        rc = self._run(tmp_path, ["--run", run_id], capsys)
        assert rc == 0
        captured = capsys.readouterr()
        assert run_id in captured.out

    def test_list_flag_empty_prints_no_runs(self, tmp_path, capsys):
        rc = self._run(tmp_path, ["--list"], capsys)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No runs found" in captured.out

    def test_list_flag_shows_all_runs(self, tmp_path, capsys):
        _make_run(tmp_path, "run-20260322-100000")
        _make_run(tmp_path, "run-20260322-110000")

        rc = self._run(tmp_path, ["--list"], capsys)
        assert rc == 0
        captured = capsys.readouterr()
        assert "run-20260322-100000" in captured.out
        assert "run-20260322-110000" in captured.out

    def test_interrupted_run_shows_incomplete(self, tmp_path, capsys):
        """When metrics.json is absent the output should note the run is incomplete."""
        run_id = "run-20260322-150000"
        run_dir = _make_run(tmp_path, run_id)  # no metrics
        latest = tmp_path / "latest"
        latest.symlink_to(run_dir.resolve())

        rc = self._run(tmp_path, [], capsys)
        assert rc == 0
        captured = capsys.readouterr()
        assert "incomplete" in captured.out


# ---------------------------------------------------------------------------
# build_parser — status subcommand registration
# ---------------------------------------------------------------------------


class TestBuildParserStatus:
    def test_status_subcommand_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
        assert args.run is None
        assert args.list is False

    def test_status_with_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(["status", "--run", "run-20260322-120000"])
        assert args.run == "run-20260322-120000"

    def test_status_with_list_flag(self):
        parser = build_parser()
        args = parser.parse_args(["status", "--list"])
        assert args.list is True
