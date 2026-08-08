# claude-code-voice-mlx

**Claude Code talks on your Mac.** Text-to-speech output for Claude Code on
macOS / Apple Silicon. Replies are synthesized by
Kokoro-82M running locally on Apple MLX and played through the system audio
output. Concurrent sessions share one playback queue: utterances play
sequentially, the project name is announced when the speaking session changes,
and cancelling one session's audio does not affect the others.

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
- No Taiwanese-accent voice. None of the evaluated open models ships one as a
  preset (see model notes below); the available routes are voice cloning or
  BlueMagpie-TTS, both too slow for interactive use on this hardware.
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
| Kokoro-82M | 10–18× | in use |
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
| `tts_daemon.py` | Resident Kokoro server: FIFO queue, sentence pipelining, per-session cancel |
| `VoiceNotch.swift` | Notch status panel |
| `install.py` | Copy to `~/.claude/hooks/`, compile panel, register hooks |

---

## 中文说明

在 Mac 上给 Claude Code 加语音输出：回复由本地 MLX 上的 Kokoro-82M 合成播放，
模型下载后不需要网络。

多个会话共用一个播放队列，按入队顺序一段一段播完；换会话时报一次项目目录名，
同一会话连续播报不重复报。在某个会话里输入，只取消该会话的语音。说"不要说"
"闭嘴"这类话会跳过正在播的那段，队列里其余的照常播，且该会话的下一条回复静音。

每条回复只念开头标了 `🔊` 的那一行（需在 CLAUDE.md 里约定），没有标记就念清洗
后的全文。收到指令时会复述指令开头几个字作为回执，顺带暴露语音识别错误。

刘海下有一个状态条，显示正在播报的会话、内容开头和排队数量，空闲时隐藏，
点击穿透。

实测（M3 Pro）：回复结束后约 0.3 秒出声，合成速度为实时的 10–18 倍，
常驻内存约 200 MB。

已知限制：权限确认和选项问题仍需键盘；断网时回合不结束、不会有任何声音；
没有台湾口音音色（评估过的开源模型都没有现成的，克隆或 BlueMagpie 在这台
机器上延迟不可用）；Mac 休眠即停。

macOS 的一个坑：系统列出的中文声音多数没装语音包，合成"成功"但输出静音，
退出码为 0。用 `--voices` 检测——静音恒为 4800 字节左右，正常语音是六位数。

## License

MIT
