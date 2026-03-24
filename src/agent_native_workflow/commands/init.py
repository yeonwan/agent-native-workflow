from __future__ import annotations

import argparse
from pathlib import Path

from agent_native_workflow.commands.init_templates import (
    PROMPT_YAML,
    REQUIREMENTS_MD,
    config_yaml,
)


def cmd_init(args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all
    from agent_native_workflow.domain import agent_config_for

    config_dir = Path(".agent-native-workflow")
    config_dir.mkdir(exist_ok=True)

    prompt_file = config_dir / "PROMPT.yaml"
    requirements_file = config_dir / "requirements.md"
    agent_config_file = config_dir / "agent-config.yaml"
    workflow_config_file = config_dir / "config.yaml"

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

    if not agent_config_file.exists():
        cli_provider = getattr(args, "cli", None) or "claude"
        agent_config_for(project_type, cli_provider=cli_provider).save(agent_config_file)
        print(f"Created {agent_config_file} (project type: {project_type})")
    else:
        print(f"Skipped {agent_config_file} (already exists)")

    if not workflow_config_file.exists():
        lint_hint = (
            f"lint-cmd: {detected.lint_cmd}" if detected.lint_cmd else "# lint-cmd: make lint"
        )
        test_hint = (
            f"test-cmd: {detected.test_cmd}" if detected.test_cmd else "# test-cmd: make test"
        )
        workflow_config_file.write_text(config_yaml(project_type, lint_hint, test_hint))
        print(f"Created {workflow_config_file}")
    else:
        print(f"Skipped {workflow_config_file} (already exists)")

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
    return 0
