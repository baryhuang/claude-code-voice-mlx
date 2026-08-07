# claude-code-voice-mlx

**Voice output for parallel Claude Code sessions.**
Run five agents at once and listen to them report back — in order, one at a time,
each announcing which project it is.

Local neural text-to-speech on Apple MLX. 0.3 s to first sound, fully offline,
no API keys.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-M1--M4-black?logo=apple)
![Offline](https://img.shields.io/badge/100%25-offline-green)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)

---

## The problem it solves

Parallel agents are now normal. Multiple Claude Code sessions, one per git
worktree, one per feature, all grinding at once. Nobody can watch five terminals.

Bolting text-to-speech onto that makes it worse, not better: every session
speaks the moment it finishes, so they talk over each other, and each new reply
cuts off whatever was mid-word. The result is a pile of half-sentences from
unidentified sources.

**This project makes concurrent agents sound like one person reporting to you.**

```
"database migration finished, no errors."             ← backend session
"frontend — build failed, a dependency is missing."   ← name announced on switch
"backend — tests pass too, ready to merge."           ← and again on switch back
```

One global queue. Utterances finish before the next begins. The project name is
spoken only when the speaker changes, so a single active session never gets
chatty.

---

## Core design

### One global queue across every session

The daemon is a single process, which makes it the natural serialization point.
Every session pushes into one FIFO; playback runs strictly in order.

- **No overlap.** An utterance plays to completion before the next starts.
- **No truncation.** A newly finished session waits its turn instead of cutting in.
- **Source identification.** The project directory name is announced on speaker
  change, and suppressed while one session keeps the floor.
- **Per-session barge-in.** Typing into session A cancels *A's* queued audio.
  Session B keeps talking, because B was not interrupted.

Queue depth is returned on every enqueue and visible via `ping`, so backlog is
observable rather than guessed at.

### Resident model, non-blocking hook

Kokoro loads in ~1 s. Paying that per reply is worse than a robotic built-in
voice. The daemon holds the model in memory; the hook pushes a line over a unix
socket and returns in **0.00 s**, so Claude Code is never blocked.

If the daemon is not running, the hook starts it in the background and falls
back to macOS `say` for that one line. Silence is never a possible outcome.

### Sentence pipelining

Synthesizing an entire reply before playback means ten seconds of dead air. Text
is split into sentences; each is synthesized and queued while the previous one
plays. The first chunk is capped shorter than the rest to win the
time-to-first-sound race.

```
117 chars → 3 chunks → first audio at 0.34 s
```

Pipelining crosses utterance boundaries: the next session's audio is already
synthesized and waiting when the current one finishes, so the queue introduces
no gap.

### Speak the summary, not the reply

Listening is roughly 10× slower than reading. A reply that scans in five seconds
takes a minute to hear. The agent writes one `🔊` line and **only that line is
spoken**; the screen keeps the full detail.

```markdown
🔊 Tests pass, deployed to staging. Want me to promote it?

## Details
...full markdown, code blocks, tables — none of it is read aloud...
```

Add this to `~/.claude/CLAUDE.md`:

```markdown
Every reply must start with a 🔊 line: one sentence, under 50 characters,
plain spoken language, no code or paths. Only that line is read aloud.
```

Without a `🔊` line it falls back to speaking the cleaned full text.

---

## Quick start

Requires **Apple Silicon** (M1–M4) and macOS.

```bash
git clone https://github.com/baryhuang/claude-code-voice-mlx
cd claude-code-voice-mlx

# 1. TTS environment (Python 3.12 — 3.13+ has no wheels yet)
uv venv ~/.claude/voice-tts --python 3.12
uv pip install --python ~/.claude/voice-tts/bin/python \
    mlx-audio soundfile "misaki[zh,en]"

# 2. Register the hooks globally
python3 install.py

# 3. Listen
~/.claude/hooks/voice_hook.py --test
```

Every Claude Code session on the machine now talks. Hook changes are picked up
live — no restart needed. Uninstall with `python3 install.py --uninstall`.

---

## Hooks

| Event | Behavior |
| --- | --- |
| `Stop` | Reply finished → extract the `🔊` line → enqueue for playback |
| `Notification` | "needs your permission to use Bash" → spoken, so no blind waiting |
| `UserPromptSubmit` | Cancels that session's audio only |

---

## Comparison

| | This project | Typical Claude Code TTS plugin |
| --- | --- | --- |
| **Concurrent sessions** | **Global queue, ordered, source announced** | Sessions talk over each other |
| **Barge-in scope** | Per session | Global, or none |
| **Time to first sound** | **0.3 s** — resident daemon + pipelining | 3–8 s — model loaded per call |
| **What is spoken** | Only the `🔊` summary line | The entire reply, markdown included |
| **Chinese** | 8 Mandarin voices, correct `lang_code` + `misaki[zh]` g2p | Often English-only or broken |
| **Cost / network** | Zero, fully offline | OpenAI / ElevenLabs API key |

---

## Configuration

`~/.claude/voice_config.json`:

```json
{
  "enabled": true,
  "engine": "kokoro",
  "model": "mlx-community/Kokoro-82M-bf16",
  "kokoro_voice": "zf_xiaoxiao",
  "speed": 1.0,
  "max_chars": 700,
  "announce_session": true
}
```

Set `announce_session` to `false` to suppress project-name announcements.

### Voices

| Voice | Note |
| --- | --- |
| `zf_xiaoxiao` *(default)* | standard Mandarin, female |
| `zf_xiaoyi` | standard Mandarin, female |
| `zm_yunxi` `zm_yunyang` `zm_yunjian` `zm_yunxia` | standard Mandarin, male |
| `zf_xiaobei` | ⚠️ **Liaoning dialect** — distinctly regional |
| `zf_xiaoni` | ⚠️ **Shaanxi dialect** |

English voices (`af_heart`, `am_michael`, …) work with `lang_code` set to `a`.

---

## Text cleaning

Raw markdown read aloud is unusable. Before synthesis:

- fenced code blocks → "(a code block)"
- `/Users/you/git/project/src/thing.py` → `thing.py`
- URLs, emoji, `**`, `##`, `-`, table pipes → removed
- thinking blocks and mid-task tool commentary → never spoken; only the final reply

---

## Troubleshooting

### `say` exits 0 but there is no sound

macOS lists Chinese voices whose voice packs were never downloaded. They **exit 0
and emit silence**, so nothing appears wrong. Detect it by synthesized file size:

```bash
~/.claude/hooks/voice_hook.py --voices
```

```
Tingting   113760 bytes  ✅
Meijia     120606 bytes  ✅
Sinji      118574 bytes  ✅
Sandy        4800 bytes  ❌ voice pack not installed
Eddy         4800 bytes  ❌
```

Silence is a constant ~4800 bytes; real speech is six figures. Install the rest
under *System Settings → Accessibility → Spoken Content → System Voice → Manage Voices*.

### `No module named 'misaki'`

Chinese requires the grapheme-to-phoneme package, and `lang_code="z"` must be
passed explicitly or Kokoro loads the English g2p and fails:

```bash
uv pip install --python ~/.claude/voice-tts/bin/python "misaki[zh,en]"
```

### Nothing is spoken at the end of a turn

The `Stop` hook and the transcript write race each other; the hook frequently
runs before the final line is flushed. Handled by retrying for up to 900 ms.

### Logs

```bash
tail -f ~/.claude/voice_hook.log          # event, session, character count
tail -f ~/.claude/voice_tts_daemon.log    # queue depth, first-chunk latency
```

---

## Benchmarks

Apple M3 Pro, 36 GB, Kokoro-82M bf16, warm:

| Input | Synthesis | Audio | Real-time factor |
| --- | --- | --- | --- |
| 12 chars | 0.30 s | 3.20 s | 10.6× |
| 28 chars | 0.40 s | 6.95 s | 17.4× |
| 60 chars | 0.81 s | 14.55 s | 18.1× |

Resident memory ≈ 200 MB. Model on disk ≈ 340 MB.

### Model selection

| Model | Real-time factor | Status |
| --- | --- | --- |
| **Kokoro-82M** | **10–18×** | shipped |
| Qwen3-TTS 1.7B VoiceDesign | 1.2× | strong Chinese and describable voices, too slow |
| [BlueMagpie-TTS](https://github.com/OpenFormosa/BlueMagpie-TTS) | 1.3× (MLX) | best Taiwanese-accent model available, 8 GB, too slow |
| macOS `say` | instant | robotic; retained as cold-start fallback |

A queue only sounds natural when each item starts promptly. Real-time factor
below roughly 5× makes backlog audible, which rules out the larger models for
interactive use today.

---

## Files

| File | Role |
| --- | --- |
| `voice_hook.py` | Hook entry point — transcript parsing, cleaning, session routing, `say` fallback |
| `tts_daemon.py` | Resident Kokoro server — global queue, sentence pipelining, per-session cancel |
| `install.py` | Copies both into `~/.claude/hooks/` and registers the hooks |

Hooks are installed **into `~/.claude/hooks/`, not run from the clone** — a global
hook should not break when a directory moves.

---

## 中文说明

并行跑 agent 已经是常态：多个 Claude Code 会话，一个 worktree 一个，同时开工。
没人看得过来五个终端。

直接加语音只会更糟——每个会话一说完就抢着播，互相打断，听到的是一堆半截话，
还不知道是哪个项目在说。

**这个项目让并行的 agent 听起来像一个人在向你汇报。**

全局一个队列，一句说完再说下一句；换会话时先报项目名，同一个会话连着说就不报。
打断也是分会话的——你在 A 会话开口，只掐 A 的语音，B 继续说完。

引擎是 Kokoro 神经网络模型跑在苹果 MLX 上，**开口 0.3 秒，全程离线，不花钱**。

中文音色八个，默认晓晓。注意 `zf_xiaobei` 是辽宁方言、`zf_xiaoni` 是陕西方言。

最大的坑：**macOS 列出来的中文声音大部分语音包没装，合成出来是静音，退出码还是 0**。
用 `--voices` 自检，静音恒定 4800 字节。

---

## Keywords

parallel Claude Code sessions · multi-agent voice output · concurrent AI agents ·
agent orchestration audio feedback · speech queue for multiple sessions ·
Claude Code TTS hook · local text-to-speech macOS · Apple MLX text-to-speech ·
Kokoro TTS Chinese · offline neural TTS Apple Silicon · git worktree parallel agents ·
hands-free coding · eyes-free programming · developer accessibility ·
中文语音合成 · 本地 TTS · 多会话语音播报 · 并行 agent 语音

## License

MIT
