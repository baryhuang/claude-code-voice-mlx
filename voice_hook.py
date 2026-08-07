#!/usr/bin/env python3
"""Claude Code 语音反馈 hook。

把 Claude 的最终回复念出来，这样你可以只用语音输入 + 耳朵听，不用盯屏幕。

事件：
  Stop          -> 念出这一轮的最终文字回复
  Notification  -> 念出"需要你批准/在等你输入"
  UserPromptSubmit -> 你一开口就掐掉正在念的内容（--stop）

命令行：
  voice_hook.py --test      试听
  voice_hook.py --toggle    开/关
  voice_hook.py --stop      立刻闭嘴
  voice_hook.py --status    看当前配置

配置：~/.claude/voice_config.json
"""

import json
import os
import re
import signal
import subprocess
import sys
from collections import deque

HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".claude", "voice_config.json")
PID_PATH = os.path.join(HOME, ".claude", ".voice_say.pid")
LOG_PATH = os.path.join(HOME, ".claude", "voice_hook.log")


LOG_MAX_BYTES = 256 * 1024      # 每轮对话都写，必须封顶


def log(msg):
    """诊断日志。hook 是静默运行的，没有日志就只能靠猜。"""
    try:
        import time
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            with open(LOG_PATH) as f:
                keep = f.readlines()[-500:]
            with open(LOG_PATH, "w") as f:
                f.writelines(keep)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except Exception:
        pass

SOCK_PATH = os.path.join(HOME, ".claude", ".voice_tts.sock")
VENV_PY = os.path.join(HOME, ".claude", "voice-tts", "bin", "python")
DAEMON_PY = os.path.join(HOME, ".claude", "hooks", "tts_daemon.py")

DEFAULTS = {
    "enabled": True,
    # say = 系统自带，难听但零依赖；kokoro = MLX 神经网络模型，好听但要常驻进程
    "engine": "kokoro",
    "model": "mlx-community/Kokoro-82M-bf16",
    "kokoro_voice": "zf_xiaobei",
    "speed": 1.0,
    # 注意：say -v '?' 会列出一堆语音包没下载的声音（Sandy/Eddy/Flo/Reed/Rocko...），
    # 它们不报错，只是输出静音。实测真能发声的中文声音只有这三个：
    # Tingting(普通话) / Meijia(台湾) / Sinji(粤语)。用 --test 会自动查。
    "voice": "Tingting",
    "rate": 200,            # 词/分钟，中文 180-240 比较舒服
    "max_chars": 700,       # 单次最多念多少字，超了提示看屏幕
    "speak_notifications": True,
}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# Kokoro 常驻服务（跑在 ~/.claude/voice-tts 那个 3.12 环境里）
# --------------------------------------------------------------------------

def daemon_request(payload, timeout=1.5):
    """给常驻服务发一条命令。连不上就返回 None，调用方负责降级。"""
    import socket
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        s.sendall(json.dumps(payload).encode())
        resp = s.recv(4096)
        s.close()
        return json.loads(resp.decode() or "{}")
    except Exception:
        return None


def daemon_alive():
    return bool((daemon_request({"cmd": "ping"}) or {}).get("ok"))


def daemon_spawn():
    """后台把服务拉起来。模型加载要几秒，所以这一句还是走 say。"""
    if not (os.path.exists(VENV_PY) and os.path.exists(DAEMON_PY)):
        log("daemon: 环境或脚本不存在，留在 say")
        return False
    try:
        subprocess.Popen(
            [VENV_PY, DAEMON_PY],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        log("daemon: 已拉起，正在加载模型")
        return True
    except Exception as e:
        log(f"daemon: 拉起失败 {e!r}")
        return False


# --------------------------------------------------------------------------
# 说话
# --------------------------------------------------------------------------

def stop_speaking(session=None):
    """掐掉还在念的话。只掐自己这一路——你在 A 会话敲键盘，
    不该让 B 会话的播报也跟着断。"""
    daemon_request({"cmd": "stop", "session": session}, timeout=0.5)
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except Exception:
        return
    try:
        # 确认这个 pid 真的是 say，别误杀别的进程
        comm = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if comm.endswith("say"):
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.remove(PID_PATH)
    except Exception:
        pass


def speak(text, cfg, session=None, label=None):
    text = text.strip()
    if not text:
        log("speak: 文本为空，跳过")
        return

    if cfg.get("engine") == "kokoro":
        resp = daemon_request({"cmd": "speak", "text": text,
                               "session": session, "label": label})
        if resp:
            log(f"speak: 交给 kokoro，{len(text)} 字，队列深度 {resp.get('queue')}")
            return
        # 服务没起来：拉起它，但这一句先用 say 兜底，别让用户干等
        daemon_spawn()
        log("speak: kokoro 未就绪，本句降级到 say")

    stop_speaking()
    cmd = ["/usr/bin/say", "-v", str(cfg["voice"]), "-r", str(cfg["rate"]), text]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # 脱离 hook 进程，hook 立刻返回，不卡住 Claude
        )
    except Exception as e:
        log(f"speak: 启动 say 失败 {e!r}")
        return
    log(f"speak: 已启动 say pid={proc.pid} 字数={len(text)}")
    try:
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))
    except Exception:
        pass


# --------------------------------------------------------------------------
# 把 markdown 洗成适合朗读的文字
# --------------------------------------------------------------------------

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF️•]"
)


def _shorten_path(s):
    """长路径念全了非常痛苦，只念文件名。"""
    if "/" in s and len(s) > 22:
        base = os.path.basename(s.rstrip("/"))
        return base or s
    return s


def clean_for_speech(md):
    # 代码块整块拿掉
    md = re.sub(r"```.*?```", "（一段代码）", md, flags=re.S)
    md = re.sub(r"~~~.*?~~~", "（一段代码）", md, flags=re.S)
    # 未闭合的代码块（回复被截断时）
    md = re.sub(r"```.*$", "（一段代码）", md, flags=re.S)
    # markdown 链接只留文字
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"https?://\S+", "（链接）", md)
    # 行内 code：路径缩短，其余保留
    md = re.sub(r"`([^`\n]*)`", lambda m: _shorten_path(m.group(1)), md)
    # 裸露的长路径
    md = re.sub(r"(?<![\w])~?/[\w.\-/]{18,}", lambda m: os.path.basename(m.group(0)), md)
    # 标题、列表符号、引用、分隔线
    md = re.sub(r"^\s{0,3}#{1,6}\s*", "", md, flags=re.M)
    md = re.sub(r"^\s*[-*+]\s+", "", md, flags=re.M)
    md = re.sub(r"^\s*\d+[.)]\s+", "", md, flags=re.M)
    md = re.sub(r"^\s*>\s?", "", md, flags=re.M)
    md = re.sub(r"^\s*([-*_]\s*){3,}$", "", md, flags=re.M)
    # 强调符号、表格竖线
    md = re.sub(r"\*\*|__|~~|\*|(?<!\w)_(?!\w)", "", md)
    md = md.replace("|", " ")
    md = EMOJI_RE.sub("", md)
    # 空白收敛
    md = re.sub(r"[ \t]+", " ", md)
    md = re.sub(r"\n{2,}", "\n", md)
    return md.strip()


SPOKEN_RE = re.compile(r"^\s*🔊[ \t]*(.+?)\s*$", re.M)


def extract_spoken(raw):
    """只念标了 🔊 的那一行。

    屏幕上可以写得详细，耳朵里只听一句结论——不用在"说清楚"和"别啰嗦"之间二选一。
    没标记就退回念全文，老行为不变。
    """
    hits = [m.group(1).strip() for m in SPOKEN_RE.finditer(raw)]
    return " ".join(h for h in hits if h)


def truncate_for_speech(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    best = max(cut.rfind(c) for c in "。！？!?\n.")
    if best > limit * 0.5:
        cut = cut[: best + 1]
    return cut + " 剩下的内容比较长，需要的话看一眼屏幕。"


# --------------------------------------------------------------------------
# 从 transcript 里取出这一轮的最终回复
# --------------------------------------------------------------------------

def last_assistant_text(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=3000)
    except Exception:
        return ""

    chunks = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue

        etype = entry.get("type")
        if etype == "user":
            break            # 到上一轮了，停
        if etype != "assistant":
            continue

        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        # 带 tool_use 的那条是干活途中的旁白，最终回复到此为止
        if any(isinstance(c, dict) and c.get("type") == "tool_use" for c in content):
            break

        text = "".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
        if text.strip():
            chunks.append(text)

    chunks.reverse()
    return "\n".join(chunks)


# --------------------------------------------------------------------------
# 通知消息 -> 中文
# --------------------------------------------------------------------------

NOTIF_PATTERNS = [
    (re.compile(r"permission to use (\w+)", re.I), "需要你批准，用 {0} 工具"),
    (re.compile(r"waiting for your input", re.I), "在等你说话"),
    (re.compile(r"idle", re.I), "闲着呢，等你安排"),
]


def notification_text(msg):
    for pat, tpl in NOTIF_PATTERNS:
        m = pat.search(msg or "")
        if m:
            return tpl.format(*m.groups())
    return "有个提示需要你处理"


# --------------------------------------------------------------------------
# 声音自检
# --------------------------------------------------------------------------

CN_VOICES = ["Tingting", "Meijia", "Sinji", "Sandy", "Eddy", "Flo",
             "Grandma", "Grandpa", "Reed", "Rocko", "Shelley"]
PROBE_TEXT = "语音反馈测试一二三四五"
SILENCE_BYTES = 20000       # 静音的 aiff 恒定在 4800 上下，有声的十万量级


def voice_is_audible(voice):
    """合成到临时文件看大小。语音包没装的声音不报错，只是输出静音——
    这是最坑的失败模式，必须主动查。"""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f".voice_probe_{os.getpid()}.aiff")
    try:
        subprocess.run(
            ["/usr/bin/say", "-v", str(voice), "-o", path, PROBE_TEXT],
            capture_output=True, timeout=20,
        )
        size = os.path.getsize(path) if os.path.exists(path) else 0
    except Exception:
        size = 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return size > SILENCE_BYTES, size


def probe_voices():
    return [(v, *voice_is_audible(v)) for v in CN_VOICES]


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def handle_hook(cfg):
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"hook: stdin 解析失败 {e!r}")
        return

    event = payload.get("hook_event_name", "")
    # 多会话时得知道是谁在说话。用目录名当人话标签，session_id 用来定向取消。
    session = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or ""
    label = os.path.basename(cwd.rstrip("/")).replace("_", " ").replace("-", " ")
    log(f"hook: 收到事件 {event!r} [{label}]")

    if event == "UserPromptSubmit":
        stop_speaking(session)   # 你一开口，只掐这个会话的
        return

    if event == "Notification":
        if cfg.get("speak_notifications", True):
            speak(notification_text(payload.get("message", "")), cfg, session, label)
        return

    if event in ("Stop", "SubagentStop"):
        if event == "SubagentStop":
            return               # 子 agent 不念，太吵
        # Stop hook 和 transcript 落盘是同一瞬间的竞态：hook 经常先跑，
        # 这时最终回复那一行还没写进文件，解析出来是空的。空了就等一下再读。
        import time
        tp = payload.get("transcript_path", "")
        raw = ""
        for attempt in range(6):
            raw = last_assistant_text(tp)
            if raw.strip():
                if attempt:
                    log(f"Stop: 第 {attempt + 1} 次才读到（落盘竞态）")
                break
            time.sleep(0.15)
        spoken = extract_spoken(raw)
        if spoken:
            log(f"Stop: 用 🔊 摘要 {len(spoken)} 字（全文 {len(raw)} 字）")
            text = truncate_for_speech(clean_for_speech(spoken), int(cfg["max_chars"]))
        else:
            log(f"Stop: 无 🔊 标记，念全文 {len(raw)} 字")
            text = truncate_for_speech(clean_for_speech(raw), int(cfg["max_chars"]))
        speak(text, cfg, session, label)
        return


def main():
    args = sys.argv[1:]
    cfg = load_config()

    if "--stop" in args:
        stop_speaking()
        return
    if "--status" in args:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        print(f"config: {CONFIG_PATH}")
        return
    if "--toggle" in args:
        cfg["enabled"] = not cfg.get("enabled", True)
        save_config(cfg)
        state = "打开" if cfg["enabled"] else "关闭"
        print(f"语音反馈已{state}")
        if cfg["enabled"]:
            speak("语音反馈打开了", cfg)
        return
    if "--voices" in args:
        for name, ok, size in probe_voices():
            print(f"{name:12} {size:>8} bytes  {'✅ 有声' if ok else '❌ 静音（语音包没装）'}")
        return
    if "--test" in args:
        ok, size = voice_is_audible(cfg["voice"])
        if not ok:
            print(f"⚠️  {cfg['voice']} 合成出来是静音（{size} bytes），语音包没装。")
            working = [n for n, good, _ in probe_voices() if good]
            print(f"   能用的中文声音：{', '.join(working) or '（一个都没有）'}")
            print(f"   改 {CONFIG_PATH} 里的 voice 字段")
            return
        speak("语音反馈工作正常。你现在可以闭上眼睛写代码了。", cfg)
        print(f"用 {cfg['voice']} 念了一句，语速 {cfg['rate']}（合成 {size} bytes，正常）")
        return

    if not cfg.get("enabled", True):
        return
    handle_hook(cfg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("崩了:\n" + traceback.format_exc())   # 记下来，但不能拖累主流程
