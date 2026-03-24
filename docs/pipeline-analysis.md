# Pipeline Analysis: Observed Problems & Improvement Roadmap

> Based on live run analysis of `run-20260323-225916` (agn clean implementation, 3 iterations, 308s)

---

## 1. What the Run Looked Like

```
iter-1 (190s)  Agent A → 3 files changed → tests PASS → Agent R: FAIL (--keep 0 bug)
iter-2  (62s)  Agent A → 0 files changed → tests PASS → Agent R: FAIL (same bug)
iter-3  (56s)  Agent A → 0 files changed → tests PASS → Agent R: PASS  ← false convergence
```

Converged in 3 iterations — but **not because the bug was fixed**. The code was identical in iterations 1, 2, and 3. Agent R randomly approved on iteration 3.

---

## 2. Problems Found

### Problem 1 — False Convergence (Most Critical)

Agent R rejected the exact same code in iterations 1 and 2, then approved it in iteration 3 without any change to the implementation.

**Root cause:** Agent R has no memory of its previous verdicts. Each iteration it reads the code fresh and makes a probabilistic judgment. Same input, different LLM output — pure non-determinism.

**Impact:** The pipeline can "converge" with bugs still present. Convergence currently means "Agent R happened to approve this time," not "the code is correct."

---

### Problem 2 — Agent A Hallucinated File Edits

```
iter-002: [Phase 1] No file changes detected from Agent A
iter-003: [Phase 1] No file changes detected from Agent A
```

In both iterations, Agent A produced detailed output describing exactly what it "fixed" — with specific line numbers, before/after code snippets — but made zero actual file changes.

**Root cause:** Session resume (`--resume <session_id>`) gives Agent A conversation memory of what it wrote in iteration 1. In the resumed session, when it receives bug feedback, it treats the existing file as already-known code and *describes* the fix in natural language instead of actually calling the Edit/Write tool. The session memory creates overconfidence: "I already know this code, I'll just tell you what I changed."

In iteration 1 (fresh session), Claude Code defaults to actually using tools to write files. In resumed sessions, it can drift toward narrating edits instead of executing them. **Session memory inadvertently suppresses tool execution.**

Note: `build_agent_a_context` already passes all necessary context (gates, feedback, changed files) as a structured prompt. So session resume is adding history without adding value — while introducing this hallucination risk.

---

### Problem 3 — No Consequence for "No Changes" Detected

The pipeline logs `No file changes detected from Agent A` but takes no action. It proceeds to run quality gates (same outcome as before) and Agent R (same code as before) — spending time and tokens on work that cannot improve the situation.

**Impact:** In the worst case, the loop runs all max_iterations with zero changes, and converges only if Agent R randomly approves.

---

### Problem 4 — Agent R Has No Cross-Iteration Context

Each Agent R call starts fresh. It doesn't know:
- That it already reviewed this code
- What verdict it gave last time
- That nothing changed since the last review

**Impact:** Redundant work (21-27s per review even for identical code) and inconsistent verdicts.

---

### Problem 5 — Lint Gate Skipped

```
LINT_CMD: <none — will skip>
lint_result: "skipped"  (all 3 iterations)
```

`detect_all()` returns `uv run ruff check src/` when run directly from the CLI. Inside the pipeline subprocess, `ruff` isn't found. This is likely a PATH/virtualenv resolution issue specific to the subprocess environment.

**Impact:** Lint errors in Agent A's output go undetected until code review.

---

## 3. Root Cause Summary

| Problem | Root Cause |
|---|---|
| False convergence | Agent R is stateless — no memory of prior verdicts |
| Hallucinated edits | `--resume` session memory suppresses tool execution in resumed sessions |
| No-change loop | Pipeline detects no-change but has no escalation path |
| Redundant Agent R reviews | Agent R re-reads identical code with no prior context |
| Lint skipped | `ruff` not in PATH inside pipeline subprocess environment |

---

## 4. Improvement Roadmap

### Fix A — Remove Session Resume for Agent A (High Priority)

`build_agent_a_context` already provides all necessary context as a structured prompt. Session resume adds conversation history on top of this — which causes the hallucination pattern above.

**Proposed change:** Remove `--resume` from Agent A. Use `--session-id` (fresh session) on every iteration, pass context entirely through the prompt.

Downside: Loses conversational continuity (Agent A won't "remember" why it made certain choices). Upside: Eliminates the hallucinated-edit problem entirely.

Alternative: Keep resume but inject an explicit instruction into the system preamble:
> "You MUST use the Edit or Write tool to make actual file changes. Describing changes in text without calling a tool has no effect — the pipeline verifies by checking git status."

---

### Fix B — Break Loop on Consecutive No-Change Iterations (High Priority)

When `files_changed_since()` returns empty, the current outcome (run gates → run Agent R → loop) is deterministic: gates will produce the same result, Agent R will get the same code.

**Proposed behavior:**
- First no-change: log warning, append explicit instruction to next Agent A prompt: "WARNING: You made no file edits in the previous iteration. You must use Edit/Write tools to modify actual files."
- Second consecutive no-change: fail the pipeline immediately with `NO_PROGRESS` outcome. Do not run gates or Agent R.

---

### Fix C — Give Agent R Cross-Iteration Memory (Medium Priority)

When Agent R is called on iteration N > 1, prepend its previous review(s) to the prompt:

```
Previous review (iter N-1): [content of review.md from iter N-1]
Code has changed since then: YES / NO

Now review the current state...
```

If code has NOT changed since the last review, skip Agent R and reuse the previous verdict directly. This eliminates both the redundant work and the inconsistent-verdict problem.

---

### Fix D — Require Agent R to Pass Twice (Medium Priority)

To prevent false convergence from a single lucky approval, require Agent R to pass on two consecutive iterations before declaring convergence. One pass after multiple fails could be noise.

This trades one extra iteration for much stronger confidence in the verdict.

---

### Fix E — Pin Lint Command in config.yaml (Low Priority)

Add `lint-cmd` as a non-commented default in `agn init`'s generated config.yaml, based on detected project type. For Python projects: `lint-cmd: uv run ruff check src tests`.

This bypasses the PATH detection issue entirely and makes lint behavior explicit and visible to the user.

---

## 5. Priority Order

| Priority | Fix | Expected Impact |
|---|---|---|
| 1 | Remove/fix session resume for Agent A | Eliminates hallucinated edits |
| 2 | Break loop on consecutive no-change | Prevents infinite no-progress loops |
| 3 | Pass previous review to Agent R | Eliminates redundant reviews + inconsistent verdicts |
| 4 | Require 2× Agent R pass | Prevents single-flip false convergence |
| 5 | Pin lint-cmd in config.yaml default | Makes lint gate reliable |
