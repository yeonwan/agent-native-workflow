# Design: Blacklist Permission Model

## Status: Draft

## Problem

현재 ANW는 **화이트리스트** 방식으로 에이전트 도구를 관리한다:

```yaml
agents:
  agent_a:
    allowed_tools:
      - Read
      - Edit
      - Write
      - Bash(git:status)
      - Bash(./gradlew compileJava)
      # ... 하나하나 열거
```

이 방식의 문제:

1. **두더지 잡기** — 빠뜨린 도구가 있으면 에이전트가 무력화됨. Java 프로젝트에서 `javap`, `strings` 등이 빠져서 JAR 의존성을 탐색하지 못한 사례 발생.
2. **프로젝트 타입별 관리 부담** — 언어/빌드시스템마다 필요한 도구가 다르고, 예측 불가능한 도구까지 미리 열거해야 함.
3. **에이전트의 문제 해결 능력 제한** — 동일 모델이 Claude Code (열린 권한)에서는 알아서 `javap`를 치는데, ANW에서는 못 함. 모델 능력이 아니라 권한 제한이 병목.

## Insight

Claude Code / IDE Copilot이 잘 동작하는 이유는 모델이 똑똑해서가 아니라 **권한이 열려있어서**:

```
Interactive:   열린 권한 → 모델의 기존 지식이 자유롭게 발현 → 성공
ANW 현재:      닫힌 권한 → 모델은 알지만 실행 차단 → 추측 → 실패
```

Interactive 모드의 핵심은 대화가 아니라 **"기본 허용, 위험한 것만 차단"** 이라는 권한 모델.

## Solution: Blacklist Permission Mode

### 원칙

```
화이트리스트: 기본 차단 + 허용 목록 → 빠뜨리면 실패
블랙리스트:   기본 허용 + 차단 목록 → 위험한 것만 막으면 나머지는 모델이 알아서
```

### CLI-Level Enforcement

두 CLI 모두 deny 플래그를 네이티브로 지원:

```
Claude:  --disallowedTools, --disallowed-tools <tools...>
Copilot: --deny-tool=<tools...>
```

**프롬프트로 "믿는" 게 아니라 CLI가 물리적으로 차단.** 에이전트가 차단된 도구를 호출하면
CLI 자체가 실행을 거부하고 에이전트에게 에러를 반환.

```
┌─────────────────────────────────────────────────┐
│  AS-IS (화이트리스트)                             │
│  allowed_tools 목록 → CLI --allowedTools        │
│  ❌ 빠뜨린 도구 = 에이전트 무력화                  │
├─────────────────────────────────────────────────┤
│  TO-BE (블랙리스트)                               │
│  denied_tools 목록 → CLI --disallowedTools      │
│  ✅ 위험한 것만 차단, 나머지 자유                   │
└─────────────────────────────────────────────────┘
```

---

## Design

### 1. Config Format

```yaml
# config.yaml
permission-strategy: blacklist   # 'whitelist' | 'blacklist' (default: whitelist)

# blacklist mode에서만 사용. 기본값 제공됨 (DEFAULT_DENIED_TOOLS).
# 추가 차단이 필요하면 여기에 append.
denied-tools:
  - "Bash(docker:*)"        # 프로젝트별 추가 차단 예시
```

### 2. Default Deny List

기본 블랙리스트 (코드에 하드코딩, config `denied-tools`로 확장 가능):

#### Pattern Syntax (verified via CLI testing)

```
Bash(command_prefix:glob_pattern)
      ^^^^^^^^^^^^^^ ^^^^^^^^^^^^^
      콜론 앞: 명령어 접두어    콜론 뒤: 나머지 인자 glob
      (공백 포함 가능)
```

**검증 결과:**

| Pattern | Test Command | Blocked? |
|---------|-------------|----------|
| `Bash(git:commit)` | `git commit --allow-empty -m test` | **NO** — 콜론 뒤가 전체 인자와 매칭 안됨 |
| `Bash(git commit:*)` | `git commit --allow-empty -m test` | **YES** |
| `Bash(git commit*)` | `git commit --allow-empty -m test` | **NO** — 콜론 필수 |
| `Bash(rm:*)` | `rm test.txt` | **YES** — 첫 토큰 + 와일드카드 |
| `Bash(rm:*)` | `rm -rf test.txt` | **YES** — `rm`으로 시작하는 모든 것 |
| `Bash(rm *)` | `rm test.txt` | **NO** — 콜론 없으면 안됨 |
| `Bash(curl:*)` | `curl -s https://...` | **YES** |
| 복수 deny 조합 | `git status` 허용 + `git commit`/`git push` 차단 | **YES** — 선택적 차단 |

**핵심 규칙:**
- 콜론(`:`)이 필수. 없으면 패턴 매칭 안됨.
- 2-word 명령 (`git commit`)은 콜론 앞에 공백 포함해서 `Bash(git commit:*)` 로 써야 함.
- 1-word 명령 (`rm`, `curl`)은 `Bash(rm:*)` 로 충분 — `rm -rf` 등 모든 변형 차단.

```python
# Provider-specific deny patterns — CLI가 물리적으로 차단
# Pattern: Bash(command_prefix:arg_glob)
# 콜론 앞 = 명령어 prefix (공백 포함 가능), 콜론 뒤 = 인자 glob

_CLAUDE_DEFAULT_DENIED = [
    # Git write operations — pipeline manages git state
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git checkout:*)",
    "Bash(git reset:*)",
    "Bash(git rebase:*)",
    "Bash(git merge:*)",
    "Bash(git stash:*)",
    "Bash(git branch -d:*)",
    "Bash(git branch -D:*)",
    "Bash(git tag:*)",
    # Destructive file operations
    "Bash(rm:*)",
    "Bash(rmdir:*)",
    # Network — no exfiltration
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(rsync:*)",
    # Package publishing
    "Bash(npm publish:*)",
    "Bash(twine:*)",
    "Bash(cargo publish:*)",
    "Bash(./gradlew publish:*)",
    "Bash(mvn deploy:*)",
    # Process/system
    "Bash(kill:*)",
    "Bash(killall:*)",
    "Bash(shutdown:*)",
    "Bash(reboot:*)",
    # Permission escalation
    "Bash(sudo:*)",
    "Bash(chmod:*)",
    "Bash(chown:*)",
]

_COPILOT_DEFAULT_DENIED = [
    # Copilot --deny-tool syntax: shell(command_prefix) — 콜론/glob 불필요
    # Verified: shell(git commit)이 git commit --allow-empty -m x 차단 확인
    "shell(git commit)",
    "shell(git push)",
    "shell(git checkout)",
    "shell(git reset)",
    "shell(git rebase)",
    "shell(git merge)",
    "shell(git stash)",
    "shell(git branch -d)",
    "shell(git branch -D)",
    "shell(git tag)",
    "shell(rm)",
    "shell(rmdir)",
    "shell(curl)",
    "shell(wget)",
    "shell(ssh)",
    "shell(scp)",
    "shell(rsync)",
    "shell(npm publish)",
    "shell(twine)",
    "shell(cargo publish)",
    "shell(kill)",
    "shell(killall)",
    "shell(shutdown)",
    "shell(reboot)",
    "shell(sudo)",
    "shell(chmod)",
    "shell(chown)",
]
```

#### Claude vs Copilot 패턴 문법 비교 (테스트 확인 완료)

| | Claude CLI | Copilot CLI |
|---|---|---|
| **플래그** | `--disallowedTools` | `--deny-tool` |
| **git commit 차단** | `Bash(git commit:*)` — 콜론+glob 필수 | `shell(git commit)` — prefix만으로 충분 |
| **rm 차단** | `Bash(rm:*)` | `shell(rm)` |
| **콜론 필수?** | **YES** | **NO** |
| **선택적 차단** | `git status` 허용 + `git commit` 차단 | 동일 |
| **테스트 결과** | `permission_denials`에 기록 | `error.code: "denied"` 반환 |

### 3. Runner 변경

```python
# claude.py
class ClaudeCodeRunner:
    def __init__(self, *, allowed_tools=None, denied_tools=None, ...):
        self._allowed_tools = allowed_tools or []
        self._denied_tools = denied_tools or []

    def run(self, prompt, ...):
        cmd = ["claude", "-p", prompt, ...]

        # 둘 다 동시 사용 가능 — deny가 allow보다 우선 (CLI 동작 확인 완료)
        if self._allowed_tools:
            cmd.extend(["--allowedTools", *self._allowed_tools])
        if self._denied_tools:
            cmd.extend(["--disallowedTools", *self._denied_tools])
```

```python
# copilot.py
class GitHubCopilotRunner:
    def run(self, prompt, ...):
        cmd = ["copilot", "--prompt", prompt, ...]

        # 둘 다 동시 사용 가능 — deny가 allow보다 우선
        for tool in self._allowed_tools:
            cmd.append(f"--allow-tool={tool}")
        for tool in self._denied_tools:
            cmd.append(f"--deny-tool={tool}")
```

**검증 결과: `deny > allow` 우선순위.**
`--allowedTools Bash` + `--disallowedTools "Bash(rm:*)"` → `ls` 허용, `rm` 차단.
양쪽 CLI 모두 동일 동작 확인.

### 4. Pipeline 연결

```python
# pipeline.py — runner 생성 시
# denied_tools는 항상 전달 (blacklist 모드가 아니어도 기본 안전장치로 작동)
runner = runner_for(
    wcfg.cli_provider,
    allowed_tools=agent_cfg.agent_a.allowed_tools,   # whitelist: 기존처럼 | blacklist: []
    denied_tools=agent_cfg.agent_a.denied_tools,     # 항상 적용 — deny > allow 우선
    ...
)
```

**하이브리드 전략:**

| permission-strategy | allowed_tools | denied_tools | 효과 |
|---------------------|--------------|-------------|------|
| `whitelist` (기존) | `[Read, Edit, Bash(git:*), ...]` | `[]` (없음) | 현재와 동일 |
| `blacklist` (신규) | `[]` (없음 → CLI에 안 넘김 → 전체 허용) | `[Bash(git commit:*), Bash(rm:*), ...]` | 전체 허용 + 위험한 것만 차단 |
| `hybrid` (가능) | `[Read, Edit, Bash, Grep, Glob]` | `[Bash(git commit:*), Bash(rm:*), ...]` | 넓게 열고 + 위험한 것만 차단 |

`hybrid`는 별도 모드 없이 `blacklist` + `allowed_tools` 동시 지정으로 자동 달성.

**Copilot `--allow-all-tools` fallback:**
Copilot CLI에서 `--allow-tool`도 `--deny-tool`도 없으면 `--allow-all-tools`를 전달.
`denied-tools: []`인 blacklist 모드에서 이 fallback이 발동해 완전 개방됨 → pipeline에서 경고 로그 출력.

**`_AGENT_A_SYSTEM` 프롬프트의 git 경고 유지 (Defense in Depth):**
blacklist 모드에서 CLI가 `git commit`, `git push`를 물리적으로 차단하므로 프롬프트의
"Do NOT run git commit" 메시지는 중복이다. 그러나 의도적으로 유지:
- Layer 1 (CLI deny)이 깨지는 경우의 보험
- 모델이 시도 → 실패 → 재시도 루프에 빠지는 것보다 사전에 안 하는 게 효율적
- 비용 제로 (프롬프트 한 줄)이므로 제거 이유가 없음

### 5. Post-Execution Audit (Safety Net)

CLI 블랙리스트가 1차 방어. 사후 감사는 2차 방어 (CLI deny 우회 대비):

**우회 가능 시나리오:**

| 우회 방법 | 예시 | CLI deny로 차단? | Audit로 감지? |
|-----------|------|:---:|:---:|
| 직접 실행 | `rm test.txt` | YES | YES (파일 삭제) |
| 간접 실행 | `python -c "import os; os.remove(...)"` | NO | YES (파일 삭제) |
| 파이프 | `find . -exec rm {} \;` | NO (`find` 허용) | YES (파일 삭제) |
| git alias | `.gitconfig` alias → `git ci` | 미검증 | YES (commit hash 변경) |
| 경로 우회 | `/usr/bin/rm` 직접 호출 | YES (CLI가 명령 파싱) | YES (파일 삭제) |

**결론: CLI deny가 직접 실행을 막고, Audit가 간접 우회를 잡는 상호 보완 구조.**

Phase 1 이후, Phase 2 (Quality Gates) 전에 실행:

```python
def audit_agent_actions(before_commit_hash, before_snapshot, logger):
    """Phase 1 이후 에이전트가 금지된 행위를 했는지 확인.

    pipeline.py:324에서 이미 snapshot_working_tree()를 호출하므로
    before_snapshot 인프라는 갖춰져 있음.
    """
    violations = []

    # 1. Unauthorized git commits?
    current_hash = _get_head_hash()
    if current_hash != before_commit_hash:
        violations.append(f"Unauthorized commit detected: {current_hash}")
        _revert_to(before_commit_hash)

    # 2. Files unexpectedly deleted?
    #    before_snapshot (from snapshot_working_tree) vs current state
    after_snapshot = snapshot_working_tree()
    deleted_files = _detect_deleted_files(before_snapshot, after_snapshot)
    if deleted_files:
        violations.append(f"Unexpected file deletions: {deleted_files}")
        # git checkout으로 복원 가능한 파일은 복원
        _restore_deleted_files(deleted_files)

    return violations
```

### 6. Agent Role별 적용

| Agent | Blacklist Mode 동작 |
|-------|---------------------|
| **Agent A** | `--disallowedTools` deny list (기본 허용 + 위험한 것만 차단) |
| **Agent R** | 기존 화이트리스트 유지 (`--allowedTools` Read, Grep, Glob, git diff/log) |
| **Agent B** | 기존 화이트리스트 유지 |
| **Agent C** | 기존 화이트리스트 유지 (Read only) |

**블랙리스트는 Agent A에만 적용.** 검증 에이전트(R, B, C)는 기존 화이트리스트 유지:
- Agent R/B/C의 역할은 **읽기 전용 검증**. 코드 수정 권한이 있으면 검증의 독립성이 깨짐.
- 화이트리스트가 적절한 이유: 이 에이전트들이 필요한 도구는 명확하고 고정적 (Read, Grep, Glob, git diff/log).
- "왜 R도 블랙리스트 안 쓰지?" → 검증자가 코드를 고칠 수 있으면 검증자가 아니라 또 다른 구현자가 됨.

---

## Implementation Plan

### Phase 1: Core

1. `domain.py` — `_CLAUDE_DEFAULT_DENIED`, `_COPILOT_DEFAULT_DENIED` 상수 추가
2. `domain.py` — `AgentPermissions`에 `denied_tools: list[str]` 필드 추가
3. `domain.py` — `agent_config_for()` 함수에 `permission_strategy` 파라미터 추가.
   `blacklist`일 때 Agent A에 `denied_tools=DEFAULT_DENIED`, `allowed_tools=[]` 설정.
   Agent R/B/C는 기존 화이트리스트 유지.
4. Runner 변경 — `claude.py`, `copilot.py`에 `denied_tools` 파라미터 + CLI 플래그 연결.
   **`if/if` (독립)**: `--allowedTools`와 `--disallowedTools`를 동시 전달 가능 (deny > allow 우선순위).
   `elif` 아님 — hybrid 모드를 위해 양쪽 동시 사용 지원.
5. `config.py` — `WorkflowConfig`에 `permission_strategy`, `denied_tools` 필드 추가.
   `denied-tools: []` (빈 리스트)일 때 경고 로그 출력: "All safety restrictions removed."
6. `pipeline.py` — `permission_strategy == "blacklist"`일 때 Agent A runner에 `denied_tools` 전달

### Phase 2: Post-Execution Audit

7. `audit.py` — Phase 1 이후 git commit hash 비교, unauthorized commit revert
8. `pipeline.py` — Phase 1 → Audit → Phase 2 순서

### Phase 3: Config & Init

9. `config.yaml` 파싱에서 `permission-strategy`, `denied-tools` 지원
10. `anw init`에서 `permission-strategy: blacklist` 기본 생성
11. 기존 프로젝트 마이그레이션 안내

---

## Defense in Depth

```
Layer 1: CLI --disallowedTools / --deny-tool    ← 하드 블록 (물리적 차단)
Layer 2: Post-execution audit                   ← commit 감지/revert + 파일 삭제 감지
         + sensitive file guard                 ← .env, .git/config 등 수정 감지
Layer 3: Git repo 자체                           ← 최악의 경우 git checkout . 로 전체 복원
```

프롬프트 기반 "제발 하지마"가 아닌, CLI 레벨 하드 블록이 1차 방어.

---

## Migration & Backward Compatibility

- 기본값은 `whitelist` → 기존 프로젝트 영향 없음
- `config.yaml`에 `permission-strategy: blacklist` 한 줄 추가로 전환
- 블랙리스트 모드에서는 `agents.agent_a.allowed_tools` 무시 (경고 로그 출력)
- Agent R/B/C는 항상 화이트리스트 (변경 없음)
- `denied-tools` 미지정 시 `DEFAULT_DENIED` 사용

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| CLI deny 직접 우회 (alias, 경로) | Post-execution audit가 잡음 (commit hash, 파일 삭제 감지) |
| 간접 실행 (`python -c "os.remove()"`) | CLI deny로 못 막지만 audit에서 파일 삭제 감지 |
| 새 CLI 버전에서 deny 플래그 동작 변경 | 버전 체크 + 사후 감사 |
| `denied-tools: []` (빈 리스트) | 경고 로그: "All safety restrictions removed" + audit는 여전히 동작 |
| Codex/Cursor에 deny 플래그 없을 때 | pipeline.py에서 경고 로그 출력 + audit는 동작. `denied_tools` kwarg는 `**_kwargs`로 무시됨 |

가장 중요한 안전장치: **ANW는 항상 git repo 안에서 실행** → 최악의 경우에도 `git checkout .`로 모든 변경을 되돌릴 수 있음.
