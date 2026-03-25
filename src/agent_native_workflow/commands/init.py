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


def cmd_init(args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.domain import agent_config_for

    config_dir = Path(".agent-native-workflow")
    config_dir.mkdir(exist_ok=True)

    cli_provider = getattr(args, "cli", None) or "claude"
    cli_explicitly_set = getattr(args, "cli", None) is not None

    prompt_file = config_dir / "PROMPT.yaml"
    requirements_file = config_dir / "requirements.md"
    agent_config_file = config_dir / "agent-config.yaml"
    workflow_config_file = config_dir / "config.yaml"

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

    # Provider config files — regenerate/update when --cli is explicitly given.
    if not agent_config_file.exists():
        agent_config_for(project_type, cli_provider=cli_provider).save(agent_config_file)
        print(f"Created {agent_config_file} (project type: {project_type})")
    elif cli_explicitly_set:
        agent_config_for(project_type, cli_provider=cli_provider).save(agent_config_file)
        print(f"Updated {agent_config_file} (provider: {cli_provider})")
    else:
        print(f"Skipped {agent_config_file} (already exists)")

    if not workflow_config_file.exists():
        lint_hint = (
            f"lint-cmd: {detected.lint_cmd}" if detected.lint_cmd else "# lint-cmd: make lint"
        )
        test_hint = (
            f"test-cmd: {detected.test_cmd}" if detected.test_cmd else "# test-cmd: make test"
        )
        workflow_config_file.write_text(
            config_yaml(project_type, lint_hint, test_hint, cli_provider)
        )
        print(f"Created {workflow_config_file}")
    elif cli_explicitly_set:
        _update_cli_provider(workflow_config_file, cli_provider)
        print(f"Updated {workflow_config_file} (cli-provider → {cli_provider})")
    else:
        print(f"Skipped {workflow_config_file} (already exists)")

    codereview_file = config_dir / "codereview.md"
    if not codereview_file.exists():
        codereview_file.write_text(CODEREVIEW_MD)
        print(f"Created {codereview_file}")
    else:
        print(f"Skipped {codereview_file} (already exists)")

    gitignore = Path(".gitignore")
    agn_entry = ".agent-native-workflow/runs/"
    if gitignore.is_file():
        if agn_entry not in gitignore.read_text():
            with gitignore.open("a") as f:
                f.write(f"\n# agent-native-workflow runtime artifacts\n{agn_entry}\n")
            print(f"Added '{agn_entry}' to .gitignore")
    else:
        gitignore.write_text(f"# agent-native-workflow runtime artifacts\n{agn_entry}\n")
        print(f"Created .gitignore with '{agn_entry}'")

    print()
    print("Next steps:")
    print(f"  1. Edit {prompt_file} — describe what to build")
    print(f"  2. Edit {requirements_file} — list testable requirements")
    print("  3. Set verification in config.yaml (none / review / triangulation)")
    print("  4. Run: agn run --cli <provider>")
    print("     Or: agn run --requirements path/to/ticket.docx")
    print(f"  5. (Optional) Edit {codereview_file} — code conventions for Agent R")
    return 0
