"""CLI subcommand implementations (one module per command)."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from agent_native_workflow.commands.clean import cmd_clean
from agent_native_workflow.commands.detect import cmd_detect
from agent_native_workflow.commands.export import cmd_export
from agent_native_workflow.commands.init import cmd_init
from agent_native_workflow.commands.log import cmd_log
from agent_native_workflow.commands.providers import cmd_providers
from agent_native_workflow.commands.run import cmd_run
from agent_native_workflow.commands.status import cmd_status
from agent_native_workflow.commands.verify import cmd_verify

CommandHandler = Callable[[argparse.Namespace], int]

COMMAND_DISPATCH: dict[str, CommandHandler] = {
    "run": cmd_run,
    "verify": cmd_verify,
    "detect": cmd_detect,
    "providers": cmd_providers,
    "init": cmd_init,
    "status": cmd_status,
    "clean": cmd_clean,
    "log": cmd_log,
    "export": cmd_export,
}

__all__ = [
    "COMMAND_DISPATCH",
    "cmd_clean",
    "cmd_detect",
    "cmd_export",
    "cmd_init",
    "cmd_log",
    "cmd_providers",
    "cmd_run",
    "cmd_status",
    "cmd_verify",
]
