#!/usr/bin/env python3
"""Kokoro TTS 常驻服务。

为什么要常驻：模型加载要好几秒，每次念之前现加载比 say 还难受。
所以模型一直待在内存里，hook 只通过 unix socket 丢一句文字过来。

为什么按句切：整段合成完再播，开口前要干等十几秒。切成句子后，
第一句（十几个字）半秒就出来了，边播边合成后面的，开口延迟只跟第一句有关。

为什么要排队：多个 Claude 会话同时干活时，如果后来的掐掉前面的，
你会听到一堆半截话。所以这里是全局的串行点——所有会话共用一个队列，
一句一句说完，换会话时先报一下是哪个项目在说话。

协议（unix socket，一行一个 JSON）：
    {"cmd": "speak", "text": "...", "session": "...", "label": "项目名"}
    {"cmd": "stop", "session": "..."}    # 不带 session 就是全停
    {"cmd": "ping"}    -> {"ok": true, "model": ..., "queue": 队列长度}
"""

import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HOME = os.path.expanduser("~")
SOCK_PATH = os.path.join(HOME, ".claude", ".voice_tts.sock")
LOG_PATH = os.path.join(HOME, ".claude", "voice_tts_daemon.log")
CONFIG_PATH = os.path.join(HOME, ".claude", "voice_config.json")
STATUS_PATH = os.path.join(HOME, ".claude", ".voice_status.json")
NOTCH_BIN = os.path.join(HOME, ".claude", "hooks", "voice-notch")

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "zf_xiaoxiao"
DEFAULT_SPEED = 1.0
DEFAULT_LANG = "z"          # Kokoro 的中文语种码，不传会去加载英文 g2p 然后报错
TARGET_PEAK = 0.9           # Kokoro 输出峰值只有 0.3 左右，比 say 明显轻，要拉齐

MAX_LOG = 256 * 1024


def log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG:
            with open(LOG_PATH) as f:
                keep = f.readlines()[-400:]
            with open(LOG_PATH, "w") as f:
                f.writelines(keep)
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


MOSS_MODEL = "mlx-community/MOSS-TTS-Nano-100M"
MOSS_REF = os.path.join(HOME, ".claude", "voice_ref.wav")


def load_config():
    cfg = {"model": DEFAULT_MODEL, "kokoro_voice": DEFAULT_VOICE,
           "speed": DEFAULT_SPEED, "lang_code": DEFAULT_LANG,
           "announce_session": True,
           # tts_model: kokoro（预置音色）或 moss（用 voice_ref.wav 克隆音色）
           "tts_model": "kokoro",
           "moss_ref_audio": MOSS_REF}
    try:
        with open(CONFIG_PATH) as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
    except Exception:
        pass
    return cfg


# --------------------------------------------------------------------------
# 断句：目标是让第一句尽量短，好快点开口
# --------------------------------------------------------------------------

HARD_BREAK = "。！？!?\n；;"
SOFT_BREAK = "，,、：:—"


def split_sentences(text, first_max=24, chunk_max=60):
    """先按句号切，太长的再按逗号切。第一块单独限得更短，抢开口速度。"""
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in HARD_BREAK:
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())

    out = []
    for p in parts:
        limit = first_max if not out else chunk_max
        while len(p) > limit:
            cut = -1
            for i in range(min(limit, len(p) - 1), max(limit // 2, 1), -1):
                if p[i] in SOFT_BREAK:
                    cut = i + 1
                    break
            if cut == -1:
                cut = limit
            out.append(p[:cut].strip())
            p = p[cut:].strip()
            limit = chunk_max
        if p:
            out.append(p)
    return [s for s in out if s]


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.sample_rate = 24000

        self.utt_q = queue.Queue()      # 待朗读的整段话
        self.play_q = queue.Queue()     # 已合成好的音频片段
        self.lock = threading.Lock()
        self.next_id = 0
        self.utt_session = {}           # utt_id -> session，用来定向取消
        self.utt_meta = {}              # utt_id -> (label, 文字开头)，给刘海状态条用
        self.pending = []               # 排队顺序（含正在播的），驱动状态显示
        self.cancelled = set()
        self.player = None
        self.playing_utt = None
        self.last_session = None        # 换会话时才报项目名

        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # -- 模型 ------------------------------------------------------------
    def load(self):
        t0 = time.time()
        import warnings, logging
        warnings.filterwarnings("ignore")          # jieba/torch 一堆噪音
        logging.getLogger("jieba").setLevel(logging.ERROR)
        from mlx_audio.tts.utils import load_model
        # 选 moss 但参考音频不存在时退回 kokoro——克隆模型没有参考就没有声音
        if self.cfg["tts_model"] == "moss" and not os.path.exists(self.cfg["moss_ref_audio"]):
            log(f"moss 参考音频不存在 {self.cfg['moss_ref_audio']}，退回 kokoro")
            self.cfg["tts_model"] = "kokoro"
        model_id = MOSS_MODEL if self.cfg["tts_model"] == "moss" else self.cfg["model"]
        self.model = load_model(model_id)
        log(f"模型加载完成 {model_id} 耗时 {time.time() - t0:.1f}s")
        # 冷启动第一次要下载音色文件、建 jieba 词典，约 7 秒。
        # 在这里先跑一次，别让用户的第一句话吃到这个延迟。
        try:
            self._synth("预热")
            log(f"预热完成，累计 {time.time() - t0:.1f}s，可以接活了")
        except Exception as e:
            log(f"预热失败 {e!r}")
        self._patch_english()

    def _patch_english(self):
        """给中文管线挂上英文回调。

        mlx-audio 建 ZHG2P 时不传 en_callable，混在中文里的英文单词
        （GitHub、pytest 这类）会被中文字音转换硬啃成乱音。misaki 本身
        支持英文回调，挂上英文 G2P 就能正常发音。需要 spacy 的
        en_core_web_sm——注意不能让 spacy 自己去下载，它会调 uv 然后
        把整个进程带崩，必须预先装好。"""
        if self.cfg["tts_model"] != "kokoro" or self.cfg["lang_code"] != "z":
            return                        # moss 是 LM 模型，混排天生没问题
        try:
            from misaki import zh, en
            eng = en.G2P(trf=False, british=False, fallback=None, unk="")
            pipe = self.model._pipelines.get("z")
            if pipe is None:
                log("英文回调：z 管线不存在，跳过")
                return
            pipe.g2p = zh.ZHG2P(en_callable=lambda t: eng(t)[0])
            log("英文回调已挂上，中英混排可用")
        except Exception as e:
            log(f"英文回调挂载失败（混排里的英文会难听，但不影响中文）: {e!r}")

    def _synth(self, text):
        """返回 float32 numpy 波形。不同模型返回结构不一样，这里做兼容。"""
        import numpy as np
        if self.cfg["tts_model"] == "moss":
            results = list(self.model.generate(text=text,
                                               ref_audio=self.cfg["moss_ref_audio"]))
        else:
            kwargs = dict(text=text, voice=self.cfg["kokoro_voice"],
                          speed=self.cfg["speed"], lang_code=self.cfg["lang_code"])
            try:
                results = list(self.model.generate(**kwargs))
            except TypeError:  # 别的模型可能不认 lang_code / speed
                results = list(self.model.generate(text=text, voice=self.cfg["kokoro_voice"]))
        if not results:
            return None
        sr = getattr(results[0], "sample_rate", None)
        if sr:
            self.sample_rate = int(sr)
        parts = []
        for seg in results:
            audio = np.asarray(getattr(seg, "audio", seg), dtype="float32")
            if audio.ndim == 2:
                # MOSS 输出 48kHz 立体声 (N, 2)。直接 reshape(-1) 会把左右声道
                # 交错摊平成双倍长度的"单声道"——听感就是半速播放加严重扭曲。
                # 必须先混成单声道。
                audio = audio.mean(axis=-1) if audio.shape[-1] <= 2 else audio.mean(axis=0)
            parts.append(audio.reshape(-1))
        wav = np.concatenate(parts) if len(parts) > 1 else parts[0]
        peak = float(abs(wav).max()) if len(wav) else 0.0
        if peak > 1e-6:
            wav = wav * (TARGET_PEAK / peak)
        return wav

    # -- 状态文件：给刘海上的 voice-notch 看的 ----------------------------
    def write_status(self):
        try:
            with self.lock:
                cur = self.playing_utt
                speaking = None
                if cur is not None and cur in self.utt_meta:
                    lbl, txt = self.utt_meta[cur]
                    speaking = {"label": lbl or "", "text": txt}
                waiting = [
                    {"label": (self.utt_meta.get(u) or ("", ""))[0]}
                    for u in self.pending
                    if u != cur and u not in self.cancelled
                ]
            tmp = STATUS_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"speaking": speaking, "queue": waiting, "ts": time.time()}, f,
                          ensure_ascii=False)
            os.replace(tmp, STATUS_PATH)   # 原子替换，读的一方永远看不到半截 JSON
        except Exception as e:
            log(f"写状态失败 {e!r}")

    # -- 入队 ------------------------------------------------------------
    def enqueue(self, text, session, label):
        """排到队尾。不打断正在说的那句——多会话时那样会变成一堆半截话。"""
        text = (text or "").strip()
        if not text:
            return 0
        with self.lock:
            self.next_id += 1
            utt_id = self.next_id
            self.utt_session[utt_id] = session
            self.utt_meta[utt_id] = (label or "", text[:24])
            self.pending.append(utt_id)
        self.utt_q.put((utt_id, session, label, text))
        depth = self.utt_q.qsize()
        log(f"入队 #{utt_id} [{label}] {len(text)}字，队列深度 {depth}")
        self.write_status()
        return depth

    def stop(self, session=None):
        """取消某个会话的朗读。不给 session 就全停。

        只掐自己那一路：你在 A 会话敲键盘，不该让 B 会话的播报也断掉。
        """
        with self.lock:
            if session is None:
                victims = set(self.utt_session)
            else:
                victims = {u for u, s in self.utt_session.items() if s == session}
            self.cancelled |= victims
            hit_current = self.playing_utt in victims
        p = self.player
        if hit_current and p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        if victims:
            log(f"取消 {len(victims)} 段（session={session or '全部'}）")
            self.write_status()

    def skip_current(self):
        """只掐正在播的这一段，队列里后面的照常接着播。

        用户喊"闭嘴"针对的是耳朵里这段，不是整个队列——别的会话
        排队等着的汇报他还要听。
        """
        with self.lock:
            cur = self.playing_utt
            if cur is not None:
                self.cancelled.add(cur)
        p = self.player
        if cur is not None and p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        if cur is not None:
            log(f"跳过正在播的 #{cur}，队列继续")
        return cur

    def _is_dead(self, utt_id):
        with self.lock:
            return utt_id in self.cancelled

    # -- 合成线程 --------------------------------------------------------
    def _synth_loop(self):
        while True:
            utt_id, session, label, text = self.utt_q.get()
            if self._is_dead(utt_id):
                self._finish_playback(utt_id)
                continue
            try:
                self._synth_utterance(utt_id, session, label, text)
            except Exception as e:
                log(f"合成整段失败 #{utt_id} {e!r}")
            # 哨兵：这一段的音频到此为止，播放线程见到它才收尾清账
            self.play_q.put((utt_id, None))

    def _synth_utterance(self, utt_id, session, label, text):
        chunks = split_sentences(text)
        # 换了会话才报项目名，一直是同一个会话就别啰嗦
        if (self.cfg.get("announce_session") and label
                and self.last_session is not None and session != self.last_session):
            chunks.insert(0, f"{label}，")
        self.last_session = session

        t0 = time.time()
        for i, chunk in enumerate(chunks):
            if self._is_dead(utt_id):
                log(f"#{utt_id} 合成中被取消")
                return
            wav = self._synth(chunk)
            if wav is None or not len(wav):
                continue
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_")
            os.close(fd)
            import soundfile as sf
            sf.write(path, wav, self.sample_rate)
            if i == 0:
                log(f"#{utt_id} 首句就绪 {time.time() - t0:.2f}s")
            self.play_q.put((utt_id, path))

    def _finish_playback(self, utt_id):
        """整段彻底结束（播完或取消）才清账。

        utt_session 必须留到这里才能删：合成比播放快十几倍，要是合成一结束
        就清掉，用户开口打断时按会话找目标就永远扑空——正在播的那段反而杀不掉。
        """
        with self.lock:
            if utt_id in self.pending:
                self.pending.remove(utt_id)
            self.utt_session.pop(utt_id, None)
            self.utt_meta.pop(utt_id, None)
            self.cancelled.discard(utt_id)
        self.write_status()

    # -- 播放线程 --------------------------------------------------------
    def _play_loop(self):
        while True:
            utt_id, path = self.play_q.get()
            if path is None:                    # 这一段播完了
                if self.playing_utt == utt_id:
                    self.playing_utt = None
                self._finish_playback(utt_id)
                continue
            if self._is_dead(utt_id):
                self._rm(path)
                continue
            if self.playing_utt != utt_id:      # 换人说话了，刷新刘海
                self.playing_utt = utt_id
                self.write_status()
            try:
                self.player = subprocess.Popen(
                    ["/usr/bin/afplay", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.player.wait()
            except Exception as e:
                log(f"播放失败 {e!r}")
            finally:
                self.player = None
                self._rm(path)

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------

def spawn_notch():
    """把刘海状态条拉起来（如果编译过而且还没在跑）。"""
    if not os.path.exists(NOTCH_BIN):
        return
    try:
        alive = subprocess.run(["pgrep", "-x", "voice-notch"],
                               capture_output=True, timeout=3).returncode == 0
        if not alive:
            subprocess.Popen([NOTCH_BIN], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            log("刘海状态条已拉起")
    except Exception as e:
        log(f"拉起刘海状态条失败 {e!r}")


def serve():
    cfg = load_config()
    engine = Engine(cfg)
    engine.load()
    engine.write_status()      # 起来先写一份空状态，别让刘海读到上次的残留
    spawn_notch()

    if os.path.exists(SOCK_PATH):
        os.remove(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    srv.listen(8)
    log(f"服务就绪 {SOCK_PATH}")

    def shutdown(*_):
        try:
            os.remove(SOCK_PATH)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            conn, _ = srv.accept()
        except Exception:
            continue
        try:
            data = conn.recv(65536).decode("utf-8", "replace").strip()
            req = json.loads(data) if data else {}
            cmd = req.get("cmd")
            if cmd == "ping":
                if cfg["tts_model"] == "moss":
                    model_id, voice = MOSS_MODEL, cfg["moss_ref_audio"]
                else:
                    model_id, voice = cfg["model"], cfg["kokoro_voice"]
                conn.sendall(json.dumps({
                    "ok": True, "model": model_id, "voice": voice,
                    "queue": engine.utt_q.qsize(),
                }).encode())
            elif cmd == "stop":
                engine.stop(req.get("session"))
                conn.sendall(b'{"ok":true}')
            elif cmd == "skip":
                skipped = engine.skip_current()
                conn.sendall(json.dumps({"ok": True, "skipped": skipped}).encode())
            elif cmd == "speak":
                depth = engine.enqueue(req.get("text", ""),
                                       req.get("session"), req.get("label"))
                conn.sendall(json.dumps({"ok": True, "queue": depth}).encode())
            else:
                conn.sendall(b'{"ok":false}')
        except Exception as e:
            log(f"请求处理失败 {e!r}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        serve()
    except Exception:
        import traceback
        log("服务崩溃:\n" + traceback.format_exc())
        raise
