#!/usr/bin/env python3
"""把语音 hook 注册进 ~/.claude/settings.json，保留已有的 hook。

  python3 install.py claude              安装
  python3 install.py claude --uninstall  卸载
"""

import json
import os
import shutil
import subprocess
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
# 菜单栏图标 + 刘海状态条（可选，需要 Xcode 的 swiftc）
NOTCH_SOURCE = os.path.join(ROOT, "src", "macos", "VoiceNotch.swift")
NOTCH_BIN = os.path.join(HOME, ".claude", "hooks", "voice-notch")
# 登录项：静音开关在菜单栏图标上，图标必须先于守护进程存在。
# 靠守护进程拉起就成了死结——静音时 hook 不说话、守护进程不启动、
# 图标不出现，想取消静音只能去改 JSON。
AGENT_LABEL = "com.claude.voice-notch"
AGENT_PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{AGENT_LABEL}.plist")
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


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{binary}</string></array>
  <key>RunAtLoad</key><true/>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"""


def build_notch():
    """有 swiftc 就把菜单栏图标编译出来；没有就跳过，纯语音照常工作。"""
    if not os.path.exists(NOTCH_SOURCE):
        return
    if not shutil.which("swiftc"):
        print("提示: 没找到 swiftc，跳过菜单栏图标（装 Xcode 后重跑 install.py）")
        return
    src_m = os.path.getmtime(NOTCH_SOURCE)
    if os.path.exists(NOTCH_BIN) and os.path.getmtime(NOTCH_BIN) >= src_m:
        return                                      # 没改过源码，不用重编
    print("编译菜单栏图标 …")
    r = subprocess.run(["swiftc", "-O", NOTCH_SOURCE, "-o", NOTCH_BIN],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"已生成 {NOTCH_BIN}")
    else:
        print(f"编译失败（不影响语音）:\n{r.stderr[:800]}")


def install_agent():
    """把菜单栏图标注册成登录项，并立刻启动它。"""
    if not os.path.exists(NOTCH_BIN):
        return False
    os.makedirs(os.path.dirname(AGENT_PLIST), exist_ok=True)
    with open(AGENT_PLIST, "w") as f:
        f.write(PLIST_TEMPLATE.format(label=AGENT_LABEL, binary=NOTCH_BIN))
    unload_agent()      # 旧的先停掉，否则新旧两个图标并排挂在菜单栏上
    uid = os.getuid()
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", AGENT_PLIST],
                       capture_output=True, text=True)
    if r.returncode != 0:       # 老系统没有 bootstrap 子命令
        r = subprocess.run(["launchctl", "load", "-w", AGENT_PLIST],
                           capture_output=True, text=True)
    if r.returncode == 0:
        print("菜单栏图标已注册为登录项（点它可以静音）")
        return True
    print(f"登录项注册失败（不影响语音）: {r.stderr.strip()[:200]}")
    return False


def unload_agent():
    """停掉登录项和当前跑着的图标进程。"""
    uid = os.getuid()
    if os.path.exists(AGENT_PLIST):
        r = subprocess.run(["launchctl", "bootout", f"gui/{uid}/{AGENT_LABEL}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["launchctl", "unload", "-w", AGENT_PLIST],
                           capture_output=True, text=True)
    # 守护进程也会拉起图标，那一份不归 launchd 管，单独收拾
    subprocess.run(["pkill", "-x", "voice-notch"], capture_output=True)


def uninstall_agent():
    unload_agent()
    try:
        os.remove(AGENT_PLIST)
    except OSError:
        pass


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
    install_agent()
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
    uninstall_agent()
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
