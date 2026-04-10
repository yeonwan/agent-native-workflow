"""Tests for verification strategies (REDESIGN Phase 1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_native_workflow.detect import ProjectConfig
from agent_native_workflow.domain import (
    CONSENSUS_AGREE_MARKER,
    REVIEW_RESULT_BLOCK_END,
    REVIEW_RESULT_BLOCK_START,
    REVIEW_VERDICT_FAIL,
    REVIEW_VERDICT_PASS,
    REVIEW_VERDICT_PASS_WITH_ADVISORY,
    TRIANGULAR_PASS_MARKER,
)
from agent_native_workflow.log import Logger
from agent_native_workflow.runners.base import RunResult
from agent_native_workflow.store import RunStore
from agent_native_workflow.strategies import (
    NoneStrategy,
    ReviewStrategy,
    TriangulationStrategy,
    build_verification_strategy,
)


def test_none_strategy_always_passes() -> None:
    strategy = NoneStrategy()
    result = strategy.run(
        requirements_file=Path("dummy.md"),
        store=MagicMock(),
        iteration=1,
        config=MagicMock(),
        timeout=300,
        max_retries=2,
        logger=MagicMock(),
    )
    assert result.passed is True
    assert result.feedback == ""


def _review_block(verdict: str, *, blocking_count: int, advisory_count: int) -> str:
    return (
        f"{REVIEW_RESULT_BLOCK_START}\n"
        f"verdict: {verdict}\n"
        f"blocking_count: {blocking_count}\n"
        f"advisory_count: {advisory_count}\n"
        f"{REVIEW_RESULT_BLOCK_END}"
    )


class _ApproveRunner:
    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        assert "src/foo.py" in prompt
        return RunResult(
            output=f"All good.\n{_review_block(REVIEW_VERDICT_PASS, blocking_count=0, advisory_count=0)}",
            session_id=None,
        )


class _RejectRunner:
    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        return RunResult(output="Missing error handling in cli.py.", session_id=None)


def test_review_strategy_passes_and_writes_review(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# Req\n\nDo the thing.\n")
    cfg = ProjectConfig(changed_files=["src/foo.py"])
    strategy = ReviewStrategy(_ApproveRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is True
    assert result.feedback == ""
    assert result.next_agent_r_session_id is None
    review = (store.run_dir / "iter-001" / "review.md").read_text()
    assert REVIEW_RESULT_BLOCK_START in review
    assert "verdict: pass" in review


class _ResumeReviewRunner:
    provider_name = "fake"
    supports_file_tools = True
    supports_resume = True

    def __init__(self) -> None:
        self.last_session_in: list[str | None] = []

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        self.last_session_in.append(session_id)
        return RunResult(
            output=f"ok\n{_review_block(REVIEW_VERDICT_PASS, blocking_count=0, advisory_count=0)}",
            session_id="review-sess-42",
        )


def test_review_strategy_resume_passes_session_and_returns_next_id(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    r = _ResumeReviewRunner()
    strategy = ReviewStrategy(r)

    out1 = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
        verification_session_id=None,
    )
    assert out1.next_agent_r_session_id == "review-sess-42"
    assert r.last_session_in == [None]

    out2 = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=2,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
        verification_session_id="review-sess-42",
    )
    assert out2.next_agent_r_session_id == "review-sess-42"
    assert r.last_session_in == [None, "review-sess-42"]


def test_review_strategy_fails_without_marker(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["a.py"])
    strategy = ReviewStrategy(_RejectRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is False
    assert "Missing error handling" in result.feedback


class _SequenceRunner:
    """Returns canned outputs in order for B → C → B confirm."""

    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self._i = 0

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        out = self._outputs[self._i]
        self._i += 1
        return RunResult(output=out, session_id=None)


def test_triangulation_strategy_full_pass(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _SequenceRunner(
        [
            "B review text",
            f"C report\n{TRIANGULAR_PASS_MARKER}\n",
            f"confirm\n{CONSENSUS_AGREE_MARKER}\n",
        ]
    )
    strategy = TriangulationStrategy(runner=runner, c_runner=None, task_title="Feature")
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is True
    assert result.feedback == ""
    assert (store.run_dir / "iter-001" / "b-review.md").read_text() == "B review text"
    assert TRIANGULAR_PASS_MARKER in (store.run_dir / "iter-001" / "c-report.md").read_text()


def test_triangulation_strategy_fails_when_pm_rejects(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _SequenceRunner(["B review", "PM says NOT MET, no pass marker here"])
    strategy = TriangulationStrategy(runner=runner)
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is False
    assert "NOT MET" in result.feedback


def test_triangulation_strategy_fails_when_dev_objects(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _SequenceRunner(
        [
            "B review",
            f"PM ok\n{TRIANGULAR_PASS_MARKER}",
            "I object — PM missed the race condition",
        ]
    )
    strategy = TriangulationStrategy(runner=runner)
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is False
    assert "object" in result.feedback.lower()


def test_build_verification_strategy_returns_expected_types() -> None:
    m = MagicMock()
    none_s = build_verification_strategy("none", verify_runner=m, c_runner=m)
    assert isinstance(none_s, NoneStrategy)
    assert isinstance(
        build_verification_strategy("review", verify_runner=m, c_runner=m), ReviewStrategy
    )
    assert isinstance(
        build_verification_strategy("triangulation", verify_runner=m, c_runner=m),
        TriangulationStrategy,
    )
    assert isinstance(
        build_verification_strategy("TRIANGULATION", verify_runner=m, c_runner=m),
        TriangulationStrategy,
    )


def test_build_verification_strategy_rejects_unknown_mode() -> None:
    m = MagicMock()
    with pytest.raises(ValueError, match="Unknown verification"):
        build_verification_strategy("nope", verify_runner=m, c_runner=m)


def test_build_review_prefers_review_runner() -> None:
    r_runner = MagicMock()
    b_runner = MagicMock()
    c_runner = MagicMock()
    strat = build_verification_strategy(
        "review",
        verify_runner=b_runner,
        c_runner=c_runner,
        review_runner=r_runner,
    )
    assert isinstance(strat, ReviewStrategy)
    assert strat._runner is r_runner


def test_build_review_falls_back_to_verify_runner() -> None:
    b_runner = MagicMock()
    c_runner = MagicMock()
    strat = build_verification_strategy(
        "review",
        verify_runner=b_runner,
        c_runner=c_runner,
        review_runner=None,
    )
    assert isinstance(strat, ReviewStrategy)
    assert strat._runner is b_runner


class _CaptureRunner:
    """Runner that records the prompt it receives."""

    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def __init__(self) -> None:
        self.last_prompt = ""

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        self.last_prompt = prompt
        return RunResult(
            output=f"ok\n{_review_block(REVIEW_VERDICT_PASS, blocking_count=0, advisory_count=0)}",
            session_id=None,
        )


def test_review_prompt_includes_consistency_check(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _CaptureRunner()
    strategy = ReviewStrategy(runner)
    strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=2,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert "iter-*/review.md" in runner.last_prompt


def test_review_prompt_includes_codereview_when_file_exists(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    (tmp_path / "codereview.md").write_text("## Conventions\n- Use pathlib\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _CaptureRunner()
    strategy = ReviewStrategy(runner)
    strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert "Code Quality Guidelines" in runner.last_prompt


def test_review_prompt_excludes_codereview_when_file_missing(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _CaptureRunner()
    strategy = ReviewStrategy(runner)
    strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert "Code Quality Guidelines" not in runner.last_prompt


def test_review_two_tier_verdict_format(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _CaptureRunner()
    strategy = ReviewStrategy(runner)
    strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert "Blocking Issues" in runner.last_prompt
    assert "Suggestions (Advisory)" in runner.last_prompt


class _AdvisoryRunner:
    """Returns a valid pass_with_advisory review block."""

    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        return RunResult(
            output=(
                "All requirements met.\n"
                "- Consider renaming foo to bar\n"
                f"{_review_block(REVIEW_VERDICT_PASS_WITH_ADVISORY, blocking_count=0, advisory_count=1)}"
            ),
            session_id=None,
        )


def test_review_strategy_advisory_returns_passed_with_advisory_flag(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    strategy = ReviewStrategy(_AdvisoryRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is True
    assert result.advisory_only is True
    assert "Consider renaming" in result.feedback


def test_review_strategy_clean_approve_has_no_advisory_flag(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["src/foo.py"])
    strategy = ReviewStrategy(_ApproveRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is True
    assert result.advisory_only is False
    assert result.feedback == ""


def test_review_prompt_includes_advisory_marker(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["x.py"])
    runner = _CaptureRunner()
    strategy = ReviewStrategy(runner)
    strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert REVIEW_RESULT_BLOCK_START in runner.last_prompt
    assert "verdict: pass | pass_with_advisory | fail" in runner.last_prompt
    assert REVIEW_RESULT_BLOCK_END in runner.last_prompt


class _FalsePositiveRunner:
    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        return RunResult(
            output=(
                "Blocking Issues remain.\n"
                "Do NOT output REVIEW_APPROVE when blocking issues exist.\n"
                "Agent A must fix validation.\n"
                f"{_review_block(REVIEW_VERDICT_FAIL, blocking_count=1, advisory_count=0)}"
            ),
            session_id=None,
        )


def test_review_strategy_does_not_false_positive_on_approve_text(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["src/foo.py"])
    strategy = ReviewStrategy(_FalsePositiveRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is False
    assert "Blocking Issues remain" in result.feedback


class _MalformedResultRunner:
    provider_name = "fake"
    supports_file_tools = True
    supports_resume = False

    def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        timeout: int = 300,
        max_retries: int = 2,
        logger=None,
        on_output=None,
    ) -> RunResult:
        return RunResult(
            output=(
                "Review text.\n"
                f"{REVIEW_RESULT_BLOCK_START}\n"
                f"verdict: {REVIEW_VERDICT_PASS}\n"
                "blocking_count: 1\n"
                "advisory_count: 0\n"
                f"{REVIEW_RESULT_BLOCK_END}"
            ),
            session_id=None,
        )


def test_review_strategy_fails_closed_on_invalid_result_block(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["src/foo.py"])
    strategy = ReviewStrategy(_MalformedResultRunner())
    result = strategy.run(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
    )
    assert result.passed is False
    assert "verdict: pass" in result.feedback


def test_run_triangular_verification_delegates_to_strategy(tmp_path: Path) -> None:
    from agent_native_workflow.verify import run_triangular_verification

    store = RunStore(base_dir=tmp_path)
    store.start_run({})
    reqs = tmp_path / "requirements.md"
    reqs.write_text("# R\n")
    cfg = ProjectConfig(changed_files=["z.py"])
    runner = _SequenceRunner(
        [
            "b",
            f"c\n{TRIANGULAR_PASS_MARKER}",
            f"d\n{CONSENSUS_AGREE_MARKER}",
        ]
    )
    ok, fb = run_triangular_verification(
        requirements_file=reqs,
        store=store,
        iteration=1,
        config=cfg,
        timeout=30,
        max_retries=1,
        logger=Logger(),
        runner=runner,
    )
    assert ok is True
    assert fb == ""
