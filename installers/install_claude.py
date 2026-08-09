#!/usr/bin/env python3
"""把语音 hook 注册进 ~/.claude/settings.json，保留已有的 hook。

  python3 install.py claude              安装
  python3 install.py claude --uninstall  卸载
"""

import json
import os
import shutil
import sys

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "src", "hooks", "voice_hook.py")
# 装到 ~/.claude/hooks/ 下，这样这个 git 仓库被挪走/删掉也不会把全局 hook 弄断
INSTALLED = os.path.join(HOME, ".claude", "hooks", "voice_hook.py")
# 常驻服务同理
DAEMON_SOURCE = os.path.join(ROOT, "src", "daemon", "tts_daemon.py")
DAEMON_INSTALLED = os.path.join(HOME, ".claude", "hooks", "tts_daemon.py")
# 刘海状态条（可选，需要 Xcode 的 swiftc）
NOTCH_SOURCE = os.path.join(ROOT, "src", "macos", "VoiceNotch.swift")
NOTCH_BIN = os.path.join(HOME, ".claude", "hooks", "voice-notch")
COMMAND = f"/usr/bin/env python3 {INSTALLED}"

# 老版本直接指向仓库里的脚本，升级时一并清掉
LEGACY_COMMANDS = {
    f"/usr/bin/env python3 {SOURCE}",
    f"/usr/bin/env python3 {os.path.join(ROOT, 'voice_hook.py')}",
}

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
    build_notch()


def build_notch():
    """有 swiftc 就把刘海状态条编译出来；没有就跳过，纯语音照常工作。"""
    import subprocess
    if not os.path.exists(NOTCH_SOURCE):
        return
    if not shutil.which("swiftc"):
        print("提示: 没找到 swiftc，跳过刘海状态条（装 Xcode 后重跑 install.py）")
        return
    src_m = os.path.getmtime(NOTCH_SOURCE)
    if os.path.exists(NOTCH_BIN) and os.path.getmtime(NOTCH_BIN) >= src_m:
        return                                      # 没改过源码，不用重编
    print("编译刘海状态条 …")
    r = subprocess.run(["swiftc", "-O", NOTCH_SOURCE, "-o", NOTCH_BIN],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"已生成 {NOTCH_BIN}")
    else:
        print(f"编译失败（不影响语音）:\n{r.stderr[:800]}")


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


def main(args=None):
    args = sys.argv[1:] if args is None else args
    settings = load()
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, SETTINGS + ".bak")

    if "--uninstall" in args:
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
