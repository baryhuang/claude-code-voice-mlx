#!/usr/bin/env python3
"""Install the voice feedback lifecycle hooks for Codex.

This keeps the existing Claude Code voice backend (model, reference audio,
daemon, queue, and notch UI) and only adds the small Codex adapter.

    python3 install.py codex
    python3 install.py codex --uninstall
"""

import json
import os
import shlex
import shutil
import sys


HOME = os.path.expanduser("~")
CODEX_DIR = os.path.join(HOME, ".codex")
HOOKS_PATH = os.path.join(CODEX_DIR, "hooks.json")
HOOK_DIR = os.path.join(CODEX_DIR, "hooks")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_SOURCE_DIR = os.path.join(ROOT, "src", "hooks")
FILES = ("voice_hook.py", "codex_voice_hook.py")
COMMAND = ("/usr/bin/env python3 "
           + shlex.quote(os.path.join(HOOK_DIR, "codex_voice_hook.py")))
EVENTS = ("UserPromptSubmit", "PermissionRequest", "Stop")


def load_hooks():
    if not os.path.exists(HOOKS_PATH):
        return {"description": "User-level Codex lifecycle hooks.", "hooks": {}}
    with open(HOOKS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{HOOKS_PATH} 顶层必须是 JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{HOOKS_PATH} 的 hooks 字段必须是 JSON object")
    return data


def is_our_handler(handler):
    return isinstance(handler, dict) and handler.get("command") == COMMAND


def strip_command(data):
    removed = []
    hooks = data.get("hooks", {})
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept = [handler for handler in handlers if not is_our_handler(handler)]
            if len(kept) != len(handlers):
                removed.append(event)
            if kept:
                updated = dict(group)
                updated["hooks"] = kept
                kept_groups.append(updated)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return removed


def copy_scripts():
    os.makedirs(HOOK_DIR, exist_ok=True)
    for name in FILES:
        src = os.path.join(HOOK_SOURCE_DIR, name)
        dst = os.path.join(HOOK_DIR, name)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)


def install(data):
    copy_scripts()
    strip_command(data)
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        hooks.setdefault(event, []).append({
            "hooks": [{
                "type": "command",
                "command": COMMAND,
                "timeout": 5,
            }]
        })
    return list(EVENTS)


def uninstall(data):
    removed = strip_command(data)
    for name in FILES:
        try:
            os.remove(os.path.join(HOOK_DIR, name))
        except OSError:
            pass
    return removed


def save(data):
    os.makedirs(CODEX_DIR, exist_ok=True)
    if os.path.exists(HOOKS_PATH):
        shutil.copy2(HOOKS_PATH, HOOKS_PATH + ".bak")
    with open(HOOKS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    data = load_hooks()
    uninstalling = "--uninstall" in args
    changed = uninstall(data) if uninstalling else install(data)
    save(data)

    verb = "移除" if uninstalling else "注册"
    print(f"已{verb}: {', '.join(changed) if changed else '没有匹配项'}")
    print(f"配置: {HOOKS_PATH}")
    if os.path.exists(HOOKS_PATH + ".bak"):
        print(f"备份: {HOOKS_PATH}.bak")

    if not uninstalling:
        backend = os.path.join(HOME, ".claude", "hooks", "tts_daemon.py")
        if not os.path.exists(backend):
            print("提示: 没找到现有语音后端；先运行 python3 install.py claude，"
                  "否则 Codex 会降级使用 macOS say。")
        print("下一步: 在 Codex CLI 输入 /hooks，审核并信任这三个 hook。")


if __name__ == "__main__":
    main()
