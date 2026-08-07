#!/usr/bin/env python3
"""把语音 hook 注册进 ~/.claude/settings.json，保留已有的 hook。

  python3 install.py            安装
  python3 install.py --uninstall  卸载
"""

import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "voice_hook.py")
# 装到 ~/.claude/hooks/ 下，这样这个 git 仓库被挪走/删掉也不会把全局 hook 弄断
INSTALLED = os.path.join(HOME, ".claude", "hooks", "voice_hook.py")
# 常驻服务同理
DAEMON_SOURCE = os.path.join(HERE, "tts_daemon.py")
DAEMON_INSTALLED = os.path.join(HOME, ".claude", "hooks", "tts_daemon.py")
COMMAND = f"/usr/bin/env python3 {INSTALLED}"

# 老版本直接指向仓库里的脚本，升级时一并清掉
LEGACY_COMMANDS = {f"/usr/bin/env python3 {SOURCE}"}

# 事件 -> 是否需要 matcher 字段
EVENTS = {
    "Stop": False,
    "Notification": False,
    "UserPromptSubmit": False,
}


def load():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS) as f:
        return json.load(f)


def copy_hook():
    os.makedirs(os.path.dirname(INSTALLED), exist_ok=True)
    for src, dst in ((SOURCE, INSTALLED), (DAEMON_SOURCE, DAEMON_INSTALLED)):
        if os.path.exists(src):
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)


def strip_commands(settings, targets):
    """从所有事件里摘掉这些 command，返回受影响的事件名。"""
    hooks = settings.get("hooks", {})
    removed = []
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            inner = entry.get("hooks", [])
            pruned = [h for h in inner if h.get("command") not in targets]
            if len(pruned) != len(inner):
                removed.append(event)
            if pruned or not inner:
                entry["hooks"] = pruned
                kept.append(entry)
        hooks[event] = kept
    return removed


def install(settings):
    copy_hook()
    strip_commands(settings, LEGACY_COMMANDS)   # 清掉指向仓库的老注册
    hooks = settings.setdefault("hooks", {})
    added = []
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        already = any(
            h.get("command") == COMMAND
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if already:
            continue
        entries.append({"hooks": [{"type": "command", "command": COMMAND}]})
        added.append(event)
    return added


def uninstall(settings):
    removed = strip_commands(settings, LEGACY_COMMANDS | {COMMAND})
    try:
        os.remove(INSTALLED)
    except OSError:
        pass
    return removed


def main():
    settings = load()
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".bak")

    if "--uninstall" in sys.argv:
        changed = uninstall(settings)
        verb = "移除"
    else:
        changed = install(settings)
        verb = "注册"

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if changed:
        print(f"已{verb}: {', '.join(sorted(set(changed)))}")
    else:
        print("没有变化（可能已经装过了）")
    print(f"备份: {SETTINGS}.bak")


if __name__ == "__main__":
    main()
