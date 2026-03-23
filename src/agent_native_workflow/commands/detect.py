from __future__ import annotations

import argparse


def cmd_detect(_args: argparse.Namespace) -> int:
    from agent_native_workflow.detect import detect_all

    cfg = detect_all()
    print(cfg.print_config())
    return 0
