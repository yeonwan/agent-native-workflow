"""Entry point: argparse wiring delegates to `commands/`."""

from __future__ import annotations

import sys

from agent_native_workflow.commands import COMMAND_DISPATCH
from agent_native_workflow.commands.parser import build_parser

__all__ = [
    "build_parser",
    "main",
    "_cmd_detect",
    "_cmd_init",
    "_cmd_log",
    "_cmd_providers",
    "_cmd_run",
    "_cmd_status",
    "_cmd_verify",
]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handler = COMMAND_DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args))


# Backward-compatible names for tests / external imports
_cmd_run = COMMAND_DISPATCH["run"]
_cmd_verify = COMMAND_DISPATCH["verify"]
_cmd_status = COMMAND_DISPATCH["status"]
_cmd_detect = COMMAND_DISPATCH["detect"]
_cmd_log = COMMAND_DISPATCH["log"]
_cmd_providers = COMMAND_DISPATCH["providers"]
_cmd_init = COMMAND_DISPATCH["init"]


if __name__ == "__main__":
    main()
