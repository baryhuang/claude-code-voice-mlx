#!/usr/bin/env python3
"""Unified installer for Claude Code and Codex voice feedback.

Examples:
    python3 install.py                 # install both clients
    python3 install.py claude          # Claude Code only
    python3 install.py codex           # Codex only (shared backend required)
    python3 install.py --uninstall     # uninstall both clients
"""

import argparse

from installers import install_claude, install_codex


def parse_args():
    parser = argparse.ArgumentParser(
        description="Install voice feedback for Claude Code and/or Codex.",
        epilog=(
            "examples:\n"
            "  python3 install.py                 install both clients\n"
            "  python3 install.py claude          install Claude Code only\n"
            "  python3 install.py codex           install Codex only\n"
            "  python3 install.py --uninstall     uninstall both clients"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "client",
        nargs="?",
        choices=("all", "claude", "codex"),
        default="all",
        help="client to configure (default: all)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the selected client hooks",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    action = ["--uninstall"] if args.uninstall else []

    targets = ("claude", "codex") if args.client == "all" else (args.client,)
    # Remove the client adapters before the shared backend; install in the
    # opposite order so Codex can immediately reuse the backend.
    if args.uninstall:
        targets = tuple(reversed(targets))

    for target in targets:
        print(f"\n== {'卸载' if args.uninstall else '安装'} {target} ==")
        if target == "claude":
            install_claude.main(action)
        else:
            install_codex.main(action)


if __name__ == "__main__":
    main()
