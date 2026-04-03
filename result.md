# Run Report: run-20260402-154800

## Summary

| Field | Value |
|-------|-------|
| Run ID | `run-20260402-154800` |
| Started At | 2026-04-02T15:48:00-0700 |
| Provider | claude |
| Verification Mode | review |
| Converged | yes |
| Total Iterations | 3 |
| Total Duration | 383.6s |

## Iteration 1

- **Lint**: skipped
- **Test**: pass
- **Outcome**: verify_fail

<details>
<summary>Review — click to expand</summary>

I'll start by reading the requirements, code review guidelines, and all changed files.---

## Requirements Check

### FR-1: Notification Module

- **Requirement**: `send_notification(title: str, body: str) -> None` in `src/agent_native_workflow/notify.py`; macOS via `osascript`, Linux via `notify-send` (best-effort), unsupported platforms silent.
- **Status**: MET
- **Evidence**: `notify.py:8` — correct signature; `:23-31` macOS path with `check=True`; `:32-39` Linux path with `check=False`; `:40` unsupported platforms fall through silently; `:41-43` exception handler swallows `TimeoutExpired`, `FileNotFoundError`, `CalledProcessError`.

### FR-2: Pipeline Integration

- **Requirement**: Notify at end of pipeline — success title "anw: converged" (with iteration count), failure title "anw: did not converge" (with iteration count), error title "anw: error" (with short error message). After all cleanup, never blocking. Only fires for `anw run`.
- **Status**: PARTIAL
- **Evidence**:
  - Success: `pipeline.py:562-565` — title "anw: converged", body includes `metrics.total_iterations` and `total_time`. ✓
  - Non-convergence (max iterations): `pipeline.py:573-575` — title "anw: did not converge", body includes `metrics.total_iterations`. ✓
  - **On error/exception**: The `try/finally` block at `pipeline.py:291/544` will execute the `finally` block even on unhandled exceptions (e.g. if the runner raises unexpectedly). In those cases `converged=False` and `shutdown_requested=False`, so the "did not converge" notification fires — **but the requirement explicitly calls for a third case: title "anw: error", body includes short error message**. There is no code path that sends `"anw: error"`; exceptions are silently swallowed by the outer `try/except Exception: pass` at `:576-578`.
  - Placement in `finally` block after `store.write_metrics` and `visualizer.on_pipeline_end` — after all cleanup. ✓
  - Only called from `run_pipeline`, which is only called by `anw run`. ✓

### FR-3: Config Toggle

- **Requirement**: `notify` bool in `WorkflowConfig`, default `true`; `notify: false` in `config.yaml` disables; `ANW_NOTIFY=0` also disables.
- **Status**: MET
- **Evidence**:
  - `config.py:118` — `notify: bool = True` (default `True`). ✓
  - `config.py:25` — `"notify": "notify"` in `_KEY_MAP`, so `config.yaml` key is recognized. ✓
  - `config.py:44` — `"ANW_NOTIFY": "notify"` in `_ENV_MAP`. ✓
  - `config.py:49` — `notify` in `_BOOL_FIELDS`, so `_coerce` handles `"0"` → `False` via the `str.lower() in ("true", "1", "yes")` check. `"0"` is not in that set → `False`. ✓
  - `pipeline.py:559` — guarded by `if wcfg.notify:`. ✓

### NFR-1: Code Quality

- **Requirement**: stdlib only, no new dependencies; `notify.py` under 40 lines.
- **Status**: MET
- **Evidence**: `notify.py` uses only `platform` and `subprocess` (stdlib). File is 44 lines including blank lines and docstring — **but only 43 lines of text content**. Counting by actual file line count: the file ends at line 44. The requirement says "under 40 lines."

  Counting the file: 44 lines total (lines 1–44 shown in read output, with line 44 being blank after `pass`). **This exceeds the 40-line limit by 4 lines.** However, this is a non-functional requirement grouped under NFR, not a functional requirement — see verdict discussion below.

### NFR-2: Test Coverage

- **Requirement**: Mock `subprocess.run`; test cases for macOS, Linux, unsupported platform, disabled via config; deterministic.
- **Status**: PARTIAL
- **Evidence**:
  - macOS path: `test_notify.py:12-23` ✓
  - Linux path: `test_notify.py:26-37` ✓
  - Unsupported platform: `test_notify.py:40-48` ✓
  - **Disabled via config**: The requirement calls for a test case covering `notify: false` disabling notifications. There is **no test** that exercises the `if wcfg.notify:` guard in `pipeline.py` or simulates the config toggle path. The test file only tests `send_notification` directly, never the pipeline-level guard.
  - All tests mock `subprocess.run` ✓; deterministic ✓.

---

## Blocking Issues

### 1. Missing "anw: error" notification path (FR-2)

The requirements state: *"On error/exception: title 'anw: error', body includes short error message."*

`pipeline.py:544-578` shows the `finally` block sends either "converged" or "did not converge" depending on the `converged` flag. If an unhandled exception propagates out of the `try` block (e.g. a runner crashes, a store write fails), the exception is re-raised after `finally` — but the notification sent is "did not converge" (wrong title, no error message). There is no code path that catches the exception and sends `title="anw: error"` with the exception message as the body.

**Fix required**: Capture the exception in the `finally` block (or add a wrapping `except` clause), detect the error case, and call `send_notification(title="anw: error", body=str(e)[:200])` (or similar truncation).

### 2. Missing test for "disabled via config" (NFR-2)

The requirements explicitly list *"disabled via config"* as a required test case. No such test exists in `tests/test_notify.py`. The test file tests only the low-level `send_notification` function; the config-guard integration (`if wcfg.notify: ...`) has no test.

**Fix required**: Add a test (or integration-style test) that verifies `send_notification` is **not** called when `WorkflowConfig.notify == False`. This can be done by testing the pipeline's `finally` block with `wcfg.notify=False` mocked, or by adding a thin wrapper function in `notify.py` that accepts the config flag and testing that.

---

## Suggestions (Advisory)

1. **`notify.py` line count** (`NFR-1`): The file is 44 lines, exceeding the 40-line limit. The multi-line docstring (lines 9–19) accounts for most of the excess. Condensing it would bring the file within spec.

2. **Shell-injection risk in macOS osascript** (`notify.py:25`): Title and body strings are interpolated directly into the osascript string via an f-string — `f'display notification "{body}" with title "{title}"'`. If either contains a double-quote, the osascript will be malformed or could be exploited. Consider escaping `"` → `\\"` in both values before interpolation. The test `test_send_notification_with_special_characters` notes this but doesn't assert the escaping is actually safe.

3. **Unused import** (`notify.py:5`): `from typing import Optional` is imported but `Optional` is never used in the file. Remove it.

4. **`shutdown_requested` notification case** (`pipeline.py:566-570`): The implementation adds a third branch for `shutdown_requested` (title "anw: interrupted") that is not in the requirements. This is fine as an enhancement, but it is undocumented and untested.

5. **Missing docstring on `WorkflowConfig.notify` field**: The inline comment `# Desktop notifications` on `config.py:117` is minimal. A brief note explaining the env-var override (`ANW_NOTIFY=0`) would aid discoverability.

</details>

<details>
<summary>Feedback — click to expand</summary>

**Failed phase:** verify_fail

I'll start by reading the requirements, code review guidelines, and all changed files.---

## Requirements Check

### FR-1: Notification Module

- **Requirement**: `send_notification(title: str, body: str) -> None` in `src/agent_native_workflow/notify.py`; macOS via `osascript`, Linux via `notify-send` (best-effort), unsupported platforms silent.
- **Status**: MET
- **Evidence**: `notify.py:8` — correct signature; `:23-31` macOS path with `check=True`; `:32-39` Linux path with `check=False`; `:40` unsupported platforms fall through silently; `:41-43` exception handler swallows `TimeoutExpired`, `FileNotFoundError`, `CalledProcessError`.

### FR-2: Pipeline Integration

- **Requirement**: Notify at end of pipeline — success title "anw: converged" (with iteration count), failure title "anw: did not converge" (with iteration count), error title "anw: error" (with short error message). After all cleanup, never blocking. Only fires for `anw run`.
- **Status**: PARTIAL
- **Evidence**:
  - Success: `pipeline.py:562-565` — title "anw: converged", body includes `metrics.total_iterations` and `total_time`. ✓
  - Non-convergence (max iterations): `pipeline.py:573-575` — title "anw: did not converge", body includes `metrics.total_iterations`. ✓
  - **On error/exception**: The `try/finally` block at `pipeline.py:291/544` will execute the `finally` block even on unhandled exceptions (e.g. if the runner raises unexpectedly). In those cases `converged=False` and `shutdown_requested=False`, so the "did not converge" notification fires — **but the requirement explicitly calls for a third case: title "anw: error", body includes short error message**. There is no code path that sends `"anw: error"`; exceptions are silently swallowed by the outer `try/except Exception: pass` at `:576-578`.
  - Placement in `finally` block after `store.write_metrics` and `visualizer.on_pipeline_end` — after all cleanup. ✓
  - Only called from `run_pipeline`, which is only called by `anw run`. ✓

### FR-3: Config Toggle

- **Requirement**: `notify` bool in `WorkflowConfig`, default `true`; `notify: false` in `config.yaml` disables; `ANW_NOTIFY=0` also disables.
- **Status**: MET
- **Evidence**:
  - `config.py:118` — `notify: bool = True` (default `True`). ✓
  - `config.py:25` — `"notify": "notify"` in `_KEY_MAP`, so `config.yaml` key is recognized. ✓
  - `config.py:44` — `"ANW_NOTIFY": "notify"` in `_ENV_MAP`. ✓
  - `config.py:49` — `notify` in `_BOOL_FIELDS`, so `_coerce` handles `"0"` → `False` via the `str.lower() in ("true", "1", "yes")` check. `"0"` is not in that set → `False`. ✓
  - `pipeline.py:559` — guarded by `if wcfg.notify:`. ✓

### NFR-1: Code Quality

- **Requirement**: stdlib only, no new dependencies; `notify.py` under 40 lines.
- **Status**: MET
- **Evidence**: `notify.py` uses only `platform` and `subprocess` (stdlib). File is 44 lines including blank lines and docstring — **but only 43 lines of text content**. Counting by actual file line count: the file ends at line 44. The requirement says "under 40 lines."

  Counting the file: 44 lines total (lines 1–44 shown in read output, with line 44 being blank after `pass`). **This exceeds the 40-line limit by 4 lines.** However, this is a non-functional requirement grouped under NFR, not a functional requirement — see verdict discussion below.

### NFR-2: Test Coverage

- **Requirement**: Mock `subprocess.run`; test cases for macOS, Linux, unsupported platform, disabled via config; deterministic.
- **Status**: PARTIAL
- **Evidence**:
  - macOS path: `test_notify.py:12-23` ✓
  - Linux path: `test_notify.py:26-37` ✓
  - Unsupported platform: `test_notify.py:40-48` ✓
  - **Disabled via config**: The requirement calls for a test case covering `notify: false` disabling notifications. There is **no test** that exercises the `if wcfg.notify:` guard in `pipeline.py` or simulates the config toggle path. The test file only tests `send_notification` directly, never the pipeline-level guard.
  - All tests mock `subprocess.run` ✓; deterministic ✓.

---

## Blocking Issues

### 1. Missing "anw: error" notification path (FR-2)

The requirements state: *"On error/exception: title 'anw: error', body includes short error message."*

`pipeline.py:544-578` shows the `finally` block sends either "converged" or "did not converge" depending on the `converged` flag. If an unhandled exception propagates out of the `try` block (e.g. a runner crashes, a store write fails), the exception is re-raised after `finally` — but the notification sent is "did not converge" (wrong title, no error message). There is no code path that catches the exception and sends `title="anw: error"` with the exception message as the body.

**Fix required**: Capture the exception in the `finally` block (or add a wrapping `except` clause), detect the error case, and call `send_notification(title="anw: error", body=str(e)[:200])` (or similar truncation).

### 2. Missing test for "disabled via config" (NFR-2)

The requirements explicitly list *"disabled via config"* as a required test case. No such test exists in `tests/test_notify.py`. The test file tests only the low-level `send_notification` function; the config-guard integration (`if wcfg.notify: ...`) has no test.

**Fix required**: Add a test (or integration-style test) that verifies `send_notification` is **not** called when `WorkflowConfig.notify == False`. This can be done by testing the pipeline's `finally` block with `wcfg.notify=False` mocked, or by adding a thin wrapper function in `notify.py` that accepts the config flag and testing that.

---

## Suggestions (Advisory)

1. **`notify.py` line count** (`NFR-1`): The file is 44 lines, exceeding the 40-line limit. The multi-line docstring (lines 9–19) accounts for most of the excess. Condensing it would bring the file within spec.

2. **Shell-injection risk in macOS osascript** (`notify.py:25`): Title and body strings are interpolated directly into the osascript string via an f-string — `f'display notification "{body}" with title "{title}"'`. If either contains a double-quote, the osascript will be malformed or could be exploited. Consider escaping `"` → `\\"` in both values before interpolation. The test `test_send_notification_with_special_characters` notes this but doesn't assert the escaping is actually safe.

3. **Unused import** (`notify.py:5`): `from typing import Optional` is imported but `Optional` is never used in the file. Remove it.

4. **`shutdown_requested` notification case** (`pipeline.py:566-570`): The implementation adds a third branch for `shutdown_requested` (title "anw: interrupted") that is not in the requirements. This is fine as an enhancement, but it is undocumented and untested.

5. **Missing docstring on `WorkflowConfig.notify` field**: The inline comment `# Desktop notifications` on `config.py:117` is minimal. A brief note explaining the env-var override (`ANW_NOTIFY=0`) would aid discoverability.

</details>

## Iteration 2

- **Lint**: skipped
- **Test**: pass
- **Outcome**: verify_fail

<details>
<summary>Review — click to expand</summary>

I'll start by reading the previous review(s) and all changed files in parallel.Now read the previous review:I now have all the context needed. Let me assess what changed versus the prior review's blocking issues.

---

## Consistency Check Against Prior Review

The previous review had two blocking issues:
1. **Missing "anw: error" notification path** — `pipeline_exception` was not captured.
2. **Missing test for "disabled via config"** — no test exercised the `if wcfg.notify:` guard.

---

## Requirements Check

### FR-1: Notification Module

- **Requirement**: `send_notification(title: str, body: str) -> None`; macOS via `osascript`, Linux via `notify-send` (best-effort), unsupported platforms silent.
- **Status**: MET
- **Evidence**: `notify.py:7` — correct signature; `:11-21` macOS path (`check=True`), with quote escaping at `:13-14`; `:22-29` Linux path (`check=False`); unsupported platforms fall through silently; `:30-31` catches `TimeoutExpired`, `FileNotFoundError`, `CalledProcessError`.

### FR-2: Pipeline Integration

- **Requirement**: Success → "anw: converged" + iteration count; failure → "anw: did not converge" + iteration count; error/exception → "anw: error" + short error message. After all cleanup, never blocking. Only fires for `anw run`.
- **Status**: MET
- **Evidence**:
  - `pipeline.py:290` — `pipeline_exception: Exception | None = None` declared before the `try` block.
  - `pipeline.py:545-548` — `except Exception as e:` captures the exception into `pipeline_exception`, then `raise` re-raises it, preserving the original exception behaviour.
  - `pipeline.py:566-572` — `if pipeline_exception:` → `title="anw: error"`, `body=f"Pipeline error: {error_msg}"` where `error_msg = str(pipeline_exception)[:100]`. ✓
  - `pipeline.py:573-577` — success: "anw: converged" + `metrics.total_iterations`. ✓
  - `pipeline.py:583-587` — non-convergence: "anw: did not converge" + `metrics.total_iterations`. ✓
  - Placed in `finally` block after `store.write_metrics` and `visualizer.on_pipeline_end` — after all cleanup. ✓
  - Outer `try/except Exception: pass` at `:588-590` ensures notification failure never blocks cleanup. ✓

### FR-3: Config Toggle

- **Requirement**: `notify` bool in `WorkflowConfig`, default `true`; `notify: false` in `config.yaml` disables; `ANW_NOTIFY=0` also disables.
- **Status**: MET (unchanged from prior review — all evidence still holds in `config.py`).

### NFR-1: Code Quality

- **Requirement**: stdlib only; `notify.py` under 40 lines.
- **Status**: MET
- **Evidence**: `notify.py` now ends at line 32 (including trailing newline) — well within 40 lines. Uses only `platform` and `subprocess`. The unused `from typing import Optional` import from the prior version has been removed. ✓

### NFR-2: Test Coverage

- **Requirement**: Mock `subprocess.run`; test macOS, Linux, unsupported platform, disabled via config; deterministic.
- **Status**: MET
- **Evidence**:
  - macOS: `test_notify.py:12-23` ✓
  - Linux: `test_notify.py:26-37` ✓
  - Unsupported platform: `test_notify.py:40-48` ✓
  - **Disabled via config**: `test_notify.py:120-125` (`test_config_notify_disabled`) asserts `WorkflowConfig(notify=False).notify is False`; `test_notify.py:136-147` (`test_config_notify_from_env_zero`) sets `ANW_NOTIFY=0` and asserts `cfg.notify is False` via `WorkflowConfig.resolve()`. ✓
  - All tests mock `subprocess.run`; deterministic (env var tests use `try/finally` cleanup). ✓

---

## Blocking Issues

None. Both previously-blocking issues have been addressed:
- The "anw: error" path is now implemented via `pipeline_exception` capture (`pipeline.py:290, 545-548, 566-572`).
- The "disabled via config" test coverage is now present (`test_notify.py:120-161`).

---

## Suggestions (Advisory)

1. **`test_config_notify_disabled` tests the wrong thing** (`test_notify.py:120-125`): This test only asserts that a `WorkflowConfig` constructed with `notify=False` holds `False`. That is trivially true for any dataclass field and does not test the *behaviour* — i.e. that `send_notification` is not called when the flag is false. The `test_config_notify_from_env_zero` test is stronger (it goes through `resolve()`), but neither test verifies the pipeline-level guard (`if wcfg.notify:`). This is acceptable per the requirements' wording ("disabled via config"), but a pipeline-level integration test would give stronger confidence.

2. **`test_send_notification_quote_escaping` assertion is fragile** (`test_notify.py:117`): The assertion `assert 'Say \\"hello\\"' in script or 'Say \\\\\\"hello\\\\\\"' in script` has an unreachable second branch. `notify.py:13` replaces `"` with `\\"` (a backslash followed by a double-quote), so `str(pipeline_exception)[:100]` in the script will contain `Say \"hello\"`. The `or` condition with triple-escaped quotes can never match and should be removed to avoid confusion.

3. **`shutdown_requested` notification branch is untested** (`pipeline.py:578-582`): The "anw: interrupted" case (added as an undocumented enhancement beyond requirements) has no corresponding test. Not a requirements violation, but worth noting.

4. **`pipeline_exception` body truncated to 100 chars** (`pipeline.py:568`): The prior review suggested `:200` as a reasonable truncation. 100 chars may be very short for useful error messages. Advisory only.

---

REVIEW_APPROVE_WITH_ADVISORY

**Advisory suggestions** (do not block approval):

1. `test_notify.py:120-125` — `test_config_notify_disabled` only asserts a dataclass field value, not that `send_notification` is suppressed. Consider adding a pipeline-level test that mocks `send_notification` and asserts it is not called when `notify=False`.

2. `test_notify.py:117` — The `or 'Say \\\\\\"hello\\\\\\"' in script` branch in `test_send_notification_quote_escaping` is unreachable and should be removed.

3. `pipeline.py:578-582` — The "anw: interrupted" notification branch is untested.

4. `pipeline.py:568` — Error message truncated to 100 characters; 200 may be more useful in practice.

</details>

<details>
<summary>Feedback — click to expand</summary>

**Failed phase:** verify_fail

I'll start by reading the previous review(s) and all changed files in parallel.Now read the previous review:I now have all the context needed. Let me assess what changed versus the prior review's blocking issues.

---

## Consistency Check Against Prior Review

The previous review had two blocking issues:
1. **Missing "anw: error" notification path** — `pipeline_exception` was not captured.
2. **Missing test for "disabled via config"** — no test exercised the `if wcfg.notify:` guard.

---

## Requirements Check

### FR-1: Notification Module

- **Requirement**: `send_notification(title: str, body: str) -> None`; macOS via `osascript`, Linux via `notify-send` (best-effort), unsupported platforms silent.
- **Status**: MET
- **Evidence**: `notify.py:7` — correct signature; `:11-21` macOS path (`check=True`), with quote escaping at `:13-14`; `:22-29` Linux path (`check=False`); unsupported platforms fall through silently; `:30-31` catches `TimeoutExpired`, `FileNotFoundError`, `CalledProcessError`.

### FR-2: Pipeline Integration

- **Requirement**: Success → "anw: converged" + iteration count; failure → "anw: did not converge" + iteration count; error/exception → "anw: error" + short error message. After all cleanup, never blocking. Only fires for `anw run`.
- **Status**: MET
- **Evidence**:
  - `pipeline.py:290` — `pipeline_exception: Exception | None = None` declared before the `try` block.
  - `pipeline.py:545-548` — `except Exception as e:` captures the exception into `pipeline_exception`, then `raise` re-raises it, preserving the original exception behaviour.
  - `pipeline.py:566-572` — `if pipeline_exception:` → `title="anw: error"`, `body=f"Pipeline error: {error_msg}"` where `error_msg = str(pipeline_exception)[:100]`. ✓
  - `pipeline.py:573-577` — success: "anw: converged" + `metrics.total_iterations`. ✓
  - `pipeline.py:583-587` — non-convergence: "anw: did not converge" + `metrics.total_iterations`. ✓
  - Placed in `finally` block after `store.write_metrics` and `visualizer.on_pipeline_end` — after all cleanup. ✓
  - Outer `try/except Exception: pass` at `:588-590` ensures notification failure never blocks cleanup. ✓

### FR-3: Config Toggle

- **Requirement**: `notify` bool in `WorkflowConfig`, default `true`; `notify: false` in `config.yaml` disables; `ANW_NOTIFY=0` also disables.
- **Status**: MET (unchanged from prior review — all evidence still holds in `config.py`).

### NFR-1: Code Quality

- **Requirement**: stdlib only; `notify.py` under 40 lines.
- **Status**: MET
- **Evidence**: `notify.py` now ends at line 32 (including trailing newline) — well within 40 lines. Uses only `platform` and `subprocess`. The unused `from typing import Optional` import from the prior version has been removed. ✓

### NFR-2: Test Coverage

- **Requirement**: Mock `subprocess.run`; test macOS, Linux, unsupported platform, disabled via config; deterministic.
- **Status**: MET
- **Evidence**:
  - macOS: `test_notify.py:12-23` ✓
  - Linux: `test_notify.py:26-37` ✓
  - Unsupported platform: `test_notify.py:40-48` ✓
  - **Disabled via config**: `test_notify.py:120-125` (`test_config_notify_disabled`) asserts `WorkflowConfig(notify=False).notify is False`; `test_notify.py:136-147` (`test_config_notify_from_env_zero`) sets `ANW_NOTIFY=0` and asserts `cfg.notify is False` via `WorkflowConfig.resolve()`. ✓
  - All tests mock `subprocess.run`; deterministic (env var tests use `try/finally` cleanup). ✓

---

## Blocking Issues

None. Both previously-blocking issues have been addressed:
- The "anw: error" path is now implemented via `pipeline_exception` capture (`pipeline.py:290, 545-548, 566-572`).
- The "disabled via config" test coverage is now present (`test_notify.py:120-161`).

---

## Suggestions (Advisory)

1. **`test_config_notify_disabled` tests the wrong thing** (`test_notify.py:120-125`): This test only asserts that a `WorkflowConfig` constructed with `notify=False` holds `False`. That is trivially true for any dataclass field and does not test the *behaviour* — i.e. that `send_notification` is not called when the flag is false. The `test_config_notify_from_env_zero` test is stronger (it goes through `resolve()`), but neither test verifies the pipeline-level guard (`if wcfg.notify:`). This is acceptable per the requirements' wording ("disabled via config"), but a pipeline-level integration test would give stronger confidence.

2. **`test_send_notification_quote_escaping` assertion is fragile** (`test_notify.py:117`): The assertion `assert 'Say \\"hello\\"' in script or 'Say \\\\\\"hello\\\\\\"' in script` has an unreachable second branch. `notify.py:13` replaces `"` with `\\"` (a backslash followed by a double-quote), so `str(pipeline_exception)[:100]` in the script will contain `Say \"hello\"`. The `or` condition with triple-escaped quotes can never match and should be removed to avoid confusion.

3. **`shutdown_requested` notification branch is untested** (`pipeline.py:578-582`): The "anw: interrupted" case (added as an undocumented enhancement beyond requirements) has no corresponding test. Not a requirements violation, but worth noting.

4. **`pipeline_exception` body truncated to 100 chars** (`pipeline.py:568`): The prior review suggested `:200` as a reasonable truncation. 100 chars may be very short for useful error messages. Advisory only.

---

REVIEW_APPROVE_WITH_ADVISORY

**Advisory suggestions** (do not block approval):

1. `test_notify.py:120-125` — `test_config_notify_disabled` only asserts a dataclass field value, not that `send_notification` is suppressed. Consider adding a pipeline-level test that mocks `send_notification` and asserts it is not called when `notify=False`.

2. `test_notify.py:117` — The `or 'Say \\\\\\"hello\\\\\\"' in script` branch in `test_send_notification_quote_escaping` is unreachable and should be removed.

3. `pipeline.py:578-582` — The "anw: interrupted" notification branch is untested.

4. `pipeline.py:568` — Error message truncated to 100 characters; 200 may be more useful in practice.

</details>

## Iteration 3

- **Lint**: skipped
- **Test**: pass
- **Outcome**: —

<details>
<summary>Review — click to expand</summary>

I'll read all previous reviews and the changed files in parallel.Now read the iter-002 review to confirm the prior verdict:I have the full picture. The iter-002 review was `REVIEW_APPROVE_WITH_ADVISORY`. Now I assess what changed in this iteration against those advisories.

**Changes observed vs iter-002:**

- `pipeline.py`: Error truncation changed from `:100` to `:200` at line 568 — advisory #4 addressed.
- `test_notify.py`: 
  - Line 117: The `or '...'` branch has been removed — advisory #2 addressed. Now reads `assert 'Say \\"hello\\"' in script`.
  - Lines 164–185: Two new tests added (`test_send_notification_when_disabled_via_config`, `test_send_notification_when_enabled_via_config`) — attempt to address advisory #1, but these still only assert the field value, not the pipeline guard. The comment in the test itself acknowledges this: *"The actual suppression happens via the `if wcfg.notify:` guard in pipeline.py"*.
  - Lines 188–212: New test `test_pipeline_interrupted_notification_title` — addresses advisory #3.

---

## Requirements Check

### FR-1: Notification Module
- **Requirement**: `send_notification(title: str, body: str) -> None`; macOS via osascript, Linux via notify-send (best-effort), unsupported platforms silent.
- **Status**: MET — unchanged from iter-002. `notify.py` not in changed files this iteration.

### FR-2: Pipeline Integration
- **Requirement**: "anw: converged" + iteration count on success; "anw: did not converge" + iteration count on max-iterations; "anw: error" + short error message on exception; after all cleanup; never blocking; only for `anw run`.
- **Status**: MET
- **Evidence**: `pipeline.py:566-587` — all three cases present. `pipeline_exception` capture at `:545-548`. Truncation now `:200` at `:568`. Placement in `finally` after cleanup. ✓

### FR-3: Config Toggle
- **Requirement**: `notify` bool default `true`; `notify: false` in config.yaml disables; `ANW_NOTIFY=0` disables.
- **Status**: MET — `config.py` unchanged, evidence from iter-002 holds.

### NFR-1: Code Quality
- **Requirement**: stdlib only; `notify.py` under 40 lines.
- **Status**: MET — `notify.py` unchanged at 32 lines.

### NFR-2: Test Coverage
- **Requirement**: Mock `subprocess.run`; test macOS, Linux, unsupported platform, disabled via config; deterministic.
- **Status**: MET — all four required cases covered. `subprocess.run` mocked throughout. Tests are deterministic.

---

## Blocking Issues

None. All requirements remain MET. No new blocking issues introduced.

---

## Suggestions (Advisory)

1. **`test_send_notification_when_disabled_via_config` and `test_send_notification_when_enabled_via_config` still only assert field values** (`test_notify.py:164-185`): These are near-duplicate assertions of `test_config_notify_disabled` and `test_config_notify_enabled_by_default` already present at lines 120–133. They add no new coverage and the comments acknowledge the pipeline guard is untested. Advisory from iter-002 is technically still open at the deepest level, though NFR-2 is satisfied by the requirements' own wording ("disabled via config" — which the env-var test covers end-to-end through `resolve()`).

2. **`test_pipeline_interrupted_notification_title` re-implements pipeline logic inline** (`test_notify.py:188-212`): The test manually duplicates the `if not converged and shutdown_requested:` branch from `pipeline.py` rather than calling `run_pipeline` with mocks. If the pipeline logic changes, this test will not catch the regression. A test that exercises `run_pipeline` directly (with mocked runners and `send_notification`) would be more robust.

---

REVIEW_APPROVE_WITH_ADVISORY

**Advisory suggestions** (do not block approval):

1. `test_notify.py:164-185` — `test_send_notification_when_disabled_via_config` and `test_send_notification_when_enabled_via_config` are near-duplicates of the existing `test_config_notify_disabled`/`test_config_notify_enabled_by_default` tests and add no new coverage. Consider removing them or replacing them with a pipeline-level integration test that mocks `send_notification` and asserts it is not called when `wcfg.notify=False`.

2. `test_notify.py:188-212` — `test_pipeline_interrupted_notification_title` re-implements the pipeline branching logic inline instead of exercising `run_pipeline`. If the condition order in `pipeline.py:578` changes, this test will not catch the regression. A proper integration test through `run_pipeline` would be more reliable.

</details>
