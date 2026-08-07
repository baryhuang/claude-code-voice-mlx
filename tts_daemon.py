#!/usr/bin/env python3
"""Kokoro TTS 常驻服务。

为什么要常驻：模型加载要好几秒，每次念之前现加载比 say 还难受。
所以模型一直待在内存里，hook 只通过 unix socket 丢一句文字过来。

为什么按句切：整段合成完再播，开口前要干等十几秒。切成句子后，
第一句（十几个字）半秒就出来了，边播边合成后面的，开口延迟只跟第一句有关。

协议（unix socket，一行一个 JSON）：
    {"cmd": "speak", "text": "..."}
    {"cmd": "stop"}
    {"cmd": "ping"}   -> {"ok": true, "model": "..."}
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

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "zf_xiaobei"
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


def load_config():
    cfg = {"model": DEFAULT_MODEL, "kokoro_voice": DEFAULT_VOICE,
           "speed": DEFAULT_SPEED, "lang_code": DEFAULT_LANG}
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
        self.gen = 0                      # 代号：换一句就 +1，老的合成结果直接丢掉
        self.lock = threading.Lock()
        self.play_q = queue.Queue()
        self.player = None
        threading.Thread(target=self._play_loop, daemon=True).start()

    # -- 模型 ------------------------------------------------------------
    def load(self):
        t0 = time.time()
        import warnings, logging
        warnings.filterwarnings("ignore")          # jieba/torch 一堆噪音
        logging.getLogger("jieba").setLevel(logging.ERROR)
        from mlx_audio.tts.utils import load_model
        self.model = load_model(self.cfg["model"])
        log(f"模型加载完成 {self.cfg['model']} 耗时 {time.time() - t0:.1f}s")
        # 冷启动第一次要下载音色文件、建 jieba 词典，约 7 秒。
        # 在这里先跑一次，别让用户的第一句话吃到这个延迟。
        try:
            self._synth("预热")
            log(f"预热完成，累计 {time.time() - t0:.1f}s，可以接活了")
        except Exception as e:
            log(f"预热失败 {e!r}")

    def _synth(self, text):
        """返回 float32 numpy 波形。不同模型返回结构不一样，这里做兼容。"""
        import numpy as np
        kwargs = dict(text=text, voice=self.cfg["kokoro_voice"],
                      speed=self.cfg["speed"], lang_code=self.cfg["lang_code"])
        try:
            results = list(self.model.generate(**kwargs))
        except TypeError:      # 别的模型可能不认 lang_code / speed
            results = list(self.model.generate(text=text, voice=self.cfg["kokoro_voice"]))
        if not results:
            return None
        seg = results[0]
        audio = getattr(seg, "audio", seg)
        sr = getattr(seg, "sample_rate", None)
        if sr:
            self.sample_rate = int(sr)
        wav = np.asarray(audio, dtype="float32").reshape(-1)
        peak = float(abs(wav).max()) if len(wav) else 0.0
        if peak > 1e-6:
            wav = wav * (TARGET_PEAK / peak)
        return wav

    # -- 播放 ------------------------------------------------------------
    def _play_loop(self):
        while True:
            gen, path = self.play_q.get()
            if gen != self.gen:               # 过期的片段，跳过
                self._rm(path)
                continue
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

    def stop(self):
        with self.lock:
            self.gen += 1
        while not self.play_q.empty():
            try:
                self._rm(self.play_q.get_nowait()[1])
            except queue.Empty:
                break
        p = self.player
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    # -- 主流程 ----------------------------------------------------------
    def speak(self, text):
        self.stop()
        with self.lock:
            gen = self.gen
        threading.Thread(target=self._speak_worker, args=(text, gen), daemon=True).start()

    def _speak_worker(self, text, gen):
        import soundfile as sf
        chunks = split_sentences(text)
        log(f"开念：{len(text)} 字 -> {len(chunks)} 句")
        t0 = time.time()
        for i, chunk in enumerate(chunks):
            if gen != self.gen:
                log("被打断，停止合成")
                return
            try:
                wav = self._synth(chunk)
            except Exception as e:
                log(f"合成失败 {chunk[:20]!r} {e!r}")
                continue
            if wav is None or not len(wav):
                continue
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_")
            os.close(fd)
            sf.write(path, wav, self.sample_rate)
            if i == 0:
                log(f"首句就绪，延迟 {time.time() - t0:.2f}s")
            self.play_q.put((gen, path))


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------

def serve():
    cfg = load_config()
    engine = Engine(cfg)
    engine.load()

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
                conn.sendall(json.dumps({"ok": True, "model": cfg["model"],
                                         "voice": cfg["kokoro_voice"]}).encode())
            elif cmd == "stop":
                engine.stop()
                conn.sendall(b'{"ok":true}')
            elif cmd == "speak":
                engine.speak(req.get("text", ""))
                conn.sendall(b'{"ok":true}')
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
