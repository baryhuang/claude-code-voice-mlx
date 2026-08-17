# Voice Feedback for Claude Code + Codex on Apple MLX

**English** · [中文文档](#中文文档)

![Claude Code and Codex voice feedback on Apple MLX](assets/social-preview.jpg)

**Give Claude Code and Codex a local cloned voice — and hear the right agent,
in the right order.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)
![Claude Code](https://img.shields.io/badge/Claude_Code-hooks-d97757)
![Codex](https://img.shields.io/badge/Codex-hooks-10a37f)

MOSS-TTS-Nano runs locally on Apple MLX and clones any voice from a clean
5–10 second recording. After the initial model download, speech stays offline.
Concurrent Claude Code and Codex sessions share one ordered playback queue, so
parallel agents never talk over one another.

| What you get | Why it matters |
| --- | --- |
| **Claude Code + Codex hooks** | One voice system and one queue across both coding agents |
| **Voice cloning** | Use your own voice, language, or accent instead of a preset voice |
| **~0.3 s to first sound** | Hear the result immediately instead of waiting for the full reply |
| **Session-aware FIFO** | Project names are announced on speaker changes; cancellation stays per session |
| **Menu bar icon + notch handle** | Mute and change settings by clicking; no config file editing |
| **MacBook notch status** | See who is speaking and how many utterances are queued |
| **Local after download** | No speech API, API key, or per-character TTS bill |

**Listen:** [demo/queue_demo.m4a](demo/queue_demo.m4a) — three utterances from
two sessions played through the queue, project name announced on speaker change
(12 s).

| Client | Lifecycle hooks | Installer |
| --- | --- | --- |
| Claude Code | `UserPromptSubmit`, `Notification`, `Stop` | `python3 install.py claude` |
| Codex | `UserPromptSubmit`, `PermissionRequest`, `Stop` | `python3 install.py codex` |

## Quick start

Requires macOS on Apple Silicon, Python 3.12, `uv`, and `ffmpeg`.

```bash
git clone https://github.com/baryhuang/claude-codex-voice-mlx
cd claude-codex-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python mlx-audio soundfile
ffmpeg -i your_voice.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav

python3 install.py                    # Claude Code + Codex
~/.claude/hooks/voice_hook.py --test
```

Then open Codex, run `/hooks` once, and trust the three new voice hooks. See
[the detailed setup](#install-claude-code--codex) for per-client installation,
configuration, and uninstall commands.

If this makes parallel agent work easier, **star the repository** — it helps
other Claude Code and Codex users find it.

---

## Architecture

```
Claude Code ──lifecycle hooks──▶ src/hooks/voice_hook.py ─┐
                                                         ├──unix socket──▶ src/daemon/tts_daemon.py ──afplay──▶ audio out
Codex ──lifecycle hooks──▶ src/hooks/codex_voice_hook.py ─┘                            │
                                                                       │ atomic write
                         fallback: /usr/bin/say                         ▼
                                                        ~/.claude/.voice_status.json
                                                                       │ 250 ms poll
                                                                       ▼
                                       voice-notch (menu bar icon + notch handle + panel)
                                                                       │ mute / settings
                                                                       ▼
                                                       ~/.claude/voice_config.json
                                                          (hook and daemon poll mtime)
```

### Queue semantics

- All sessions enqueue into a single FIFO in the daemon. Playback is strictly
  in enqueue order; an utterance plays to completion before the next starts.
- The project directory name is prepended when the speaking session differs
  from the previous one, and omitted otherwise.
- `UserPromptSubmit` in session A cancels A's queued and playing audio only.
- Quiet phrases in the prompt ("不要说", "闭嘴", "stop talking") cancel the
  currently playing utterance regardless of its session, leave the rest of the
  queue intact, and suppress the next reply of the issuing session.
- Enqueue and `ping` responses include current queue depth.

### Daemon

`tts_daemon.py` keeps the model in memory. Model load takes ~3 s; warmup
(first synthesis, tokenizer fetch, compile) finishes ~6 s after start. The
hook sends one JSON message over a unix socket and returns after the
round-trip (single-digit milliseconds); neither Claude Code nor Codex is
blocked by synthesis.

The voice comes from the reference clip at `moss_ref_audio`
(`~/.claude/voice_ref.wav` by default). Without that file the daemon exits and
the hook falls back to `/usr/bin/say`, so audio never goes fully silent.

### Sentence pipelining

Text is split at sentence punctuation; each chunk is synthesized while the
previous one plays. The first chunk is capped at 24 characters (later chunks
60) to reduce time to first audio: measured first-chunk latency is 0.31 s.
Chunks of the next queued utterance are synthesized while the current one is
still playing, so there is no gap at utterance boundaries.

### What is spoken

The hook looks for lines beginning with `🔊` in the final reply and speaks
only those. Without the marker, only the cleaned opening ~100 characters are
spoken. The intended setup instructs both clients (via
`~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`) to start each reply with one
short `🔊` summary line:

```markdown
Every reply must start with a 🔊 line: one sentence, under 50 characters,
plain spoken language, no code or paths. Only that line is read aloud.
```

Cleaning applied before synthesis:

- fenced code blocks → "（一段代码）"
- paths longer than ~18 characters → basename only
- URLs, emoji, markdown syntax, table pipes → removed
- thinking blocks and intermediate tool-use commentary → not spoken; only the
  text after the last tool call of the turn

### Prompt acknowledgement

On each submitted prompt, the hook speaks the configured prefix plus the first
clause of the prompt (≤24 chars), e.g. 「收到，帮我把测试跑一遍」. This
confirms receipt and exposes speech-recognition errors, since the echoed text
is what the system actually received. Slash commands and quiet phrases are not
acknowledged.

### Menu bar icon and mute

`VoiceNotch.swift` builds a single binary that provides both the menu bar
icon and the notch panel. The icon is always in the menu bar (🔇 when muted,
animated when speaking) and its menu holds every runtime setting:

| Menu item | Effect |
| --- | --- |
| **静音（不出声）** | `muted: true` — hooks keep running, nothing is played |
| **跳过当前这段** | `{"cmd": "skip"}` — drop the playing utterance, keep the queue |
| **清空队列** | `{"cmd": "stop"}` — drop everything queued and playing |
| **收到指令先应一声** | `ack_on_prompt` |
| **念系统通知和批准请求** | `speak_notifications` |
| **换会话时报项目名** | `announce_session` |
| **打开配置文件…** | Opens `~/.claude/voice_config.json` |

Mute is a config key, not a socket command, so all three writers agree on one
state: the menu, the CLI (`voice_hook.py --mute` / `--unmute` /
`--mute-toggle`), and hand edits. The hook drops speech before it enqueues;
the daemon re-reads the config (mtime check) before every synthesis and
playback chunk, so anything already queued is dropped too. Muting also sends
`stop` so the currently playing chunk is cut immediately rather than after it
finishes.

`install.py` registers the binary as a login item
(`~/Library/LaunchAgents/com.claude.voice-notch.plist`). It has to start
independently of the daemon: while muted, no hook speaks, so no daemon
spawns — if the icon depended on the daemon it would vanish exactly when it
is needed to unmute. After **退出**, bring it back with:

```bash
launchctl kickstart -k gui/$UID/com.claude.voice-notch
```

### Clicking during playback

Both notch surfaces are buttons, and one of them is always on screen:

| State | What is under the notch | Left click | Right click |
| --- | --- | --- | --- |
| idle | the 46×15 handle | menu | mute toggle |
| speaking | the status pill | menu | mute toggle |

The handle steps aside while the pill is up so the two never stack, and the
pill's window is resized to exactly the pill (`NSHostingView.fittingSize`) —
any transparent margin would swallow clicks in the strip where window title
bars pass. When nothing is showing, the panel goes back to
`ignoresMouseEvents`.

Right click is there because muting mid-sentence is the urgent case: one click
and audio stops immediately (`stop` is sent alongside the config write), and
it stays muted until you turn it back on — mute is a config key, so it also
survives a daemon restart, unlike the one-shot 「闭嘴」 voice command.

### The notch handle (when the menu bar is full)

A menu bar with no free slots is common on notched MacBooks, and macOS handles
overflow badly: the status item is created and reports `isVisible == true`,
but it is placed in the strip left of the notch and its window's
`occlusionState` never contains `.visible`. The icon simply is not drawn, with
no error anywhere.

So the app also draws its own control surface: a 46×15 pill directly under the
notch, always present, dimmed to 42% until the pointer reaches it. Clicking it
opens the same menu as the status item. Its position is ours to choose, so it
cannot be pushed off the bar. It hides while the status panel is speaking so
the two never stack.

The CLI (`voice_hook.py --mute-toggle`, bindable to a hotkey) works regardless
of both.

### Notch status panel

The same binary draws a black panel under the MacBook notch showing the
speaking session's label, the opening words of the utterance, and the number
queued. It is click-through (`ignoresMouseEvents`), sits above the menu bar
(`.statusBar` level), and hides when nothing is speaking, queued, or muted.

It reads `~/.claude/.voice_status.json`, which the daemon rewrites atomically
(`os.replace`) on every queue transition, and polls mtime at 250 ms.
`install.py` compiles it with `swiftc` if available; without it, audio
operates normally and neither the icon nor the panel appears. The daemon also
launches the binary at startup if it is not already running.

---

## Engineering notes

Small details that made or broke this, none of which raised an error:

1. **macOS lists voices that don't exist.** Uninstalled voice packs synthesize
   silence and exit 0. Detection: output file size — silence is a constant
   ~4800 bytes, speech is six figures.
2. **Claude Code's `Stop` hook races the transcript flush** and usually wins,
   reading an empty reply. Retry at 150 ms intervals for up to 900 ms. Codex
   supplies `last_assistant_message` directly and avoids this race.
3. **Barge-in bookkeeping must outlive synthesis.** Synthesis runs ~10×
   faster than playback; dropping the utterance→session map when synthesis
   finished meant the playing utterance was untraceable and cancel found
   nothing.
4. **The model outputs stereo `(N, 2)`.** `reshape(-1)` interleaves the
   channels into a double-length mono stream — plays at half speed, badly
   distorted. Downmix before writing.
5. **The official reference clips are FLAC named `.wav`.** Three of six fail
   to decode until converted.
6. **Never clone from synthesized audio.** A reference clip that is itself TTS
   output degrades the clone — copy of a copy. Use a real recording.
7. **First chunk capped at 24 chars** (later ones 60): time-to-first-sound is
   set by the first chunk alone, so make it small.
8. **Status file written via `os.replace`** so the notch panel never reads a
   torn JSON.
9. **The mute switch cannot live inside the daemon.** While muted no hook
   speaks, so no daemon spawns — a UI owned by the daemon disappears exactly
   when it is needed to unmute. The icon is a login item, and mute is a
   config key both sides poll, not daemon state.
10. **A full menu bar silently swallows the status item.** It is created,
    `isVisible` is true, it has a frame on the menu bar — and it is never
    drawn. The only API that admits it is `window.occlusionState`, which
    never contains `.visible`. Hence the notch handle: a surface whose
    position the app controls.

---

## Install Claude Code + Codex

Requires Apple Silicon and macOS. Python 3.13+ currently has no wheels for
the dependencies; use 3.12.

```bash
git clone https://github.com/baryhuang/claude-codex-voice-mlx
cd claude-codex-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python mlx-audio soundfile

# the voice: any 5-10 s clean single-speaker recording
ffmpeg -i your_voice.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav

# install the shared backend plus both Claude Code and Codex hooks
python3 install.py
~/.claude/hooks/voice_hook.py --test

codex
# inside Codex, run /hooks once and trust the three voice hooks
```

### 1. Shared voice backend and Claude Code

`install.py` copies `voice_hook.py` and `tts_daemon.py` to
`~/.claude/hooks/`, compiles the menu bar app, registers it as a login item,
and registers three hooks in
`~/.claude/settings.json` (user scope, all projects; existing hooks are
preserved and the previous settings file is backed up to `.bak`). Hooks are
run from `~/.claude/hooks/`, not from the clone, so moving or deleting the
clone does not break them. Claude Code re-reads hook configuration per event;
running sessions pick the hooks up without restart.

Install only Claude Code: `python3 install.py claude`.

Uninstall only Claude Code: `python3 install.py claude --uninstall`.

### 2. Codex

After the Claude voice backend above is working, register the equivalent
Codex lifecycle hooks:

```bash
python3 install.py codex
codex                 # then run /hooks and trust the three new hooks
```

The adapter writes `~/.codex/hooks.json` without replacing existing hooks and
backs up an existing file as `hooks.json.bak`. It reuses the same MOSS model,
reference clip, daemon, FIFO, configuration, menu bar icon, and notch panel
as Claude Code, so both clients share one ordered audio queue and one mute
switch. Uninstall only the Codex
adapter with `python3 install.py codex --uninstall`. To remove both clients,
run `python3 install.py --uninstall`.

Codex `Stop` supplies `last_assistant_message` directly, avoiding the
transcript-flush race. `PermissionRequest` is the Codex equivalent used for
spoken approval notices. Codex requires new or changed command hooks to be
reviewed once with `/hooks` before they run.

For short spoken summaries in every repository, add the same `🔊` convention
to `~/.codex/AGENTS.md`:

```markdown
Every final reply must start with a 🔊 line: one sentence, under 50 characters,
plain spoken language, no code or paths. Only that line is read aloud.
```

## Hooks

| Client | Event | Behavior |
| --- | --- | --- |
| Both | `UserPromptSubmit` | Cancel this session's audio; acknowledge the prompt; handle quiet phrases |
| Both | `Stop` | Extract the `🔊` lines from the finished reply and enqueue them |
| Claude Code | `Notification` | Speak permission requests and idle notices |
| Codex | `PermissionRequest` | Speak approval requests before the prompt is shown |

`SubagentStop` events return without speaking.

## Configuration

`~/.claude/voice_config.json`:

```json
{
  "enabled": true,
  "muted": false,
  "engine": "moss",
  "model": "mlx-community/MOSS-TTS-Nano-100M",
  "moss_ref_audio": "~/.claude/voice_ref.wav",
  "max_chars": 700,
  "announce_session": true,
  "ack_on_prompt": true,
  "ack_prefix": "收到",
  "speak_notifications": true
}
```

`engine: "say"` bypasses the daemon entirely (macOS built-in voices; robotic
but dependency-free). Replies longer than `max_chars` are truncated at a
sentence boundary with a spoken notice.

`muted` and `enabled` are different switches. `muted: true` keeps the hooks
running and only suppresses audio, so barge-in, quiet phrases, and the queue
still behave normally the moment you unmute; it is the one the menu bar icon
toggles. `enabled: false` (`voice_hook.py --toggle`) turns the hook off
entirely. `muted`, `announce_session`, `ack_on_prompt`, and
`speak_notifications` take effect immediately; `model` and `moss_ref_audio`
are read when the daemon loads and need a restart.

### The voice

The daemon speaks in whatever voice is at `moss_ref_audio`: 5–10 seconds of
clean, single-speaker audio, no background music. Any accent or language the
clip carries, the output carries. Convert anything ffmpeg can read:

```bash
ffmpeg -i clip.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav
```

Restart the daemon after changing the clip (`pkill -f tts_daemon.py`; the next
reply respawns it).

---

## Limitations

- Output only. Permission prompts and option questions still require the
  keyboard; there is no voice confirmation path.
- If the network drops mid-turn, no `Stop` event fires and nothing is spoken.
  There is no stall/heartbeat alert.
- The acknowledgement goes through the same FIFO as everything else; if
  another session is mid-utterance, the ack waits (no priority lane).
- No long-running-command narration: between reply boundaries the system is
  silent unless a permission notification fires.
- Playback stops when the Mac sleeps; the daemon does not prevent sleep.
- The `🔊` convention depends on the model following `CLAUDE.md` or
  `AGENTS.md`. When it does not, only the cleaned opening ~100 characters are
  spoken.
- macOS and Apple Silicon only.

---

## Troubleshooting

### `say` exits 0 but produces no sound (fallback path)

macOS lists voices whose packs are not installed. Synthesis for those
succeeds, exits 0, and produces silence. Detect by output size — silence is a
constant ~4800 bytes, real speech is six figures:

```bash
~/.claude/hooks/voice_hook.py --voices
```

On this machine only Tingting, Meijia, and Sinji were installed out of the 11
listed Chinese voices. Additional voices can be installed under System
Settings → Accessibility → Spoken Content → System Voice → Manage Voices.

### Nothing spoken at end of turn (intermittent)

The `Stop` hook races the transcript flush and often runs first, reading an
empty final message. The hook retries for up to 900 ms before giving up.
`~/.claude/voice_hook.log` records the character count read per event.

### Speech is half-speed and distorted

The model emits 48 kHz stereo; if you process its output yourself, downmix
`(N, 2)` to mono before writing — flattening interleaves the channels
(engineering note 4).

### Logs

```bash
tail -f ~/.claude/voice_hook.log          # per-event: type, session, chars
tail -f ~/.claude/voice_tts_daemon.log    # queue transitions, first-chunk latency
```

---

## Measurements

M3 Pro, 36 GB, MOSS-TTS-Nano-100M, warm daemon:

| Input | Synthesis | Audio duration | Real-time factor |
| --- | --- | --- | --- |
| short (10 chars) | 0.52 s | 5.6 s | 10.7× |
| mixed zh/en (24 chars) | 1.00 s | 11.2 s | 11.3× |
| long (36 chars) | 1.8 s | 16.6 s | 9.1× |

First-chunk latency through the daemon: 0.31 s. Model on disk ~280 MB;
resident memory ~470 MB.

### Models evaluated

| Model | Real-time factor (this hardware) | Outcome |
| --- | --- | --- |
| MOSS-TTS-Nano-100M (OpenMOSS, 2026) | 9–11× | in use — voice cloning, native zh/en mixing |
| Kokoro-82M | 10–18× | replaced: preset voices only, English inside Chinese text unusable without patching |
| Qwen3-TTS 1.7B VoiceDesign 8-bit | 1.2× | rejected: latency |
| BlueMagpie-TTS (Taiwanese accent) | ~1.3× (MLX, per upstream docs) | rejected: latency, 8 GB download |
| macOS `say` | n/a (streams immediately) | fallback only |

Selection criterion: below roughly 5× real time, queue backlog becomes
audible as start-of-utterance delay, which defeats the ordered-queue design.

---

## Repository layout

```text
.
├── install.py                    # unified installer: all / claude / codex
├── installers/
│   ├── install_claude.py         # ~/.claude/settings.json integration
│   └── install_codex.py          # ~/.codex/hooks.json integration
├── src/
│   ├── hooks/
│   │   ├── voice_hook.py         # shared behavior + Claude hook entry
│   │   └── codex_voice_hook.py   # Codex JSON adapter
│   ├── daemon/
│   │   └── tts_daemon.py         # resident model, FIFO, synthesis, playback
│   └── macos/
│       └── VoiceNotch.swift      # menu bar icon + notch status panel
├── tests/
│   └── test_codex_voice.py
└── demo/
    └── queue_demo.m4a
```

---

# 中文文档

![Claude Code 和 Codex 的 Apple MLX 本地语音反馈](assets/social-preview.jpg)

**给 Claude Code 和 Codex 一个本地克隆声音，并让多个智能体按正确顺序开口。**

**让 Claude Code 和 Codex 都在你的 Mac 上开口说话。** 回复由本地 Apple MLX 上运行的
MOSS-TTS-Nano 合成播放——这是一个一亿参数的声音克隆模型，你给一段 5–10 秒的
参考音频，它就用那个声音说话，预置音色没有的口音（比如台湾腔）也因此可用。
多个并发会话共用一个播放队列：按顺序一段一段播完，换会话时报项目名，取消一个
会话的语音不影响其他会话。

模型下载完成后不需要任何网络。

实测（M3 Pro，36 GB）：回复结束后约 0.3 秒出声，合成速度为实时的 9–11 倍，
常驻内存约 470 MB，磁盘约 280 MB。

**试听：** [demo/queue_demo.m4a](demo/queue_demo.m4a) — 两个会话的三段话
经过队列播放，换会话时报项目名（12 秒）。

| 客户端 | 生命周期 Hooks | 安装器 |
| --- | --- | --- |
| Claude Code | `UserPromptSubmit`、`Notification`、`Stop` | `python3 install.py claude` |
| Codex | `UserPromptSubmit`、`PermissionRequest`、`Stop` | `python3 install.py codex` |

| 能力 | 作用 |
| --- | --- |
| **Claude Code + Codex hooks** | 两个编码智能体共用一套语音系统和一个队列 |
| **声音克隆** | 用自己的声音、语言和口音，不受预置音色限制 |
| **约 0.3 秒出声** | 不必等整段回复合成完就能听到结果 |
| **会话感知 FIFO** | 换会话时报项目名，取消语音只影响当前会话 |
| **菜单栏图标 + 刘海把手** | 静音和各项开关点一下就改，不用翻 JSON |
| **MacBook 刘海状态** | 一眼看到谁在说话、后面还有几段排队 |
| **下载后本地运行** | 不需要语音 API、API key 或按字计费 |

## 快速开始

需要 Apple Silicon Mac、Python 3.12、`uv` 和 `ffmpeg`。

```bash
git clone https://github.com/baryhuang/claude-codex-voice-mlx
cd claude-codex-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python mlx-audio soundfile
ffmpeg -i 你的声音.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav

python3 install.py                    # Claude Code + Codex
~/.claude/hooks/voice_hook.py --test
```

然后打开 Codex，输入一次 `/hooks`，信任三个新语音 hook。单独安装某个客户端、
配置和卸载命令见[完整安装说明](#安装-claude-code--codex)。如果它让并行智能体更好用，
欢迎给仓库一个 **Star**，这样其他 Claude Code 和 Codex 用户也更容易找到它。

## 架构

```
Claude Code ──生命周期 hooks──▶ src/hooks/voice_hook.py ─┐
                                                         ├──unix socket──▶ src/daemon/tts_daemon.py ──afplay──▶ 声音输出
Codex ──生命周期 hooks──▶ src/hooks/codex_voice_hook.py ─┘                            │
                                                                       │ 原子写入
                           兜底: /usr/bin/say                           ▼
                                                        ~/.claude/.voice_status.json
                                                                       │ 250ms 轮询
                                                                       ▼
                                       voice-notch (menu bar icon + notch handle + panel)
                                                                       │ mute / settings
                                                                       ▼
                                                       ~/.claude/voice_config.json
                                                          (hook and daemon poll mtime)
```

### 队列语义

- 所有会话进同一个 FIFO，严格按入队顺序播放，一段播完才播下一段。
- 说话的会话变了才报项目目录名，同一会话连续播报不重复报。
- 在会话 A 里输入，只取消 A 的排队和播放，不动其他会话。
- 提示词里出现"不要说""闭嘴""stop talking"这类话：跳过正在播的那段
  （不论属于哪个会话），队列其余照常，且发令会话的下一条回复静音。
- 入队和 `ping` 都返回当前队列深度。

### 常驻服务

`tts_daemon.py` 把模型留在内存里。模型加载约 3 秒，预热（首次合成、拉取
tokenizer、编译）在启动后约 6 秒完成。hook 通过 unix socket 发一条 JSON 就
返回（毫秒级），Claude Code 和 Codex 都不会被合成阻塞。

声音来自 `moss_ref_audio` 指向的参考音频（默认 `~/.claude/voice_ref.wav`）。
文件不存在时守护进程直接退出，hook 自动降级用 `/usr/bin/say`，不会彻底无声。

### 按句流水线

按标点断句，上一句在播时合成下一句。第一块限 24 字（后续 60 字）以缩短开口
延迟：实测首句 0.31 秒出声。下一段排队话语的音频在当前段播放期间就已合成好，
段间无空隙。

### 念什么

hook 只念最终回复里以 `🔊` 开头的行；没有标记就只念清洗后的开头约 100 字。
配套做法是在 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md` 里约定每条回复
第一行写一句 `🔊` 摘要。

合成前的清洗：代码块 →"（一段代码）"；长路径只留文件名；URL、emoji、
markdown 符号、表格竖线删除；思考过程和干活途中的旁白不念，只念本轮最后
一次工具调用之后的文字。

### 指令回执

每次收到提示词，先念配置的前缀加指令第一小句（≤24 字），如「收到，帮我把
测试跑一遍」。既确认收到，也把语音识别听错的地方暴露出来。斜杠命令和闭嘴
口令不回执。

### 菜单栏图标与静音

`VoiceNotch.swift` 编译出来的是一个程序，同时提供菜单栏图标和刘海状态条。
图标常驻菜单栏（静音时是划掉的喇叭，播报时会动），菜单里就是全部运行时设置：

| 菜单项 | 作用 |
| --- | --- |
| **静音（不出声）** | `muted: true`——hook 照常跑，只是不播放 |
| **跳过当前这段** | `{"cmd": "skip"}`——掐掉正在播的，队列继续 |
| **清空队列** | `{"cmd": "stop"}`——正在播的和排队的一起丢掉 |
| **收到指令先应一声** | `ack_on_prompt` |
| **念系统通知和批准请求** | `speak_notifications` |
| **换会话时报项目名** | `announce_session` |
| **打开配置文件…** | 打开 `~/.claude/voice_config.json` |

静音是配置里的键，不是 socket 命令，所以三个入口说的是同一件事：菜单、
命令行（`voice_hook.py --mute` / `--unmute` / `--mute-toggle`）和手改配置。
hook 在入队之前就丢掉文本；守护进程每次合成、每次播放前看一眼配置 mtime，
已经排在队列里的也一并丢掉。点静音时还会顺手发一条 `stop`，正在播的那半句
立刻断掉，不用等它念完。

`install.py` 会把这个程序注册成登录项
（`~/Library/LaunchAgents/com.claude.voice-notch.plist`）。它必须独立于守护
进程启动：静音时没有 hook 会说话，守护进程根本不会被拉起——如果图标归守护
进程管，就会在你最需要它取消静音的时候消失。菜单里点了**退出**之后想再打开：

```bash
launchctl kickstart -k gui/$UID/com.claude.voice-notch
```

### 播报当中怎么点

刘海下面这两块都是按钮，任何时候都有一块在：

| 状态 | 刘海下面是什么 | 左键 | 右键 |
| --- | --- | --- | --- |
| 空闲 | 46×15 的小把手 | 菜单 | 直接静音／取消静音 |
| 播报中 | 状态条本身 | 菜单 | 直接静音／取消静音 |

状态条弹出时把手让位，两块黑的不会叠；状态条的窗口按
`NSHostingView.fittingSize` 收到刚好包住它——多出来的透明边会吞掉点击，而那
一带正是窗口标题栏经过的地方。都不显示时面板恢复点击穿透。

右键是为了「正在念的时候马上闭嘴」这个急事：一下就停（写配置的同时发
`stop`），而且一直静音到你自己打开为止——静音是配置里的键，守护进程重启也还
在，跟一次性的「闭嘴」口令不是一回事。

### 刘海把手（菜单栏塞满时）

有刘海的 MacBook 很容易把菜单栏塞满，而 macOS 处理溢出的方式很糟：图标建出来
了、`isVisible` 也是 true，但它被排进刘海左边那条，窗口的 `occlusionState`
里始终没有 `.visible`——就是不画出来，而且哪儿都不报错。

所以程序自己还画了一块入口：刘海正下方 46×15 的小把手，一直在，平时只有 42%
浓度，鼠标靠近才显出来。点它弹出的菜单和菜单栏图标那份一模一样。位置由我们
自己定，挤不掉。播报状态条弹出时它自动让位，两块黑的不会叠在一起。

命令行（`voice_hook.py --mute-toggle`，可以绑快捷键）在两者之外任何时候都能用。

### 刘海状态条

同一个程序在刘海下方画一块黑色面板，显示正在播报的会话名、内容开头和排队
数量。点击穿透，位于菜单栏之上，空闲、静音时隐藏。
它读守护进程每次队列变化时原子重写（`os.replace`）的状态文件，250 毫秒轮询
mtime。`install.py` 检测到 `swiftc` 就编译；没有 `swiftc` 则图标和面板都没有，
不影响语音。守护进程启动时也会拉起它（如果还没在跑）。

## 工程笔记

以下细节都不报错，全是静默的坏结果：

1. **macOS 会列出不存在的声音。** 语音包没装的声音合成出静音、退出码 0。
   判据：文件大小——静音恒约 4800 字节，真语音是六位数。
2. **Claude Code 的 `Stop` hook 和 transcript 落盘是竞态**，hook 常先跑、
   读到空回复。以 150 毫秒间隔重试，最多 900 毫秒。Codex 直接提供
   `last_assistant_message`，没有这个竞态。
3. **打断的账本必须活到播放结束。** 合成比播放快约 10 倍；合成一完就删
   话语→会话映射，正在播的那段就查无此人，取消永远扑空。
4. **模型输出立体声 `(N, 2)`。** `reshape(-1)` 把左右声道交错摊成双倍长的
   单声道——半速播放、严重失真。写文件前必须混成单声道。
5. **官方参考音频是 FLAC 伪装成 `.wav`**，六个里三个解不开，转格式才行。
6. **别拿合成音频当克隆参考。** 参考本身是 TTS 的产物会劣化克隆——复印件再
   复印。要用真人录音。
7. **第一块限 24 字**（后续 60）：开口延迟只由第一块决定，越小越快。
8. **状态文件用 `os.replace` 写**，刘海面板永远读不到半截 JSON。
9. **静音开关不能长在守护进程里。** 静音时没有 hook 会说话，守护进程也就
   不会被拉起——归它管的界面正好在你要取消静音时消失。所以图标是登录项，
   静音是两边各自轮询的配置键，不是守护进程的内部状态。
10. **菜单栏塞满时状态栏图标会被无声吞掉。** 图标建出来了、`isVisible` 是
    true、frame 也在菜单栏上，就是不画。唯一肯说实话的 API 是
    `window.occlusionState`——里面永远没有 `.visible`。刘海把手就是为此而来：
    位置由程序自己说了算。

## 安装 Claude Code + Codex

需要 Apple Silicon 和 macOS。Python 3.13+ 目前没有依赖的 wheel，用 3.12。

```bash
git clone https://github.com/baryhuang/claude-codex-voice-mlx
cd claude-codex-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python mlx-audio soundfile

# 声音：任何 5-10 秒干净的单人录音
ffmpeg -i 你的声音.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav

# 安装共用后端、Claude Code hooks 和 Codex hooks
python3 install.py
~/.claude/hooks/voice_hook.py --test

codex
# 在 Codex 里输入一次 /hooks，信任三个语音 hook
```

### 1. 共用语音后端和 Claude Code

`install.py` 把两个 Python 文件拷到 `~/.claude/hooks/`、编译菜单栏图标并注册
成登录项、在
`~/.claude/settings.json`（用户级，所有项目生效）注册三个 hook；已有 hook
保留，旧配置备份为 `.bak`。hook 从 `~/.claude/hooks/` 运行，不依赖 clone
目录。Claude Code 每次事件都重读 hook 配置，运行中的会话无需重启。

只安装 Claude Code：`python3 install.py claude`。

只卸载 Claude Code：`python3 install.py claude --uninstall`。

### 2. Codex

上面的 Claude 语音后端能工作后，注册对应的 Codex lifecycle hooks：

```bash
python3 install.py codex
codex                 # 然后输入 /hooks，信任新加入的三个 hook
```

安装器会保留 `~/.codex/hooks.json` 里已有的 hook，并把旧文件备份为
`hooks.json.bak`。Codex 和 Claude Code 共用同一个 MOSS 模型、参考声音、
常驻服务、FIFO 队列、配置、菜单栏图标和刘海面板，因此两边同时工作时仍然按一个
队列播报，静音也是一起静音。
只卸载 Codex 适配器：`python3 install.py codex --uninstall`。两边一起卸载：
`python3 install.py --uninstall`。

Codex 的 `Stop` 直接提供 `last_assistant_message`，没有 transcript 落盘竞态；
权限语音提醒使用 `PermissionRequest`。Codex 对新建或变更过的命令 hook 要求先
通过 `/hooks` 审核信任一次。

如果希望所有项目只念简短结论，把同样的约定加入 `~/.codex/AGENTS.md`：

```markdown
每条最终回复必须以 🔊 行开头：一句话、50 字以内、适合朗读，不含代码和路径。
只有这一行会被念出来。
```

## Hooks

| 客户端 | 事件 | 行为 |
| --- | --- | --- |
| 两者 | `UserPromptSubmit` | 取消本会话语音；回执指令；处理闭嘴口令 |
| 两者 | `Stop` | 提取回复里的 `🔊` 行并入队 |
| Claude Code | `Notification` | 念出权限请求和空闲提示 |
| Codex | `PermissionRequest` | 在权限确认框出现时念出请求 |

`SubagentStop` 直接返回不念。

## 配置

`~/.claude/voice_config.json`：

```json
{
  "enabled": true,
  "muted": false,
  "engine": "moss",
  "model": "mlx-community/MOSS-TTS-Nano-100M",
  "moss_ref_audio": "~/.claude/voice_ref.wav",
  "max_chars": 700,
  "announce_session": true,
  "ack_on_prompt": true,
  "ack_prefix": "收到",
  "speak_notifications": true
}
```

`engine: "say"` 完全绕过守护进程（系统自带声音，机械但零依赖）。超过
`max_chars` 的回复在句号处截断并念一句提示。

`muted` 和 `enabled` 是两个开关。`muted: true` 时 hook 照常运行，只是不出声，
打断、闭嘴口令、队列这些逻辑都还在，取消静音后立刻恢复正常——菜单栏图标点
的就是它。`enabled: false`（`voice_hook.py --toggle`）是把 hook 整个关掉。
`muted`、`announce_session`、`ack_on_prompt`、`speak_notifications` 改完立刻
生效；`model` 和 `moss_ref_audio` 是守护进程加载时读的，改完要重启。

### 声音

守护进程用 `moss_ref_audio` 里那段声音说话：5–10 秒、干净、单人、无背景音乐。
参考音频里是什么口音、什么语言，输出就是什么。ffmpeg 能读的格式都能转：

```bash
ffmpeg -i clip.mp3 -t 10 -ac 1 -ar 48000 -sample_fmt s16 ~/.claude/voice_ref.wav
```

换参考音频后重启守护进程（`pkill -f tts_daemon.py`，下一条回复会自动拉起）。

## 限制

- 只有输出。权限确认和选项问题仍需键盘，没有语音应答通道。
- 断网时回合不结束，`Stop` 不触发，没有任何声音；没有卡死心跳报警。
- 回执和其他内容走同一个 FIFO，别的会话在播时回执要排队，没有优先通道。
- 长命令执行期间没有进度播报，除非权限通知触发。
- Mac 休眠即停，守护进程不阻止休眠。
- `🔊` 约定依赖模型遵守 `CLAUDE.md` 或 `AGENTS.md`；不遵守时只念清洗后的
  开头约 100 字。
- 仅支持 macOS 和 Apple Silicon。

## 排障

### `say` 退出码 0 但没有声音（兜底路径）

用 `--voices` 自检（原理见工程笔记第 1 条）。缺的声音在
系统设置 → 辅助功能 → 朗读内容 → 系统声音 → 管理声音 里下载。

### 回合结束偶尔不念

工程笔记第 2 条的竞态，已用重试兜住。`~/.claude/voice_hook.log` 记录每次
事件读到的字数。

### 声音半速且失真

模型输出 48 kHz 立体声；自己处理它的输出时，`(N, 2)` 必须先混成单声道再写
文件——直接摊平会把声道交错（工程笔记第 4 条）。

### 日志

```bash
tail -f ~/.claude/voice_hook.log          # 每次事件：类型、会话、字数
tail -f ~/.claude/voice_tts_daemon.log    # 队列变化、首句延迟
```

## 测量数据

M3 Pro，36 GB，MOSS-TTS-Nano-100M，热启动：

| 输入 | 合成耗时 | 音频时长 | 实时倍率 |
| --- | --- | --- | --- |
| 短句（10 字） | 0.52 s | 5.6 s | 10.7× |
| 中英混排（24 字） | 1.00 s | 11.2 s | 11.3× |
| 长句（36 字） | 1.8 s | 16.6 s | 9.1× |

经守护进程全链路的首句延迟 0.31 秒。模型磁盘占用约 280 MB，常驻内存约 470 MB。

### 评估过的模型

| 模型 | 实时倍率（本机） | 结论 |
| --- | --- | --- |
| MOSS-TTS-Nano-100M（OpenMOSS，2026） | 9–11× | 在用——声音克隆、中英混排原生支持 |
| Kokoro-82M | 10–18× | 已替换：只有预置音色，中文夹英文不打补丁没法听 |
| Qwen3-TTS 1.7B VoiceDesign 8-bit | 1.2× | 弃：太慢 |
| BlueMagpie-TTS（台湾口音） | ~1.3×（MLX，引自上游文档） | 弃：太慢，8 GB |
| macOS `say` | 即时流式 | 仅作兜底 |

取舍标准：低于约 5 倍实时，队列积压会变成可听见的起播延迟，破坏顺序队列的
设计初衷。

## 目录结构

```text
.
├── install.py                    # 统一安装入口：all / claude / codex
├── installers/
│   ├── install_claude.py         # 集成 ~/.claude/settings.json
│   └── install_codex.py          # 集成 ~/.codex/hooks.json
├── src/
│   ├── hooks/
│   │   ├── voice_hook.py         # 共用行为和 Claude hook 入口
│   │   └── codex_voice_hook.py   # Codex JSON 适配器
│   ├── daemon/
│   │   └── tts_daemon.py         # 常驻模型、FIFO、合成和播放
│   └── macos/
│       └── VoiceNotch.swift      # 菜单栏图标 + 刘海状态面板
├── tests/
│   └── test_codex_voice.py
└── demo/
    └── queue_demo.m4a
```

## License

MIT
