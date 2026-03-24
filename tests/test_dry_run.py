"""Tests for 'agn run --dry-run' command — prints prompt and exits without running pipeline."""

from __future__ import annotations

from pathlib import Path

from agent_native_workflow.cli import _cmd_run, build_parser


class TestBuildParserDryRun:
    """Test parser registration of --dry-run flag."""

    def test_dry_run_flag_parsed(self):
        """--dry-run flag is recognized by parser."""
        parser = build_parser()
        args = parser.parse_args(["run", "--dry-run"])
        assert args.command == "run"
        assert args.dry_run is True

    def test_dry_run_default_false(self):
        """--dry-run defaults to False when not specified."""
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.dry_run is False

    def test_dry_run_with_other_flags_ignored(self):
        """--dry-run can be combined with other flags (they are ignored)."""
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--dry-run", "--cli", "claude", "--model", "claude-3-5-sonnet"]
        )
        assert args.dry_run is True
        assert args.cli == "claude"
        assert args.model == "claude-3-5-sonnet"


class TestCmdRunDryRun:
    """Test _cmd_run behavior with --dry-run flag."""

    def _make_args(self, tmp_path: Path, extra_args: list[str] | None = None) -> object:
        """Helper to parse args with standard defaults."""
        parser = build_parser()
        default_args = [
            "run",
            "--output-dir",
            str(tmp_path),
        ]
        if extra_args:
            default_args.extend(extra_args)
        return parser.parse_args(default_args)

    def test_dry_run_prints_header_and_footer(self, tmp_path, capsys):
        """Dry-run prints header, prompt, and footer."""
        # Create minimal requirements file
        req_file = tmp_path / "requirements.md"
        req_file.write_text("# Test Requirements\n\nTest requirement")

        args = self._make_args(tmp_path, ["--dry-run", "--requirements", str(req_file)])
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "=== Agent A Prompt (dry-run) ===" in captured.out
        assert "=== End of Prompt ===" in captured.out

    def test_dry_run_prints_requirements_when_no_prompt(self, tmp_path, capsys):
        """When no PROMPT file, dry-run prints requirements file content."""
        req_file = tmp_path / "requirements.md"
        req_content = "# Test Requirements\n\nThis is a test requirement.\n"
        req_file.write_text(req_content)

        args = self._make_args(
            tmp_path,
            [
                "--dry-run",
                "--prompt",
                str(tmp_path / "nonexistent.yaml"),
                "--requirements",
                str(req_file),
            ],
        )
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        # Should contain the requirements content
        assert "This is a test requirement" in captured.out

    def test_dry_run_prints_prompt_yaml_when_available(self, tmp_path, capsys):
        """When PROMPT.yaml exists, dry-run prints rendered prompt."""
        prompt_file = tmp_path / "PROMPT.yaml"
        prompt_file.write_text(
            """\
title: Test Task
build: |
  Do something.
criteria:
  - Criterion 1
  - Criterion 2
"""
        )
        req_file = tmp_path / "requirements.md"
        req_file.write_text("# Requirements")

        args = self._make_args(
            tmp_path,
            [
                "--dry-run",
                "--prompt",
                str(prompt_file),
                "--requirements",
                str(req_file),
            ],
        )
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        # Should contain content from the prompt
        assert "Test Task" in captured.out

    def test_dry_run_missing_requirements_file_exits_1(self, tmp_path, capsys):
        """When requirements file doesn't exist, dry-run exits with code 1."""
        req_file = tmp_path / "nonexistent.md"

        args = self._make_args(tmp_path, ["--dry-run", "--requirements", str(req_file)])
        rc = _cmd_run(args)

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR: Requirements file not found" in captured.err

    def test_dry_run_uses_explicit_requirements_path(self, tmp_path, capsys):
        """Dry-run uses explicit requirements path when provided."""
        # Create requirements.md with unique content
        req_file = tmp_path / "my_requirements.md"
        req_content = "# My Unique Requirements\n"
        req_file.write_text(req_content)

        args = self._make_args(
            tmp_path,
            [
                "--dry-run",
                "--prompt",
                str(tmp_path / "nonexistent.yaml"),
                "--requirements",
                str(req_file),
            ],
        )
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "=== Agent A Prompt (dry-run) ===" in captured.out
        assert "My Unique Requirements" in captured.out

    def test_dry_run_ignores_other_flags(self, tmp_path, capsys):
        """Dry-run ignores --cli, --model, --max-iterations, etc."""
        req_file = tmp_path / "requirements.md"
        req_file.write_text("# Requirements")

        # Pass many flags — dry-run should ignore all of them
        args = self._make_args(
            tmp_path,
            [
                "--dry-run",
                "--requirements",
                str(req_file),
                "--cli",
                "copilot",
                "--model",
                "gpt-4",
                "--max-iterations",
                "10",
                "--timeout",
                "500",
            ],
        )
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        # Should succeed without error (no runner created)
        assert "=== Agent A Prompt (dry-run) ===" in captured.out

    def test_dry_run_with_missing_prompt_but_valid_requirements(self, tmp_path, capsys):
        """When prompt file is missing but requirements exists, uses requirements."""
        req_file = tmp_path / "requirements.md"
        req_file.write_text("# Valid Requirements\n")

        args = self._make_args(
            tmp_path,
            [
                "--dry-run",
                "--prompt",
                str(tmp_path / "missing.yaml"),
                "--requirements",
                str(req_file),
            ],
        )
        rc = _cmd_run(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "=== Agent A Prompt (dry-run) ===" in captured.out
        assert "Valid Requirements" in captured.out
