# claude-code-voice-mlx

**Hear Claude Code speak. Locally, in Chinese or English, 0.3 s to first sound.**

A text-to-speech (TTS) hook for [Claude Code](https://claude.com/claude-code) that
reads its replies out loud using a neural voice running on Apple MLX — so you can
code with your eyes off the screen. No API keys, no network, no per-character billing.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-M1--M4-black?logo=apple)
![Offline](https://img.shields.io/badge/100%25-offline-green)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)

> Voice input already works everywhere — dictation, Whisper, Wispr Flow. Voice
> *output* is the missing half. Without it you still have to look at the screen,
> and the whole point of talking to your agent is lost.

---

## Why this one

There are already several Claude Code TTS plugins. This one is different in three ways:

| | This project | Typical alternative |
| --- | --- | --- |
| **Time to first sound** | **0.3 s** — resident daemon + sentence pipelining | 3–8 s — model loaded per call |
| **What gets spoken** | Only the `🔊` summary line you write | The entire reply, markdown and all |
| **Chinese** | First-class, 8 Mandarin voices, correct `lang_code` + `misaki[zh]` g2p | Often English-only or broken |
| **Cost / network** | Zero, fully offline | OpenAI / ElevenLabs API key |

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

That's it. Every Claude Code session on the machine now talks. Hook changes are
picked up live — no restart needed.

To uninstall: `python3 install.py --uninstall`

---

## How it works

Three hooks, one resident model:

| Hook event | What happens |
| --- | --- |
| `Stop` | Claude finished a reply → extract the `🔊` line → speak it |
| `Notification` | "needs your permission to use Bash" → speak it, so you're not waiting blind |
| `UserPromptSubmit` | You start talking → cut the audio mid-sentence |

### Design decision 1 — a resident daemon

Kokoro loads in ~1 s. Doing that on every reply is worse than the robotic
built-in voice. So `tts_daemon.py` stays resident with the model in memory, and
the hook just pushes a line of text over a unix socket and returns in **0.00 s**
— Claude Code is never blocked.

If the daemon isn't up, the hook starts it in the background **and falls back to
macOS `say` for that one line**. You never get silence.

### Design decision 2 — sentence pipelining

Synthesizing a whole reply before playing it means 10+ seconds of dead air.
Instead the text is split into sentences, each synthesized and queued as the
previous one plays. The first chunk is capped shorter than the rest specifically
to win the time-to-first-sound race.

```
117 chars → 3 chunks → first audio at 0.34 s
```

### Design decision 3 — speak the summary, not the reply

Listening is ~10× slower than reading. A reply that scans in 5 seconds takes a
minute to hear. So the agent writes one `🔊` line and **only that line is
spoken** — the screen keeps the full detail.

```markdown
🔊 Tests pass, deployed to staging. Want me to promote it?

## Details
...full markdown, code blocks, tables — none of it is read aloud...
```

Add this to your `~/.claude/CLAUDE.md` so the agent actually writes that line:

```markdown
Every reply must start with a 🔊 line: one sentence, under 50 characters,
plain spoken language, no code or paths. Only that line is read aloud.
```

No `🔊` line? It falls back to speaking the cleaned full text, so nothing breaks.

---

## Text cleaning

Raw markdown read aloud is unbearable. Before synthesis:

- fenced code blocks → "(a code block)"
- `/Users/you/git/project/src/thing.py` → `thing.py`
- URLs, emoji, `**`, `##`, `-`, table pipes → gone
- thinking blocks and mid-task tool commentary → never spoken, only the final reply

---

## Voices

Eight Mandarin voices ship with Kokoro:

| Voice | Note |
| --- | --- |
| `zf_xiaoxiao` *(default)* | standard Mandarin, female |
| `zf_xiaoyi` | standard Mandarin, female |
| `zm_yunxi` `zm_yunyang` `zm_yunjian` `zm_yunxia` | standard Mandarin, male |
| `zf_xiaobei` | ⚠️ **Liaoning dialect** — sounds distinctly regional |
| `zf_xiaoni` | ⚠️ **Shaanxi dialect** |

Set in `~/.claude/voice_config.json`:

```json
{
  "enabled": true,
  "engine": "kokoro",
  "model": "mlx-community/Kokoro-82M-bf16",
  "kokoro_voice": "zf_xiaoxiao",
  "speed": 1.0,
  "max_chars": 700
}
```

English voices (`af_heart`, `am_michael`, …) work too — set `lang_code` to `a`.

---

## Troubleshooting

### `say` runs fine but there is no sound

macOS lists Chinese voices that were never actually downloaded. They **exit 0 and
emit silence** — the nastiest failure mode there is, because nothing looks wrong.

Measure the synthesized file size instead:

```bash
~/.claude/hooks/voice_hook.py --voices
```

```
Tingting   113760 bytes  ✅
Meijia     120606 bytes  ✅
Sinji      118574 bytes  ✅
Sandy        4800 bytes  ❌ voice pack not installed
Eddy         4800 bytes  ❌
...
```

Silence is a constant ~4800 bytes; real speech is six figures. Download the rest
under *System Settings → Accessibility → Spoken Content → System Voice → Manage Voices*.

### Kokoro errors with `No module named 'misaki'`

Chinese needs the grapheme-to-phoneme package, and the call must pass
`lang_code="z"` explicitly — otherwise Kokoro loads the English g2p and dies:

```bash
uv pip install --python ~/.claude/voice-tts/bin/python "misaki[zh,en]"
```

### It speaks an empty reply / stays silent at the end of a turn

The `Stop` hook and the transcript write are a race — the hook often runs before
the final line is flushed to disk. Handled by retrying for up to 900 ms.

### Logs

```bash
tail -f ~/.claude/voice_hook.log          # hook: which event, how many chars
tail -f ~/.claude/voice_tts_daemon.log    # daemon: model load, first-chunk latency
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

### Models evaluated and rejected

| Model | Real-time factor | Verdict |
| --- | --- | --- |
| **Kokoro-82M** | **10–18×** | ✅ shipped |
| Qwen3-TTS 1.7B VoiceDesign | 1.2× | Great Chinese, describable voices, far too slow |
| BlueMagpie-TTS (Taiwanese accent) | 1.3× (MLX) | Best Taiwanese-accent model available; 8 GB and too slow |
| macOS `say` | instant | Robotic; kept only as the cold-start fallback |

No open model currently ships a **Taiwanese-accent** Mandarin preset. The routes
are voice cloning or [BlueMagpie-TTS](https://github.com/OpenFormosa/BlueMagpie-TTS),
both of which cost more latency than an interactive coding loop can absorb today.

---

## Files

| File | Role |
| --- | --- |
| `voice_hook.py` | Hook entry point — transcript parsing, cleaning, routing, `say` fallback |
| `tts_daemon.py` | Resident Kokoro server — sentence pipelining, playback queue, barge-in |
| `install.py` | Copies both into `~/.claude/hooks/` and registers the hooks |

The hook is installed **into `~/.claude/hooks/`, not run from the clone** — a
global hook should not break because you moved a directory.

---

## 中文说明

语音输入早就够用了，缺的是语音输出。听不到就还得盯屏幕，那跟语音助手对话就没意义了。

这是给 Claude Code 装的本地语音播报，用 Kokoro 神经网络模型跑在苹果 MLX 上，
**开口 0.3 秒，全程离线，不花一分钱**。

三个设计要点：

1. **常驻服务** — 模型待在内存里，hook 通过 unix socket 丢一句话就返回，不拖慢 Claude
2. **按句流水线** — 边合成边播，首句 0.34 秒出声，跟整段多长无关
3. **只念摘要** — 回复第一行标 `🔊`，只念那一行，屏幕上保留全部细节

安装看上面 [Quick start](#quick-start)。中文音色八个，默认晓晓；
注意 `zf_xiaobei` 是辽宁方言、`zf_xiaoni` 是陕西方言，别选错。

最大的坑：**macOS 列出来的中文声音大部分语音包没装，合成出来是静音，而且退出码是 0**。
用 `--voices` 自检，静音恒定 4800 字节。

---

## Keywords

Claude Code text-to-speech · Claude Code TTS hook · local TTS macOS ·
Apple MLX text-to-speech · Kokoro TTS Chinese · offline neural TTS Apple Silicon ·
hands-free coding · eyes-free programming · accessibility for developers ·
voice coding assistant · 中文语音合成 · 本地 TTS · 语音编程

## License

MIT
