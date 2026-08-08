# claude-code-voice-mlx

**English** · [中文文档](#中文文档)

**Claude Code talks on your Mac.** Text-to-speech output for Claude Code on
macOS / Apple Silicon. Replies are synthesized locally on Apple MLX — by
Kokoro-82M (preset voices) or MOSS-TTS-Nano (any voice cloned from a 5–10 s
reference clip) — and played through the system audio output. Concurrent
sessions share one playback queue: utterances play sequentially, the project
name is announced when the speaking session changes, and cancelling one
session's audio does not affect the others.

No network access is required after the initial model download.

Measured on an M3 Pro (36 GB): first audio ~0.3 s after a reply completes,
synthesis at 10–18× real time, ~200 MB resident memory.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)

**Listen:** [demo/queue_demo.m4a](demo/queue_demo.m4a) — three utterances from
two sessions played through the queue, project name announced on speaker change
(12 s).

---

## Architecture

```
Claude Code session ──hook──▶ voice_hook.py ──unix socket──▶ tts_daemon.py ──afplay──▶ audio out
                                   │                              │
                                   │ fallback: /usr/bin/say       │ atomic write
                                   ▼                              ▼
                          ~/.claude/voice_hook.log      ~/.claude/.voice_status.json
                                                                  │ 250 ms poll
                                                                  ▼
                                                          voice-notch (SwiftUI)
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

`tts_daemon.py` keeps the model in memory. Model load takes ~1 s; the first
synthesis additionally downloads the voice file and builds the jieba
dictionary (~5 s), which is done once at startup as a warmup. The hook sends
one JSON message over a unix socket and returns after the round-trip
(single-digit milliseconds); Claude Code is not blocked by synthesis.

If the socket is unreachable, the hook falls back to `/usr/bin/say` for that
utterance and starts the daemon in the background.

### Sentence pipelining

Text is split at sentence punctuation; each chunk is synthesized while the
previous one plays. The first chunk is capped at 24 characters (later chunks
60) to reduce time to first audio. Measured: a 117-character reply split into
3 chunks produced first audio at 0.34 s. Chunks of the next queued utterance
are synthesized while the current one is still playing, so there is no gap at
utterance boundaries.

### What is spoken

The hook looks for lines beginning with `🔊` in the final reply and speaks
only those. Without the marker, the full reply is cleaned and spoken instead.
The intended setup instructs the agent (via `~/.claude/CLAUDE.md`) to start
each reply with one short `🔊` summary line:

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

### Notch status panel

`VoiceNotch.swift` is a single-file SwiftUI program that draws a black panel
under the MacBook notch showing the speaking session's label, the opening
words of the utterance, and the number queued. It is click-through
(`ignoresMouseEvents`), sits above the menu bar (`.statusBar` level), and
hides when nothing is speaking or queued.

It reads `~/.claude/.voice_status.json`, which the daemon rewrites atomically
(`os.replace`) on every queue transition, and polls mtime at 250 ms.
`install.py` compiles it with `swiftc` if available; without it, audio
operates normally and no panel appears. The daemon launches the binary at
startup if it is not already running.

---

## Engineering notes

Small details that made or broke this, none of which raised an error:

1. **macOS lists voices that don't exist.** Uninstalled voice packs synthesize
   silence and exit 0. Detection: output file size — silence is a constant
   ~4800 bytes, speech is six figures.
2. **The `Stop` hook races the transcript flush** and usually wins, reading an
   empty reply. Retry at 150 ms intervals for up to 900 ms.
3. **Barge-in bookkeeping must outlive synthesis.** Synthesis runs 10–18×
   faster than playback; dropping the utterance→session map when synthesis
   finished meant the playing utterance was untraceable and cancel found
   nothing.
4. **MOSS outputs stereo `(N, 2)`.** `reshape(-1)` interleaves the channels
   into a double-length mono stream — plays at half speed, badly distorted.
   Downmix before writing.
5. **MOSS's official reference clips are FLAC named `.wav`.** Three of six
   fail to decode until converted.
6. **spacy's on-demand model download kills the process** inside a uv-managed
   venv (its pip subprocess exits nonzero). Preinstall `en_core_web_sm` as a
   wheel.
7. **Kokoro's Chinese G2P ships without an English callback**, so embedded
   English is phonemized as Chinese. Patch `ZHG2P(en_callable=...)` after
   warmup.
8. **Never clone from synthesized audio.** A reference clip that is itself TTS
   output degrades the clone — copy of a copy. Use a real recording.
9. **First chunk capped at 24 chars** (later ones 60): time-to-first-sound is
   set by the first chunk alone, so make it small.
10. **Status file written via `os.replace`** so the notch panel never reads a
    torn JSON. Two of Kokoro's eight Mandarin voices (`zf_xiaobei`,
    `zf_xiaoni`) are dialect voices — easy to pick by accident.

## Install

Requires Apple Silicon and macOS. Python 3.13+ currently has no wheels for
the dependencies; use 3.12.

```bash
git clone https://github.com/baryhuang/claude-code-voice-mlx
cd claude-code-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python \
    mlx-audio soundfile "misaki[zh,en]" \
    "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

python3 install.py
~/.claude/hooks/voice_hook.py --test
```

`install.py` copies `voice_hook.py` and `tts_daemon.py` to
`~/.claude/hooks/`, compiles the notch panel, and registers three hooks in
`~/.claude/settings.json` (user scope, all projects; existing hooks are
preserved and the previous settings file is backed up to `.bak`). Hooks are
run from `~/.claude/hooks/`, not from the clone, so moving or deleting the
clone does not break them. Claude Code re-reads hook configuration per event;
running sessions pick the hooks up without restart.

Uninstall: `python3 install.py --uninstall`.

## Hooks

| Event | Behavior |
| --- | --- |
| `Stop` | Extract the `🔊` lines from the finished reply and enqueue them |
| `Notification` | Speak permission requests and idle notices |
| `UserPromptSubmit` | Cancel this session's audio; acknowledge the prompt; handle quiet phrases |

`SubagentStop` events return without speaking.

## Configuration

`~/.claude/voice_config.json`:

```json
{
  "enabled": true,
  "engine": "kokoro",
  "tts_model": "kokoro",
  "model": "mlx-community/Kokoro-82M-bf16",
  "kokoro_voice": "zf_xiaoxiao",
  "moss_ref_audio": "~/.claude/voice_ref.wav",
  "speed": 1.0,
  "max_chars": 700,
  "announce_session": true,
  "ack_on_prompt": true,
  "ack_prefix": "收到",
  "speak_notifications": true
}
```

`tts_model: "moss"` switches the daemon to
[MOSS-TTS-Nano-100M](https://huggingface.co/mlx-community/MOSS-TTS-Nano-100M)
(OpenMOSS, 2026), a voice-cloning model: it speaks in whatever voice you put at
`moss_ref_audio` (5–10 s of clean single-speaker audio; this is how you get an
accent no preset ships, e.g. Taiwanese Mandarin). Native mixed Chinese–English,
48 kHz, ~9× real time on an M3 Pro. Note its output is **stereo** `(N, 2)` —
flattening it naively interleaves the channels and plays at half speed,
distorted; the daemon downmixes to mono. If the reference file is missing the
daemon falls back to Kokoro. Kokoro remains the default: 24 kHz mono, preset
voices, slightly faster.

`engine: "say"` bypasses the daemon entirely. Replies longer than `max_chars`
are truncated at a sentence boundary with a spoken notice.

### Voices

Kokoro ships 8 Mandarin voices:

| Voice | Note |
| --- | --- |
| `zf_xiaoxiao` (default), `zf_xiaoyi` | standard Mandarin, female |
| `zm_yunxi`, `zm_yunyang`, `zm_yunjian`, `zm_yunxia` | standard Mandarin, male |
| `zf_xiaobei` | Liaoning-dialect accent |
| `zf_xiaoni` | Shaanxi-dialect accent |

English voices (`af_heart`, `am_michael`, …) require `lang_code` `"a"`.

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
- No preset covers regional accents (e.g. Taiwanese Mandarin). The working
  route is the MOSS cloning engine with a user-supplied reference clip; there
  is no ready-made voice to pick.
- Playback stops when the Mac sleeps; the daemon does not prevent sleep.
- The `🔊` convention depends on the model following the CLAUDE.md
  instruction. When it does not, the cleaned full reply is spoken, which is
  long.
- macOS and Apple Silicon only.

---

## Troubleshooting

### `say` exits 0 but produces no sound

macOS lists voices whose packs are not installed. Synthesis for those
succeeds, exits 0, and produces silence. Detect by output size — silence is a
constant ~4800 bytes, real speech is six figures:

```bash
~/.claude/hooks/voice_hook.py --voices
```

```
Tingting   113760 bytes  ok
Meijia     120606 bytes  ok
Sinji      118574 bytes  ok
Sandy        4800 bytes  voice pack not installed
Eddy         4800 bytes  voice pack not installed
```

On this machine only Tingting, Meijia, and Sinji were installed out of the 11
listed Chinese voices. Additional voices can be installed under System
Settings → Accessibility → Spoken Content → System Voice → Manage Voices.

### `No module named 'misaki'`

Chinese synthesis needs the `misaki[zh]` grapheme-to-phoneme package, and
calls must pass `lang_code="z"` explicitly; otherwise Kokoro loads the
English g2p and raises.

### English words inside Chinese sentences sound garbled

mlx-audio constructs the Chinese G2P without an English callback, so embedded
English ("GitHub", "pytest") is mangled by the Chinese converter. The daemon
patches the pipeline after warmup: `ZHG2P(en_callable=...)` wired to the
English G2P. This requires spacy's `en_core_web_sm` to be installed **ahead of
time** (included in the install command above) — if spacy tries to download it
at first use, its installer subprocess fails inside a uv-managed venv and
takes the daemon down with it.

### Nothing spoken at end of turn (intermittent)

The `Stop` hook races the transcript flush and often runs first, reading an
empty final message. The hook retries for up to 900 ms before giving up.
`~/.claude/voice_hook.log` records the character count read per event.

### Logs

```bash
tail -f ~/.claude/voice_hook.log          # per-event: type, session, chars
tail -f ~/.claude/voice_tts_daemon.log    # queue transitions, first-chunk latency
```

---

## Measurements

M3 Pro, 36 GB, Kokoro-82M bf16, warm daemon:

| Input | Synthesis | Audio duration | Real-time factor |
| --- | --- | --- | --- |
| 12 chars | 0.30 s | 3.20 s | 10.6× |
| 28 chars | 0.40 s | 6.95 s | 17.4× |
| 60 chars | 0.81 s | 14.55 s | 18.1× |

Model on disk ~340 MB; resident memory ~200 MB.

### Models evaluated

| Model | Real-time factor (this hardware) | Outcome |
| --- | --- | --- |
| Kokoro-82M | 10–18× | default engine |
| MOSS-TTS-Nano-100M | ~9× | cloning engine (`tts_model: "moss"`) |
| Qwen3-TTS 1.7B VoiceDesign 8-bit | 1.2× | rejected: latency |
| BlueMagpie-TTS (Taiwanese accent) | ~1.3× (MLX, per upstream docs) | rejected: latency, 8 GB download |
| macOS `say` | n/a (streams immediately) | fallback only |

Selection criterion: below roughly 5× real time, queue backlog becomes
audible as start-of-utterance delay, which defeats the ordered-queue design.

---

## Files

| File | Role |
| --- | --- |
| `voice_hook.py` | Hook entry: transcript parsing, cleaning, session routing, `say` fallback |
| `tts_daemon.py` | Resident TTS server (Kokoro / MOSS): FIFO queue, sentence pipelining, per-session cancel |
| `VoiceNotch.swift` | Notch status panel |
| `install.py` | Copy to `~/.claude/hooks/`, compile panel, register hooks |

---

# 中文文档

**让 Claude Code 在你的 Mac 上开口说话。** 回复由本地 Apple MLX 上运行的
Kokoro-82M（或 MOSS-TTS-Nano 克隆音色）合成播放。多个并发会话共用一个播放
队列：按顺序一段一段播完，换会话时报项目名，取消一个会话的语音不影响其他会话。

模型下载完成后不需要任何网络。

实测（M3 Pro，36 GB）：回复结束后约 0.3 秒出声，合成速度为实时的 10–18 倍，
常驻内存约 200 MB。

**试听：** [demo/queue_demo.m4a](demo/queue_demo.m4a) — 两个会话的三段话
经过队列播放，换会话时报项目名（12 秒）。

## 架构

```
Claude Code 会话 ──hook──▶ voice_hook.py ──unix socket──▶ tts_daemon.py ──afplay──▶ 声音输出
                              │                              │
                              │ 兜底: /usr/bin/say           │ 原子写入
                              ▼                              ▼
                     ~/.claude/voice_hook.log      ~/.claude/.voice_status.json
                                                             │ 250ms 轮询
                                                             ▼
                                                     voice-notch (SwiftUI)
```

### 队列语义

- 所有会话进同一个 FIFO，严格按入队顺序播放，一段播完才播下一段。
- 说话的会话变了才报项目目录名，同一会话连续播报不重复报。
- 在会话 A 里输入，只取消 A 的排队和播放，不动其他会话。
- 提示词里出现"不要说""闭嘴""stop talking"这类话：跳过正在播的那段
  （不论属于哪个会话），队列其余照常，且发令会话的下一条回复静音。
- 入队和 `ping` 都返回当前队列深度。

### 常驻服务

`tts_daemon.py` 把模型留在内存里。模型加载约 1 秒；首次合成还要下载音色、
建 jieba 词典（约 5 秒），这些在启动预热里做完。hook 通过 unix socket 发一条
JSON 就返回（毫秒级），Claude Code 不会被合成阻塞。socket 不通时，该句降级
用 `/usr/bin/say` 念，同时后台把服务拉起来。

### 按句流水线

按标点断句，上一句在播时合成下一句。第一块限 24 字（后续 60 字）以缩短开口
延迟。实测 117 字的回复切 3 块，首句 0.34 秒出声。下一段排队话语的音频在
当前段播放期间就已合成好，段间无空隙。

### 念什么

hook 只念最终回复里以 `🔊` 开头的行；没有标记就念清洗后的全文。配套做法是在
`~/.claude/CLAUDE.md` 里约定每条回复第一行写一句 `🔊` 摘要。

合成前的清洗：代码块 →"（一段代码）"；长路径只留文件名；URL、emoji、
markdown 符号、表格竖线删除；思考过程和干活途中的旁白不念，只念本轮最后
一次工具调用之后的文字。

### 指令回执

每次收到提示词，先念配置的前缀加指令第一小句（≤24 字），如「收到，帮我把
测试跑一遍」。既确认收到，也把语音识别听错的地方暴露出来。斜杠命令和闭嘴
口令不回执。

### 刘海状态条

`VoiceNotch.swift` 是单文件 SwiftUI 程序，在刘海下方画一块黑色面板，显示
正在播报的会话名、内容开头和排队数量。点击穿透，位于菜单栏之上，空闲时隐藏。
它读守护进程每次队列变化时原子重写（`os.replace`）的状态文件，250 毫秒轮询
mtime。`install.py` 检测到 `swiftc` 就编译；没有也不影响语音。守护进程启动时
自动拉起面板。

## 工程笔记

以下细节都不报错，全是静默的坏结果：

1. **macOS 会列出不存在的声音。** 语音包没装的声音合成出静音、退出码 0。
   判据：文件大小——静音恒约 4800 字节，真语音是六位数。
2. **`Stop` hook 和 transcript 落盘是竞态**，hook 常先跑、读到空回复。
   以 150 毫秒间隔重试，最多 900 毫秒。
3. **打断的账本必须活到播放结束。** 合成比播放快 10–18 倍；合成一完就删
   话语→会话映射，正在播的那段就查无此人，取消永远扑空。
4. **MOSS 输出立体声 `(N, 2)`。** `reshape(-1)` 把左右声道交错摊成双倍长的
   单声道——半速播放、严重失真。写文件前必须混成单声道。
5. **MOSS 官方参考音频是 FLAC 伪装成 `.wav`**，六个里三个解不开，转格式才行。
6. **spacy 按需下载模型会把进程带崩**（uv 管理的 venv 里它的 pip 子进程
   非零退出）。必须预装 `en_core_web_sm` 的 wheel。
7. **Kokoro 的中文 G2P 不带英文回调**，夹在中文里的英文被当中文注音。预热后
   打补丁 `ZHG2P(en_callable=...)`。
8. **别拿合成音频当克隆参考。** 参考本身是 TTS 的产物会劣化克隆——复印件再
   复印。要用真人录音。
9. **第一块限 24 字**（后续 60）：开口延迟只由第一块决定，越小越快。
10. **状态文件用 `os.replace` 写**，刘海面板永远读不到半截 JSON。Kokoro 八个
    中文音色里 `zf_xiaobei` 是辽宁话、`zf_xiaoni` 是陕西话，容易误选。

## 安装

需要 Apple Silicon 和 macOS。Python 3.13+ 目前没有依赖的 wheel，用 3.12。

```bash
git clone https://github.com/baryhuang/claude-code-voice-mlx
cd claude-code-voice-mlx

uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python \
    mlx-audio soundfile "misaki[zh,en]" \
    "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

python3 install.py
~/.claude/hooks/voice_hook.py --test
```

`install.py` 把两个 Python 文件拷到 `~/.claude/hooks/`、编译刘海面板、在
`~/.claude/settings.json`（用户级，所有项目生效）注册三个 hook；已有 hook
保留，旧配置备份为 `.bak`。hook 从 `~/.claude/hooks/` 运行，不依赖 clone
目录。Claude Code 每次事件都重读 hook 配置，运行中的会话无需重启。

卸载：`python3 install.py --uninstall`。

## Hooks

| 事件 | 行为 |
| --- | --- |
| `Stop` | 提取回复里的 `🔊` 行并入队 |
| `Notification` | 念出权限请求和空闲提示 |
| `UserPromptSubmit` | 取消本会话语音；回执指令；处理闭嘴口令 |

`SubagentStop` 直接返回不念。

## 配置

`~/.claude/voice_config.json`：

```json
{
  "enabled": true,
  "engine": "kokoro",
  "tts_model": "kokoro",
  "model": "mlx-community/Kokoro-82M-bf16",
  "kokoro_voice": "zf_xiaoxiao",
  "moss_ref_audio": "~/.claude/voice_ref.wav",
  "speed": 1.0,
  "max_chars": 700,
  "announce_session": true,
  "ack_on_prompt": true,
  "ack_prefix": "收到",
  "speak_notifications": true
}
```

`tts_model: "moss"` 切到 [MOSS-TTS-Nano-100M](https://huggingface.co/mlx-community/MOSS-TTS-Nano-100M)
（OpenMOSS，2026）：克隆型模型，用 `moss_ref_audio` 指向的参考音频的声音说话
（5–10 秒干净的单人录音；预置音色没有的口音——比如台湾腔——只有这条路）。
中英混排原生支持，48 kHz，M3 Pro 上约 9 倍实时。注意它输出**立体声** `(N, 2)`，
直接摊平会声道交错、半速失真，守护进程已做单声道混音。参考文件不存在时自动
退回 Kokoro。默认仍是 Kokoro：24 kHz 单声道、预置音色、稍快。

`engine: "say"` 完全绕过守护进程。超过 `max_chars` 的回复在句号处截断并念一句
提示。

### 音色

Kokoro 带 8 个中文音色：

| 音色 | 说明 |
| --- | --- |
| `zf_xiaoxiao`（默认）、`zf_xiaoyi` | 标准普通话，女声 |
| `zm_yunxi`、`zm_yunyang`、`zm_yunjian`、`zm_yunxia` | 标准普通话，男声 |
| `zf_xiaobei` | 辽宁方言口音 |
| `zf_xiaoni` | 陕西方言口音 |

英文音色（`af_heart`、`am_michael` 等）需 `lang_code` 设为 `"a"`。

## 限制

- 只有输出。权限确认和选项问题仍需键盘，没有语音应答通道。
- 断网时回合不结束，`Stop` 不触发，没有任何声音；没有卡死心跳报警。
- 回执和其他内容走同一个 FIFO，别的会话在播时回执要排队，没有优先通道。
- 长命令执行期间没有进度播报，除非权限通知触发。
- Mac 休眠即停，守护进程不阻止休眠。
- `🔊` 约定依赖模型遵守 CLAUDE.md 指令，不遵守就念清洗后的全文，会很长。
- 仅支持 macOS 和 Apple Silicon。

## 排障

### `say` 退出码 0 但没有声音

用 `--voices` 自检（原理见工程笔记第 1 条）。缺的声音在
系统设置 → 辅助功能 → 朗读内容 → 系统声音 → 管理声音 里下载。

### `No module named 'misaki'`

中文合成需要 `misaki[zh]`，且调用必须显式传 `lang_code="z"`。

### 中文句子里的英文单词念得乱

工程笔记第 6、7 条：预装 spacy 模型 + 英文回调补丁，安装命令里已包含。

### 回合结束偶尔不念

工程笔记第 2 条的竞态，已用重试兜住。`~/.claude/voice_hook.log` 记录每次
事件读到的字数。

### 日志

```bash
tail -f ~/.claude/voice_hook.log          # 每次事件：类型、会话、字数
tail -f ~/.claude/voice_tts_daemon.log    # 队列变化、首句延迟
```

## 测量数据

M3 Pro，36 GB，Kokoro-82M bf16，热启动：

| 输入 | 合成耗时 | 音频时长 | 实时倍率 |
| --- | --- | --- | --- |
| 12 字 | 0.30 s | 3.20 s | 10.6× |
| 28 字 | 0.40 s | 6.95 s | 17.4× |
| 60 字 | 0.81 s | 14.55 s | 18.1× |

模型磁盘占用约 340 MB，常驻内存约 200 MB。

### 评估过的模型

| 模型 | 实时倍率（本机） | 结论 |
| --- | --- | --- |
| Kokoro-82M | 10–18× | 默认引擎 |
| MOSS-TTS-Nano-100M | ~9× | 克隆引擎（`tts_model: "moss"`） |
| Qwen3-TTS 1.7B VoiceDesign 8-bit | 1.2× | 弃：太慢 |
| BlueMagpie-TTS（台湾口音） | ~1.3×（MLX，引自上游文档） | 弃：太慢，8 GB |
| macOS `say` | 即时流式 | 仅作兜底 |

取舍标准：低于约 5 倍实时，队列积压会变成可听见的起播延迟，破坏顺序队列的
设计初衷。

## 文件

| 文件 | 职责 |
| --- | --- |
| `voice_hook.py` | hook 入口：transcript 解析、清洗、会话路由、`say` 兜底 |
| `tts_daemon.py` | 常驻合成服务：FIFO 队列、按句流水线、按会话取消 |
| `VoiceNotch.swift` | 刘海状态面板 |
| `install.py` | 拷贝到 `~/.claude/hooks/`、编译面板、注册 hook |

## License

MIT
