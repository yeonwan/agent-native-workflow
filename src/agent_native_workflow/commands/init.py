from __future__ import annotations

import argparse
import re
from pathlib import Path

from agent_native_workflow.commands.init_templates import (
    CODEREVIEW_MD,
    PROMPT_YAML,
    REQUIREMENTS_MD,
    config_yaml,
)


def _update_cli_provider(config_path: Path, new_provider: str) -> None:
    """Patch cli-provider line in an existing config.yaml, preserving all other settings."""
    text = config_path.read_text()
    patched = re.sub(
        r"^(cli-provider:\s*).*$",
        rf"\g<1>{new_provider}",
        text,
        flags=re.MULTILINE,
    )
    config_path.write_text(patched)


def _upsert_agents_block(config_path: Path, agents_yaml: str) -> None:
    """Replace or append the generated `agents:` block in config.yaml."""
    text = config_path.read_text()
    block = agents_yaml.rstrip() + "\n"
    marker_re = re.compile(r"\n?# BEGIN agents\n.*?# END agents\n?", flags=re.DOTALL)
    if marker_re.search(text):
        updated = marker_re.sub("\n" + block, text).rstrip() + "\n"
    else:
        updated = text.rstrip() + "\n\n" + block
    config_path.write_text(updated)


def cmd_init(args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.domain import agent_config_for

    config_dir = Path(".agent-native-workflow")
    config_dir.mkdir(exist_ok=True)

    cli_provider = getattr(args, "cli", None) or "claude"
    cli_explicitly_set = getattr(args, "cli", None) is not None

    prompt_file = config_dir / "PROMPT.yaml"
    requirements_file = config_dir / "requirements.md"
    workflow_config_file = config_dir / "config.yaml"
    legacy_agent_config_file = config_dir / "agent-config.yaml"

    # Content files — never overwrite; user customises these.
    if not prompt_file.exists():
        prompt_file.write_text(PROMPT_YAML)
        print(f"Created {prompt_file}")
    else:
        print(f"Skipped {prompt_file} (already exists)")

    if not requirements_file.exists():
        requirements_file.write_text(REQUIREMENTS_MD)
        print(f"Created {requirements_file}")
    else:
        print(f"Skipped {requirements_file} (already exists)")

    detected = detect_all()
    project_type = detected.project_type
    embedded_agents_yaml = agent_config_for(
        project_type, cli_provider=cli_provider
    ).to_embedded_yaml()

    if legacy_agent_config_file.exists():
        print(
            "Detected legacy .agent-native-workflow/agent-config.yaml "
            "(still supported; migrate settings into config.yaml > agents when convenient)"
        )

    if not workflow_config_file.exists():
        lint_hint = (
            f"lint-cmd: {detected.lint_cmd}" if detected.lint_cmd else "# lint-cmd: make lint"
        )
        test_hint = (
            f"test-cmd: {detected.test_cmd}" if detected.test_cmd else "# test-cmd: make test"
        )
        workflow_config_file.write_text(
            config_yaml(project_type, lint_hint, test_hint, cli_provider, embedded_agents_yaml)
        )
        print(f"Created {workflow_config_file}")
    elif cli_explicitly_set:
        _update_cli_provider(workflow_config_file, cli_provider)
        _upsert_agents_block(workflow_config_file, embedded_agents_yaml)
        print(f"Updated {workflow_config_file} (cli-provider and agents defaults → {cli_provider})")
    else:
        print(f"Skipped {workflow_config_file} (already exists)")

    codereview_file = config_dir / "codereview.md"
    if not codereview_file.exists():
        codereview_file.write_text(CODEREVIEW_MD)
        print(f"Created {codereview_file}")
    else:
        print(f"Skipped {codereview_file} (already exists)")

    gitignore = Path(".gitignore")
    anw_entry = ".agent-native-workflow/runs/"
    if gitignore.is_file():
        if anw_entry not in gitignore.read_text():
            with gitignore.open("a") as f:
                f.write(f"\n# agent-native-workflow runtime artifacts\n{anw_entry}\n")
            print(f"Added '{anw_entry}' to .gitignore")
    else:
        gitignore.write_text(f"# agent-native-workflow runtime artifacts\n{anw_entry}\n")
        print(f"Created .gitignore with '{anw_entry}'")

    print()
    print("Next steps:")
    print(f"  1. Edit {prompt_file} — describe what to build")
    print(f"  2. Edit {requirements_file} — list testable requirements")
    print("  3. Set verification in config.yaml (none / review / triangulation)")
    print("  4. Run: anw run --cli <provider>")
    print("     Or: anw run --requirements path/to/ticket.docx")
    print(f"  5. (Optional) Edit {codereview_file} — code conventions for Agent R")
    return 0
