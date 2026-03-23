# AI-Native Engineering: Triangulation & Multi-Agent Pipeline


---

## Table of Contents

1. Triangulation Workflow (Final Model)
2. Full AI-Native Pipeline
3. Shared Knowledge Base
4. Trade-off Decision Model
5. TL;DR

---



## 01 — Triangulation Workflow

> **Core principle:** Each agent holds a deliberately scoped subset of information. No agent does another's job. Imperfection per iteration is acceptable — the loop handles convergence.
>
> **Simple agents × many iterations > Complex agents × few iterations**

### The Three Agents


| Agent                           | Role                                                                                        | Input                                                            | Output                                      |
| ------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------- |
| **A — Implementer**             | Writes or fixes code                                                                        | Ticket + all accumulated feedback from previous loops            | Code (diff / changed files)                 |
| **B — Senior Dev Reviewer**     | Reviews code quality only. Does NOT read ticket. Produces a plain-language changelog for C. | Diff / changed files only *(NOT ticket)*                         | Code review + plain-language change summary |
| **C — Requirements Judge (PM)** | Verifies requirements are met. Never judges code quality. Never sees raw code.              | Ticket + B's plain-language summary *(NOT code, NOT file paths)* | Pass / Fail + requirements verdict          |


### Why C Gets a Plain-Language Summary, Not a Navigator

If you give C a navigator with file paths and line references, C will follow those references — that's what LLMs do when context is available. The instruction "don't look at code" fights the model's natural tendency to be thorough.

**The fix:** B writes the summary like a changelog, not a technical report.

```
✅ Good (plain-language, no pointers back to code):
   "Added token expiry check. Added /profile endpoint.
    Rate limiting not implemented. Password reset flow untouched."

❌ Bad (navigator with line refs — C will follow these):
   "Token expiry check: auth/middleware.py:L44–58
    Profile endpoint: api/routes/user.py:L23–41"
```

C literally cannot look up raw code even if it wants to. This restores genuine role purity.

### Gate Condition

```
B approves code quality  AND  C confirms requirements met  →  PASS → exit loop
Either B or C rejects                                      →  FAIL → merge feedback → back to A
```

### The Feedback Loop


| Loop | Agent A receives                        | Effect                                      |
| ---- | --------------------------------------- | ------------------------------------------- |
| 1    | Ticket only                             | First implementation                        |
| 2    | Ticket + B1 review + C1 verdict         | Targeted fix — two independent perspectives |
| 3+   | Ticket + all accumulated B & C feedback | History prevents repeating same mistakes    |


> **Design rule:** Don't add complexity to an agent to compensate for a weakness the loop already handles.

---



## 02 — Full AI-Native Pipeline

> Triangulation (A+B+C) is the core. Additional agents are added only when a specific, recurring, costly failure is provable.

### Agent Map


| Agent                      | Role                    | Input                               | Notes                                                            |
| -------------------------- | ----------------------- | ----------------------------------- | ---------------------------------------------------------------- |
| **A**                      | Implementer             | Ticket + feedback history           | Core                                                             |
| **B**                      | Senior Dev Reviewer     | Diff only                           | Core — produces plain-language summary for C                     |
| **C**                      | Requirements Judge (PM) | Ticket + B's summary                | Core — never sees raw code                                       |
| ↩ *Loop until B ∧ C agree* |                         |                                     |                                                                  |
| **D**                      | QA Engineer             | Ticket + final code                 | Writes tests from user behavior POV, not implementation POV      |
| **E**                      | Red Teamer              | Code only — no requirements context | Adversarial mindset: "how do I break this?"                      |
| **Human**                  | Reviews agent verdicts  | Summaries from B, C, D, E           | Judges agent outputs, not raw code line-by-line                  |
| **F***                     | Docs Keeper             | Diff + ticket                       | Updates CLAUDE.md after merge. Long-term compounding value only. |


>  Agent F has near-zero short-term gain. Add only after A–D are running smoothly and docs rot is a proven problem.

### The Mindset Shift


| Traditional Engineering       | AI-Native Engineering                                        |
| ----------------------------- | ------------------------------------------------------------ |
| Human writes, human reviews   | Agent writes, agents cross-verify                            |
| Human catches bugs            | Agents catch bugs, human catches agent failures              |
| Knowledge lives in developers | Knowledge lives in agent prompts & CLAUDE.md                 |
| Senior devs are bottlenecks   | Senior devs **define agent personas** — expertise multiplied |
| Code review is synchronous    | Verification is parallel and automated                       |


---



## 03 — Shared Knowledge Base

> A large codebase carries architecture decisions, service responsibilities, conventions, design patterns, and historical context. This cannot live in any single agent's prompt — it must be structured so each agent pulls only the slice it needs.

### CLAUDE.md Hierarchy (Claude Code native)

```
/CLAUDE.md                          ← Global: architecture, ADRs, service map
/services/payments/CLAUDE.md        ← Service: why it exists, its contracts
/services/payments/src/CLAUDE.md    ← Code-level: patterns, conventions
```

Each agent, when working in a directory, automatically picks up context from the nearest CLAUDE.md. You don't manually inject it.

### What Each Agent Actually Needs


| Knowledge Type               | Agent A    | Agent B | Agent C |
| ---------------------------- | ---------- | ------- | ------- |
| Coding style & conventions   | ✅          | ✅       | —       |
| Design patterns in use       | ✅          | ✅       | —       |
| Architecture boundaries      | —          | ✅       | ✅       |
| Why THIS service exists      | ✅ (scoped) | —       | ✅       |
| Why OTHER services exist     | —          | —       | —       |
| Business requirements detail | —          | —       | ✅       |


### Codebase Evolution Policy — Must Be Made Explicit

Without this, A doesn't know when to conform vs. innovate, and B can't distinguish violations from suggestions.

- **Agent A:** Default to existing patterns UNLESS ticket explicitly says "refactor"
- **Agent B:** Distinguish `VIOLATION` (must fix) vs `SUGGESTION` (optional improvement)
- **Agent C:** Reject if change bleeds into another service's responsibility

### Agent F — Living Documentation Loop

After every merged ticket, Agent F reads what changed and why, then updates the relevant CLAUDE.md files. Six months from now, Agent A will have accurate institutional context — not stale docs nobody updated.

---



## 04 — Trade-off Decision Model

> Every agent added costs **tokens, latency, and complexity**. Returns on quality, safety, and trust diminish past a certain point. The goal is to stay on the steep part of the utility curve.

### The Cost Triangle


| Cost Dimension | How it compounds                                                                        |
| -------------- | --------------------------------------------------------------------------------------- |
| **Token cost** | `tokens/ticket × price/token × run frequency` — loops multiply this                     |
| **Latency**    | Sequential agents stack wait time. Parallel helps but complicates orchestration.        |
| **Complexity** | Each agent is a new failure point. Prompt drift, context conflicts, maintenance burden. |


### Net ROI by Agent

```
Net ROI = Marginal Gain − Marginal Cost   (illustrative, 0–10 scale)

Agent       Gain    Cost    Net ROI
─────────────────────────────────────
A            6.0     1.5    +4.5   ██████████████████████
B            9.5     2.0    +7.5   ██████████████████████████████
C            9.0     2.5    +6.5   ██████████████████████████
D (QA)       6.5     3.5    +3.0   ████████████
E (Sec)      4.0     4.5    -0.5   ░ (domain-dependent — can be positive for fintech/healthcare)
F (Docs)     2.0     3.0    -1.0   ░ (long-term compounding only)
─────────────────────────────────────
                              ▲
                         steep drop-off after A+B+C
                         for most product teams
```

> A+B+C gives ~80% of the value at ~40% of the full pipeline cost.

### The Decision Rule: Symptom-Driven Agent Addition

Only add an agent when you can point to a **specific, recurring, costly failure** it would catch.


| Symptom                          | Add                   |
| -------------------------------- | --------------------- |
| Bugs escaping to production      | Agent D (QA)          |
| Wrong features being built       | Agent C (PM judge)    |
| Code quality degrading over time | Agent B (Senior Dev)  |
| Security incidents               | Agent E (Red Team)    |
| Docs rotting, context lost       | Agent F (Docs Keeper) |


### Measuring Agent ROI

Track this ratio monthly per agent. If it drops below 1.0 — cut or merge.

```
ROI = (issues caught × avg cost of issue if escaped)
      ────────────────────────────────────────────────
            (tokens × cost per token × runs)
```

### Factors That Shift Your Optimal Point


| Factor            | Fewer agents   | More agents                        |
| ----------------- | -------------- | ---------------------------------- |
| Ticket complexity | Simple CRUD    | Complex business logic             |
| Domain risk       | Internal tools | Fintech / healthcare               |
| Rework cost       | Low stakes     | Production incidents are expensive |
| Speed priority    | Ship fast      | Ship safe                          |


> **"Agents should be as few as possible, but as many as necessary."**

---



## TL;DR

### Triangulation Core

- **A:** implements using ticket + full feedback history from all previous loops
- **B:** reviews diff only → produces code review + **plain-language changelog** (no file paths, no line refs)
- **C:** reads ticket + B's plain-language summary only — never touches raw code, never judges quality
- Loop until **both B and C pass independently**
- C gets a plain-language summary (not a navigator) so it literally cannot drift into reading code

### Full Pipeline

- A+B+C is the core. ~80% of value, ~40% of full pipeline cost
- Add D (QA), E (Security), F (Docs) only when ROI is provable from a specific recurring failure

### Shared Knowledge Base

- Use CLAUDE.md hierarchy — each agent pulls only what it needs
- Define an explicit **Codebase Evolution Policy** or agents will interpret "good code" differently
- Agent F keeps docs alive automatically after each merge

### Trade-off Model

- Symptom-driven addition only — never speculative
- Measure Agent ROI monthly. If ROI < 1.0, cut or merge
- Stay on the steep part of the utility curve

---

> **The meta-principle:** Your senior developers' job becomes defining the agents — writing system prompts, personas, and convergence criteria — rather than doing the reviews themselves. Their expertise gets multiplied across every ticket automatically.

