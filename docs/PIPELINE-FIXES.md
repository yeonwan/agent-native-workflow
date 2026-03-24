# Pipeline Robustness Fixes

> Fixing real problems observed in a live run (`run-20260323-225916`).
> See `docs/pipeline-analysis.md` for the full diagnosis.

---

## Context

The pipeline ran `agn clean` with Haiku for all agents (A, R). It "converged" in 3 iterations, but:

- **Agent A made zero file changes in iterations 2 and 3** (hallucinated edits due to `--resume`)
- **Agent R approved unchanged code on iteration 3** after rejecting the same code twice (non-deterministic)
- **Lint gate was skipped** because `ruff` was not found in the subprocess PATH

These six fixes are ordered by priority. Implement them in order.

---

## Fix 1 — Adaptive Session Resume (no-change → fresh session)

**Problem:** `--resume` gives Agent A session memory, which causes it to *describe* fixes instead of calling Edit/Write tools. Result: 0 file changes in iterations 2+.

**Solution:** Keep resume by default, but switch to a fresh session when no file changes are detected. Also inject an explicit tool-use instruction into the resume prompt.

### 1a. Add tool-use enforcement to resume prompt

**File:** `src/agent_native_workflow/store.py`, method `_build_resume_agent_a_context`

In the prompt string returned by this method, add this block **before** the iteration history section:

```
> **CRITICAL: You MUST use the Edit or Write tool to make actual file changes.**
> Describing changes in text has NO effect — the pipeline checks `git status`.
> If you do not call a file-editing tool, the pipeline will detect zero changes.
```

### 1b. Drop resume on no-change detection

**File:** `src/agent_native_workflow/pipeline.py`, inside the `for iteration in range(...)` loop.

After detecting no file changes (the `else` branch at ~line 313-314 where it logs `"No file changes detected from Agent A"`), add logic:

```python
# Track consecutive no-change count
# (declare `consecutive_no_change = 0` before the for loop, next to `agent_a_session`)

if agent_changed:
    consecutive_no_change = 0
    # ... existing code ...
else:
    consecutive_no_change += 1
    logger.info("[Phase 1] No file changes detected from Agent A")

    if consecutive_no_change == 1 and runner.supports_resume:
        logger.warn("[Phase 1] Dropping session resume — will use fresh session next iteration")
        agent_a_session = None
    elif consecutive_no_change >= 2:
        logger.warn("[Phase 1] Two consecutive no-change iterations — aborting pipeline")
        iter_metrics.outcome = IterationOutcome.GATE_FAIL
        iter_metrics.duration_s = round(time.time() - iter_start, 2)
        metrics.iterations.append(iter_metrics)
        break
```

This means:
- 1st no-change: reset `agent_a_session = None` so next iteration starts a fresh CLI session
- 2nd consecutive no-change: break the loop immediately (don't waste tokens on gates/review)

### 1c. Skip gates and review when no files changed

Still in `pipeline.py`, after the no-change detection block above: when `consecutive_no_change >= 1`, skip Phase 2 and Phase 3 entirely and `continue` to the next iteration. The pipeline should only run gates/review when Agent A actually changed something.

```python
if consecutive_no_change >= 1 and consecutive_no_change < 2:
    # Write feedback telling Agent A it made no changes
    store.write_feedback(
        iteration,
        "You produced no file changes. You MUST use Edit/Write tools to modify files.",
        outcome=IterationOutcome.GATE_FAIL,
        gate_results=[],
    )
    iter_metrics.outcome = IterationOutcome.GATE_FAIL
    iter_metrics.duration_s = round(time.time() - iter_start, 2)
    metrics.iterations.append(iter_metrics)
    logger.phase_end("phase1_implement", "no_change", iteration=iteration)
    continue
```

---

## Fix 2 — Skip Agent R When Code Is Unchanged

**Problem:** Agent R reviews identical code each iteration and gives random verdicts. This causes false convergence.

**Solution:** If no files changed since the last review, reuse the previous Agent R verdict instead of re-running.

**File:** `src/agent_native_workflow/pipeline.py`, in the Phase 3 section (~line 368+).

Before calling `strategy.run(...)`, check if there were file changes:

```python
# Before strategy.run(), check if code actually changed
if not agent_changed and iteration > 1:
    logger.info("[Phase 3] No code changes since last review — reusing previous verdict (FAIL)")
    prev_review = store.read_feedback(iteration - 1)
    store.write_feedback(
        iteration,
        prev_review or "No changes made. Previous review verdict (FAIL) reused.",
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
```

**Important:** This block goes **before** the existing `strategy = build_verification_strategy(...)` call. If `agent_changed` is empty and `iteration > 1`, we skip the review entirely.

Note: Fix 1 already handles no-change by skipping gates+review and breaking on 2nd consecutive. Fix 2 is a safety net for any edge case where we reach Phase 3 with unchanged code (e.g., gates passed but Agent A only changed non-code files that gates don't check).

---

## Fix 3 — Tell Agent R to Read Its Own Previous Reviews

**Problem:** Agent R gives inconsistent verdicts on identical code across iterations.

**Root cause:** Agent R already has session memory via `--resume` and file-read tools to access previous `review.md` files — but the current prompt says "review this code" with no reference to prior verdicts. LLMs follow the current prompt instruction more strongly than conversation history, so session memory alone doesn't prevent flip-flopping.

**Solution:** Add one instruction to the prompt telling Agent R to read its previous reviews for consistency. No Python-level content injection needed — Agent R reads the files itself.

**File:** `src/agent_native_workflow/strategies/review.py`

At the end of the `prompt` string (before the `## Verdict` section), add:

```python
prompt = f"""You are a code reviewer checking whether an implementation meets \
its requirements.

## Requirements
Read `{requirements_file}` — this is the source of truth.

## Changed Files
The following files were changed in this implementation:
{changed_section}

Read each changed file and verify the implementation against requirements.

## Consistency Check
If this is not the first review in this run, previous reviews are saved at:
`{store.run_dir}/iter-*/review.md`
Read your previous review(s) before deciding. Your verdict must be consistent
with prior reviews unless the code has actually changed since then.

## Your Review
...rest of prompt unchanged...
```

This leverages the existing `review.md` artifacts and Agent R's file-read tools. No prompt bloat from injecting full review content — Agent R reads what it needs directly.

---

## Fix 4 — Ensure Lint Commands Use `uv run` Prefix

**Problem:** `detect_all()` finds `ruff` at the system level, but inside the pipeline subprocess `ruff` is not in PATH. Result: lint gate silently skipped. `agn init` already generates uncommented `lint-cmd` / `test-cmd` when detected, but the detected commands may lack the `uv run` prefix needed for subprocess execution.

**Solution:** Ensure the detected commands in `detect.py` use `uv run` prefix when the project uses `uv` (has `pyproject.toml` with `uv` or a `uv.lock` file).

**File:** `src/agent_native_workflow/detect.py`

In the detection logic that sets `lint_cmd` and `test_cmd`, check if the project uses `uv`. If it does, prefix detected Python tool commands (like `ruff`, `pytest`, `mypy`) with `uv run`. For example, if the current detection produces `ruff check src tests`, it should produce `uv run ruff check src tests` when `uv.lock` or a `[tool.uv]` section exists in `pyproject.toml`.

Look at the existing detection functions and find where `lint_cmd` and `test_cmd` are assigned. Add a helper that checks for `uv` usage and prefixes accordingly. The existing `_cmd_exists()` / `_resolve_cmd()` helpers can be used to check if `uv` is available.

---

## Fix 5 — Add `IterationOutcome.NO_PROGRESS`

**Problem:** When the pipeline breaks due to consecutive no-change iterations (Fix 1b), there's no specific outcome for this. Using `GATE_FAIL` is semantically wrong.

**File:** `src/agent_native_workflow/domain.py`

Add a new enum value to `IterationOutcome`:

```python
class IterationOutcome(str, Enum):
    PASS = "pass"
    GATE_FAIL = "gate_fail"
    VERIFY_FAIL = "verify_fail"
    SECURITY_FAIL = "security_fail"
    NO_PROGRESS = "no_progress"   # ← add this
```

Then update Fix 1b and Fix 1c code to use `IterationOutcome.NO_PROGRESS` instead of `IterationOutcome.GATE_FAIL` for the no-change cases.

---

## Fix 6 — Two-Tier Review with `codereview.md`

**Problem:** Agent R currently only checks requirements (MET / NOT MET / PARTIAL). It acts as a checklist machine, not a senior developer. Real code reviews also cover conventions, patterns, error handling, and project-specific practices.

**Solution:** Two changes:

1. Add support for an optional `codereview.md` file that defines project-specific review guidelines
2. Restructure Agent R's prompt into a **2-tier verdict**: blocking (requirements) vs advisory (code quality)

### 6a. Read `codereview.md` if it exists

**File:** `src/agent_native_workflow/strategies/review.py`, in the `run()` method.

Before building the prompt, check if a `codereview.md` file exists:

```python
codereview_path = store.base_dir / "codereview.md"
has_codereview = codereview_path.is_file()
```

### 6b. Restructure Agent R prompt with 2-tier verdict

**File:** `src/agent_native_workflow/strategies/review.py`

Replace the current `prompt` with a 2-tier structure. The full new prompt:

```python
codereview_section = ""
if has_codereview:
    codereview_section = f"""
## Code Quality Guidelines (Advisory)
Read `{codereview_path}` for project-specific conventions and patterns.
Violations of these guidelines do NOT block approval. List them in a
separate "Suggestions" section.
"""

prompt = f"""You are a senior developer reviewing code for correctness AND quality.

## Part 1: Requirements Check (Blocking)
Read `{requirements_file}` — this is the source of truth.

For each requirement:
- **Requirement**: [quote it]
- **Status**: MET / NOT MET / PARTIAL
- **Evidence**: specific code references
{codereview_section}
## Changed Files
{changed_section}

Read each changed file thoroughly.

## Consistency Check
If this is not the first review in this run, previous reviews are saved at:
`{store.run_dir}/iter-*/review.md`
Read your previous review(s) before deciding. Your verdict must be consistent
with prior reviews unless the code has actually changed since then.

## Your Review

### Blocking Issues
List anything where requirements are NOT MET or there are bugs/security issues.
These MUST be fixed before approval.

### Suggestions (Advisory)
Code quality improvements, convention violations, naming, patterns.
These do NOT block approval but are recommended.

## Verdict
If ALL requirements are MET and there are no blocking issues, output:
{REVIEW_APPROVE_MARKER}

Otherwise, list exactly what Agent A must fix (blocking issues only).
Advisory suggestions should NOT prevent approval."""
```

### 6c. Generate `codereview.md` template in `agn init`

**File:** `src/agent_native_workflow/commands/init_templates.py`

Add a new template constant:

```python
CODEREVIEW_MD = """\
# Code Review Guidelines

<!--
  Optional: project-specific conventions for Agent R (the reviewer).
  Agent R reads this file during review but violations here do NOT block approval.
  They appear as "Suggestions" in the review output.
  Delete this file if you only want requirements-based review.
-->

## Conventions
- Follow existing naming patterns in the codebase
- All public functions must have type hints and a docstring
- No bare `except:` — always specify exception type

## Patterns
- Use `pathlib.Path` over `os.path` for file operations
- Prefer dataclasses or Pydantic models over raw dicts for structured data

## Testing
- Tests must be deterministic (no sleep, no real network)
- Use fixtures for shared test setup
"""
```

**File:** `src/agent_native_workflow/commands/init.py`

Import `CODEREVIEW_MD` from `init_templates` and add generation logic after the `workflow_config_file` block:

```python
codereview_file = config_dir / "codereview.md"
if not codereview_file.exists():
    codereview_file.write_text(CODEREVIEW_MD)
    print(f"Created {codereview_file}")
else:
    print(f"Skipped {codereview_file} (already exists)")
```

### 6d. Update "Next steps" output

**File:** `src/agent_native_workflow/commands/init.py`

In the "Next steps" print block at the end, add a line:

```
  5. (Optional) Edit .agent-native-workflow/codereview.md — code conventions for reviewer
```

---

## Testing

After implementing all fixes, run:

```bash
uv run pytest tests/ -v
```

All existing tests must pass. Write new tests:

**`tests/test_pipeline_no_progress.py`** (new file):

1. **`test_no_change_drops_resume_on_first_occurrence`**: Mock Agent A to return output but change no files. Verify `agent_a_session` is set to `None` after iteration 1.
2. **`test_two_consecutive_no_change_breaks_pipeline`**: Mock Agent A to never change files. Verify pipeline breaks after 2 iterations (not `max_iterations`).
3. **`test_review_skipped_when_code_unchanged`**: Mock Agent A to change files in iter 1 but not iter 2. Verify Agent R is NOT called on iter 2.

**`tests/test_review_strategy.py`** (new file):

4. **`test_review_prompt_includes_consistency_check`**: Run ReviewStrategy with `iteration=2`. Verify the prompt passed to the runner contains `"iter-*/review.md"`.
5. **`test_review_prompt_includes_codereview_when_file_exists`**: Create a `codereview.md` in the store's base_dir. Run ReviewStrategy. Verify prompt contains `"Code Quality Guidelines"`.
6. **`test_review_prompt_excludes_codereview_when_file_missing`**: Run ReviewStrategy without `codereview.md`. Verify prompt does NOT contain `"Code Quality Guidelines"`.
7. **`test_review_two_tier_verdict_format`**: Verify the prompt contains both `"Blocking Issues"` and `"Suggestions (Advisory)"` sections.

---

## File Change Summary

| File | Change |
|---|---|
| `src/agent_native_workflow/store.py` | Add tool-use enforcement to `_build_resume_agent_a_context` |
| `src/agent_native_workflow/pipeline.py` | Add `consecutive_no_change` tracking, skip gates/review on no-change, reuse verdict on unchanged code |
| `src/agent_native_workflow/strategies/review.py` | 2-tier prompt (blocking + advisory), consistency check, `codereview.md` support |
| `src/agent_native_workflow/detect.py` | Add `uv run` prefix to detected lint/test commands when project uses `uv` |
| `src/agent_native_workflow/domain.py` | Add `NO_PROGRESS` to `IterationOutcome` |
| `src/agent_native_workflow/commands/init_templates.py` | Add `CODEREVIEW_MD` template |
| `src/agent_native_workflow/commands/init.py` | Generate `codereview.md` on `agn init`, update "Next steps" |
| `tests/test_pipeline_no_progress.py` | New: no-change detection and review-skip tests |
| `tests/test_review_strategy.py` | New: 2-tier prompt, consistency check, codereview.md tests |
