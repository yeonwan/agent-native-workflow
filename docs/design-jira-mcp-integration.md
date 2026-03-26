# Jira + MCP Integration Design

> Status: Draft (2026-03-26)

## Problem

현재 파이프라인은 **파일 기반** requirements에 의존한다:

```
--requirements requirements.md | spec.docx | spec.pdf | spec.doc
```

실무에서는 Jira 티켓이 requirements 소스인 경우가 대부분이다.
Agent A가 MCP(Model Context Protocol)를 통해 Jira에서 직접 requirements를 가져오는 방법을 설계한다.

---

## Core Challenges

| # | 문제 | 설명 |
|---|------|------|
| 1 | Requirements 획득 | Jira ticket → text 변환 필요. 본문, acceptance criteria, 첨부, 하위 이슈 등 구조 복잡 |
| 2 | 일관성 | Agent A, R, B, C 모두 **동일한** requirements를 봐야 함. 실시간 읽기 시 중간에 티켓 수정 가능 |
| 3 | MCP 의존성 | MCP server 설정은 CLI 레벨 (`.claude/mcp_servers.json` 등) — 파이프라인이 직접 제어 불가 |

---

## Approach: Phase 0 — Requirements Fetch

**"Fetch → Snapshot → Pipeline as-is"** 전략:

```
┌─────────────────────────────────────────────────────┐
│  Phase 0: Requirements Fetch (NEW)                  │
│                                                     │
│  Input:  --requirements PROJ-123                    │
│          (Jira ticket ID — regex auto-detect)       │
│                                                     │
│  Action: Lightweight agent call with MCP access     │
│          OR direct Jira REST API call               │
│                                                     │
│  Output: .agent-native-workflow/runs/run-XXX/       │
│          └── requirements-snapshot.md               │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
          Pipeline runs exactly as today
          (agents_requirements_file = snapshot)
```

장점:
- 기존 파이프라인 변경 최소화 — snapshot 만들면 나머지 동일
- 일관성 보장 — 한 번 fetch 후 snapshot 고정
- CLI 독립적 — Claude MCP, Copilot MCP, REST API 모두 교체 가능

---

## Detailed Design

### 1. Requirements Source Abstraction

```python
# requirements_source.py (NEW)

class RequirementsSource(Protocol):
    """Pluggable requirements source."""
    def fetch(self, identifier: str, store: RunStore, logger: Logger) -> Path:
        """Fetch requirements and return path to readable .md file."""
        ...

class FileSource:
    """Current behavior — read from local file."""
    def fetch(self, identifier: str, store: RunStore, logger: Logger) -> Path:
        path = Path(identifier)
        if not is_text_format(path):
            text = load_requirements(path)
            return store.write_requirements_snapshot(text)
        return path

class JiraMcpSource:
    """Fetch from Jira via MCP-enabled agent."""
    def __init__(self, runner: AgentRunner, mcp_config: dict): ...
    def fetch(self, identifier: str, store: RunStore, logger: Logger) -> Path:
        # 1. Run lightweight agent with MCP tools
        # 2. Agent writes requirements-snapshot.md
        # 3. Return path
        ...

class JiraRestSource:
    """Fetch from Jira via REST API (no MCP needed)."""
    def __init__(self, base_url: str, api_token: str): ...
    def fetch(self, identifier: str, store: RunStore, logger: Logger) -> Path:
        # Direct API call → markdown → snapshot
        ...
```

### 2. Jira Ticket ID Auto-Detection

```python
# requirements_loader.py

import re

_JIRA_TICKET_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

def is_jira_ticket(identifier: str) -> bool:
    return bool(_JIRA_TICKET_PATTERN.match(identifier))
```

```bash
# 사용법 — 기존 --requirements 플래그 재사용
anw run --requirements PROJ-123              # Jira 단일 티켓
anw run --requirements PROJ-123,PROJ-124     # 여러 티켓
anw run --requirements requirements.md       # 기존 파일 (변경 없음)
```

### 3. MCP Fetcher Agent Prompt

Phase 0에서 실행하는 경량 agent의 프롬프트:

```markdown
You are a requirements extraction agent. Your ONLY job is to read a Jira
ticket and produce a structured requirements document.

## Task
Read Jira ticket `{ticket_id}` using the Jira MCP tools available to you.

## Extract
1. **Summary** — ticket title
2. **Description** — full description body
3. **Acceptance Criteria** — if present (description or custom field)
4. **Sub-tasks** — each with summary and status
5. **Labels / Components** — for context
6. **Linked Issues** — blocking/blocked-by relationships
7. **Attachments** — list filenames only (no binary download)

## Output Format
Write to `{output_path}` in this exact markdown format:

# {ticket_id}: {summary}

## Description
{description}

## Acceptance Criteria
- [ ] {criterion_1}
- [ ] {criterion_2}

## Sub-tasks
| Ticket | Summary | Status |
|--------|---------|--------|
| SUB-1  | ...     | Done   |

## Context
- Labels: ...
- Components: ...
- Linked: PROJ-999 (blocks)

## Rules
- Write ONLY to `{output_path}`. Do not modify any other file.
- Extract acceptance criteria separately even if embedded in description.
- If no explicit acceptance criteria, note it and infer testable requirements.
- Preserve original wording — do not paraphrase.
```

### 4. Config Extension

```yaml
# .agent-native-workflow/config.yaml

requirements-source: auto          # auto | file | jira-mcp | jira-rest

# Jira REST API (no MCP needed)
jira-base-url: https://company.atlassian.net
jira-api-token-env: JIRA_API_TOKEN   # env var name (never store token directly)

# MCP — informational (actual config lives in CLI settings)
mcp-servers:
  - name: jira
    required-for: [jira-mcp]
```

`requirements-source: auto` 동작:
1. `--requirements` 값이 `[A-Z]+-\d+` 패턴 → `jira-mcp` 시도 → `jira-rest` fallback
2. 파일 확장자 존재 → `file` (현재 동작 그대로)

### 5. Pipeline Change — Minimal

```python
# pipeline.py — Phase 0 삽입 (~line 210)

if is_jira_ticket(str(requirements_file)):
    # Phase 0: Fetch requirements from Jira
    source = _resolve_source(wcfg, runner)
    agents_requirements_file = source.fetch(
        str(requirements_file), store, logger
    )
elif not is_text_format(requirements_file):
    requirements_text = load_requirements(requirements_file)
    agents_requirements_file = store.write_requirements_snapshot(requirements_text)
else:
    agents_requirements_file = requirements_file
```

기존 코드에 **6줄 추가**로 완료.

### 6. JiraRestSource — MCP-Free Fallback

MCP 없는 환경을 위한 REST API 직접 호출:

```python
class JiraRestSource:
    def fetch(self, ticket_id: str, store: RunStore, logger: Logger) -> Path:
        import urllib.request
        import json

        url = f"{self.base_url}/rest/api/3/issue/{ticket_id}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Basic {self._auth}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        md = self._to_markdown(ticket_id, data)
        return store.write_requirements_snapshot(md)
```

외부 의존성 없음 (`urllib` 표준 라이브러리만 사용).

### 7. MCP Server Setup (User Side)

파이프라인은 MCP server를 직접 관리하지 않음. CLI 레벨 설정:

**Claude Code:**
```json
// .claude/mcp_servers.json
{
  "jira": {
    "command": "npx",
    "args": ["-y", "@anthropic/jira-mcp-server"],
    "env": {
      "JIRA_BASE_URL": "https://company.atlassian.net",
      "JIRA_API_TOKEN": "${JIRA_API_TOKEN}"
    }
  }
}
```

**Copilot:** VS Code settings.json에서 MCP server 설정.

`anw init --jira`가 이 설정을 scaffold할 수 있음 (파일 생성만, 토큰은 사용자 설정).

---

## End-to-End Flow

```
User: anw run --requirements PROJ-123

1. CLI parses "PROJ-123" → is_jira_ticket() = True
2. Phase 0: Requirements Fetch
   ├─ Try jira-mcp: fetcher agent + MCP → snapshot.md
   ├─ Fallback jira-rest: REST API → snapshot.md
   └─ agents_requirements_file = snapshot.md
3. Phase 1: Agent A reads snapshot.md, implements
4. Phase 2: Quality gates (lint, test, security)
5. Phase 3: Agent R reads snapshot.md, verifies
6. Converge or iterate
```

---

## Challenges & Trade-offs

| 항목 | 챌린지 | 대응 |
|------|--------|------|
| MCP 가용성 | 사용자마다 설정 다름 | `auto`: MCP → REST fallback → 명확한 에러 |
| Jira 구조 다양성 | 팀마다 custom field 다름 | Fetcher prompt "있으면 추출" 방식. REST는 common fields만 |
| 첨부파일 | 이미지, PDF | 텍스트 첨부만 다운로드. 이미지는 파일명만 기록 |
| 여러 티켓 | Epic → Story | `PROJ-123,PROJ-124` 또는 JQL 지원 (확장) |
| 티켓 변경 | 파이프라인 중 수정 | Snapshot 고정 — 의도적 설계 |
| 보안 | API 토큰 | env var 참조만. config에 직접 저장 안 함 |

---

## Implementation Priority

1. **Phase A**: `JiraRestSource` — MCP 없이 동작, 즉시 테스트 가능
2. **Phase B**: `JiraMcpSource` — Fetcher agent prompt + MCP 연동
3. **Phase C**: `anw init --jira` — MCP server config scaffold
4. **Phase D**: 여러 티켓 / JQL 지원
