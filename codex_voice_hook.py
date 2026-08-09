#!/usr/bin/env python3
"""Codex lifecycle-hook adapter for the shared voice feedback backend.

Codex sends one JSON object on stdin.  The shared implementation in
``voice_hook.py`` handles playback and the MOSS daemon; this adapter always
returns a JSON object because Codex Stop hooks require JSON on stdout.
"""

import json
import sys

import voice_hook


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        voice_hook.log(f"codex hook: stdin 解析失败 {exc!r}")
        print("{}")
        return

    cfg = voice_hook.load_config()
    if cfg.get("enabled", True):
        voice_hook.handle_payload(payload, cfg)

    # In particular, Stop treats plain text as invalid output.  An empty JSON
    # object means success without steering or continuing the turn.
    print("{}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        voice_hook.log("codex hook 崩了:\n" + traceback.format_exc())
        print("{}")
