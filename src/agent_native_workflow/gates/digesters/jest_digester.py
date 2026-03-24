from __future__ import annotations

import json


class JestDigester:
    """Parse Jest / Vitest JSON output or fall back to text heuristics."""

    def digest(self, raw_output: str, exit_code: int) -> str:
        if exit_code == 0:
            return ""

        stripped = raw_output.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                failures: list[str] = []
                for suite in data.get("testResults", []):
                    for test in suite.get("assertionResults", []):
                        if test.get("status") == "failed":
                            name = test.get("fullName", test.get("title", "?"))
                            msgs = test.get("failureMessages", []) or test.get("message", "")
                            if isinstance(msgs, str):
                                msg = msgs[:300]
                            else:
                                msg = "\n".join(msgs[:2])[:300]
                            failures.append(f"FAIL: {name}\n{msg}")
                if failures:
                    return "\n\n".join(failures[:10])[:3000]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        lines = raw_output.splitlines()
        failure_blocks: list[str] = []
        current_block: list[str] = []
        in_failure = False

        for line in lines:
            if line.strip().startswith("●"):
                if current_block:
                    failure_blocks.append("\n".join(current_block))
                current_block = [line]
                in_failure = True
            elif in_failure:
                if line.strip() == "" and len(current_block) > 3:
                    failure_blocks.append("\n".join(current_block))
                    current_block = []
                    in_failure = False
                else:
                    current_block.append(line)

        if current_block:
            failure_blocks.append("\n".join(current_block))

        if failure_blocks:
            return "\n\n".join(failure_blocks[:10])[:3000]

        return raw_output[-2000:]
