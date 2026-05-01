"""Tests for snapshot_working_tree and files_changed_since in detect.py.

These functions are the core change-detection mechanism used by the pipeline
to decide whether Agent A made progress in each iteration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from agent_native_workflow.detect import files_changed_since, snapshot_working_tree

# ── helpers ───────────────────────────────────────────────────────────────────


def _git_status(lines: list[str]):
    """Patch subprocess.run to return the given git-status --porcelain lines."""

    class _Result:
        stdout = "\n".join(lines)
        returncode = 0

    def _run(*args: object, **kwargs: object) -> _Result:
        return _Result()

    return _run


# ── snapshot_working_tree ─────────────────────────────────────────────────────


def test_snapshot_empty_when_clean(tmp_path: Path) -> None:
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([])):
        snap = snapshot_working_tree(tmp_path)
    assert snap == {}


def test_snapshot_includes_modified_file(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("content")
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M foo.py"])):
        snap = snapshot_working_tree(tmp_path)
    assert "foo.py" in snap
    assert isinstance(snap["foo.py"], str)


def test_snapshot_hash_differs_after_content_change(tmp_path: Path) -> None:
    f = tmp_path / "store.py"
    f.write_text("v1")
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        snap1 = snapshot_working_tree(tmp_path)

    f.write_text("v2 — agent made further changes")
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        snap2 = snapshot_working_tree(tmp_path)

    assert snap1["store.py"] != snap2["store.py"]


def test_snapshot_returns_empty_dict_on_git_failure(tmp_path: Path) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise subprocess.SubprocessError

    with patch("agent_native_workflow.detect.subprocess.run", _raise):
        snap = snapshot_working_tree(tmp_path)
    assert snap == {}


def test_snapshot_returns_empty_dict_when_git_not_found(tmp_path: Path) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    with patch("agent_native_workflow.detect.subprocess.run", _raise):
        snap = snapshot_working_tree(tmp_path)
    assert snap == {}


def test_snapshot_handles_untracked_file(tmp_path: Path) -> None:
    (tmp_path / "new.py").write_text("brand new")
    with patch("agent_native_workflow.detect.subprocess.run", _git_status(["?? new.py"])):
        snap = snapshot_working_tree(tmp_path)
    assert "new.py" in snap


def test_snapshot_handles_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status([" M a.py", " M b.py"]),
    ):
        snap = snapshot_working_tree(tmp_path)
    assert len(snap) == 2
    assert "a.py" in snap
    assert "b.py" in snap


# ── files_changed_since ───────────────────────────────────────────────────────


def test_files_changed_since_detects_new_file(tmp_path: Path) -> None:
    """A file that wasn't in the before-snapshot is reported as changed."""
    (tmp_path / "new.py").write_text("brand new")
    before: dict[str, str] = {}  # nothing was modified before Agent A ran
    with patch("agent_native_workflow.detect.subprocess.run", _git_status(["?? new.py"])):
        changed = files_changed_since(before, tmp_path)
    assert "new.py" in changed


def test_files_changed_since_detects_content_change_on_already_modified_file(
    tmp_path: Path,
) -> None:
    """Core regression: file already in 'M' state is detected when content changes.

    This was the iter-002 no_progress bug: the file was already modified by
    iter-001 so the git status line was identical before and after iter-002's
    Agent A run. The hash comparison now catches this.
    """
    f = tmp_path / "store.py"
    f.write_text("iter-001 changes")

    # Snapshot BEFORE iter-002's Agent A run (file already M from iter-001)
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        before = snapshot_working_tree(tmp_path)

    # Agent A makes additional changes
    f.write_text("iter-001 changes + iter-002 changes")

    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        changed = files_changed_since(before, tmp_path)

    assert "store.py" in changed


def test_files_changed_since_ignores_file_with_same_content(tmp_path: Path) -> None:
    """A file that is still modified but whose content didn't change is NOT reported."""
    f = tmp_path / "store.py"
    f.write_text("unchanged content")

    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        before = snapshot_working_tree(tmp_path)

    # Agent A ran but did not actually touch this file
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([" M store.py"])):
        changed = files_changed_since(before, tmp_path)

    assert changed == []


def test_files_changed_since_empty_when_nothing_changed(tmp_path: Path) -> None:
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([])):
        before = snapshot_working_tree(tmp_path)
    with patch("agent_native_workflow.detect.subprocess.run", _git_status([])):
        changed = files_changed_since(before, tmp_path)
    assert changed == []


def test_files_changed_since_handles_rename(tmp_path: Path) -> None:
    """Porcelain rename format 'R  old -> new' — path is the new name."""
    (tmp_path / "new_name.py").write_text("content")
    before: dict[str, str] = {}
    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status(["R  old_name.py -> new_name.py"]),
    ):
        changed = files_changed_since(before, tmp_path)
    assert "new_name.py" in changed


def test_files_changed_since_returns_multiple_changed_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("before")
    (tmp_path / "b.py").write_text("before")

    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status([" M a.py", " M b.py"]),
    ):
        before = snapshot_working_tree(tmp_path)

    (tmp_path / "a.py").write_text("after")
    (tmp_path / "b.py").write_text("after")

    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status([" M a.py", " M b.py"]),
    ):
        changed = files_changed_since(before, tmp_path)

    assert "a.py" in changed
    assert "b.py" in changed


def test_files_changed_since_only_reports_actually_changed_file(tmp_path: Path) -> None:
    """Only the file that changed content is reported, not the untouched one."""
    (tmp_path / "a.py").write_text("same")
    (tmp_path / "b.py").write_text("before")

    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status([" M a.py", " M b.py"]),
    ):
        before = snapshot_working_tree(tmp_path)

    # Only b.py gets modified
    (tmp_path / "b.py").write_text("after")

    with patch(
        "agent_native_workflow.detect.subprocess.run",
        _git_status([" M a.py", " M b.py"]),
    ):
        changed = files_changed_since(before, tmp_path)

    assert "b.py" in changed
    assert "a.py" not in changed
