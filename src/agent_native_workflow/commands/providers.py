from __future__ import annotations

import argparse


def cmd_providers(_args: argparse.Namespace) -> int:
    from agent_native_workflow.runners.factory import available_providers

    providers = available_providers()
    print(f"{'Provider':<12} {'CLI Command':<10} {'File Tools':<12} {'Status'}")
    print("-" * 60)
    for p in providers:
        experimental_tag = " [experimental]" if p["experimental"] else ""
        file_tools = "Yes" if p["file_tools"] else "No"
        print(
            f"{p['provider']:<12} {p['cli_cmd']:<10} {file_tools:<12} "
            f"{p['status']}{experimental_tag}"
        )
    return 0
