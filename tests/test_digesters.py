"""Gate output digesters (ENHANCE Phase C)."""

from __future__ import annotations

import json

from agent_native_workflow.gates.digesters.factory import build_digester
from agent_native_workflow.gates.digesters.generic import GenericDigester
from agent_native_workflow.gates.digesters.jest_digester import JestDigester
from agent_native_workflow.gates.digesters.pytest_digester import PytestDigester


def test_generic_pass_returns_empty() -> None:
    assert GenericDigester().digest("anything", 0) == ""


def test_generic_small_failure_unchanged() -> None:
    msg = "Error: one line\n"
    assert GenericDigester(max_chars=5000).digest(msg, 1) == msg


def test_pytest_digester_extracts_summary_section() -> None:
    raw = """
foo
=========================== short test summary info ============================
FAILED tests/test_x.py::test_a - AssertionError: 1 != 2
========================= 1 failed, 2 passed in 1.2s ==========================
"""
    out = PytestDigester().digest(raw, 1)
    assert "FAILED tests/test_x.py::test_a" in out
    assert "1 failed" in out.lower() or "failed" in out.lower()


def test_build_digester_pytest_command() -> None:
    d = build_digester("test", "uv run pytest tests/")
    assert isinstance(d, PytestDigester)


def test_build_digester_jest_command() -> None:
    assert isinstance(build_digester("test", "npx jest"), JestDigester)
    assert isinstance(build_digester("test", "npx vitest run"), JestDigester)


def test_build_digester_unknown_uses_generic() -> None:
    assert isinstance(build_digester("lint", "ruff check ."), GenericDigester)


def test_jest_digester_json() -> None:
    payload = {
        "testResults": [
            {
                "assertionResults": [
                    {
                        "status": "failed",
                        "fullName": "suite a",
                        "failureMessages": ["expected 1 got 2"],
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload)
    out = JestDigester().digest(raw, 1)
    assert "suite a" in out
    assert "expected 1" in out
