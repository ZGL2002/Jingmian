# interview_agent/web/local_audio.py
"""本地语音引擎：FunASR runtime（流式/整段 ASR）+ CosyVoice（TTS）。

服务以独立进程常驻，本模块只做协议适配：
- ASR：WebSocket 连 FunASR runtime server（2pass 模式，自带 VAD/标点/ITN）；
- TTS：HTTP 调 CosyVoice server 的 OpenAI 兼容端点 POST /v1/audio/speech（返回 WAV），
  解析 WAV 头并重采样为 22.05kHz 单声道 PCM16 后经 on_chunk 透出。

websockets / httpx 在方法体内延迟 import：未使用本地语音的环境不受影响。
"""
from __future__ import annotations
import json
import struct
import threading
from collections.abc import Callable
from pathlib import Path

from .audio import AudioError

TTS_TARGET_RATE = 22050  # 前端契约：/api/tts/stream 下发 22.05kHz 单声道 PCM16
CONNECT_TIMEOUT = 5.0
IO_TIMEOUT = 60.0


class WavPcmStreamConverter:
    """WAV 字节流 → 目标采样率单声道 PCM16 字节流。

    跨块保持状态：WAV 头未凑齐前攒字节；重采样维护浮点读取位置；
    立体声下混前保留不完整帧的字节。采样率一致且单声道时直通（逐字节保真）。
    """

    def __init__(self, target_rate: int = TTS_TARGET_RATE):
        self._target = target_rate
        self._buf = bytearray()
        self._meta: tuple[int, int, int, int] | None = None  # (data_offset, channels, rate, bits)
        self._pend: list[int] = []   # 待重采样的样本（int16 值，已下混为单声道）
        self._pos = 0.0              # 重采样读取位置（_pend 的浮点索引）
        self._frame_rest = b""       # 不足一个声道帧的残留字节

    def feed(self, block: bytes) -> bytes:
        self._buf += block
        if self._meta is None:
            meta = _parse_wav_header(bytes(self._buf))
            if meta is None:
                return b""  # 头还没凑齐
            self._meta = meta
            data_offset, _, _, bits = meta
            if bits != 16:
                raise AudioError(f"本地 TTS 输出为 {bits}bit，仅支持 16bit PCM")
            del self._buf[:data_offset]
        data = bytes(self._buf)
        self._buf.clear()
        if self._passthrough():
            return data
        self._append_samples(data)
        return self._resample_available()

    def flush(self) -> bytes:
        if self._passthrough() or not self._pend:
            return b""
        # 收尾：把最后一个样本作为终点补一次插值，避免丢尾巴
        self._pos = len(self._pend) - 1
        out = [self._interp(self._pos)]
        self._pend = []
        self._pos = 0.0
        return _pack_i16(out)

    def _passthrough(self) -> bool:
        _, channels, rate, _ = self._meta or (0, 1, self._target, 16)
        return channels == 1 and rate == self._target

    def _append_samples(self, data: bytes) -> None:
        _, channels, _, _ = self._meta
        data = self._frame_rest + data
        frame_size = channels * 2
        n_frames, rest = divmod(len(data), frame_size)
        if rest:
            self._frame_rest = data[len(data) - rest:]
        else:
            self._frame_rest = b""
        if n_frames == 0:
            return
        samples = struct.unpack(f"<{n_frames * channels}h", data[:n_frames * frame_size])
        if channels == 1:
            self._pend.extend(samples)
        else:
            for i in range(0, len(samples), channels):
                self._pend.append(round(sum(samples[i:i + channels]) / channels))

    def _resample_available(self) -> bytes:
        _, _, rate, _ = self._meta
        step = rate / self._target
        out: list[int] = []
        while self._pos + 1 < len(self._pend):
            out.append(self._interp(self._pos))
            self._pos += step
        # 保留已消费的最后一个样本，供下一块边界插值
        consumed = int(self._pos)
        if consumed > 0:
            keep = max(consumed - 1, 0)
            del self._pend[:keep]
            self._pos -= keep
        return _pack_i16(out)

    def _interp(self, pos: float) -> int:
        i = int(pos)
        frac = pos - i
        a = self._pend[i]
        b = self._pend[i + 1] if i + 1 < len(self._pend) else a
        return int(a + (b - a) * frac)


def _pack_i16(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def _parse_wav_header(buf: bytes) -> tuple[int, int, int, int] | None:
    """解析 RIFF/WAV 头，返回 (data 起始偏移, 声道数, 采样率, 位深)；字节不足返回 None。"""
    if len(buf) < 12 or buf[0:4] != b"RIFF" or buf[8:12] != b"WAVE":
        if len(buf) >= 12:
            raise AudioError("本地 TTS 返回的不是 WAV 音频")
        return None
    offset = 12
    channels = rate = bits = None
    while True:
        if offset + 8 > len(buf):
            return None
        chunk_id = buf[offset:offset + 4]
        (size,) = struct.unpack_from("<I", buf, offset + 4)
        body = offset + 8
        if chunk_id == b"fmt ":
            if body + size > len(buf):
                return None
            _, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", buf, body)
        elif chunk_id == b"data":
            if channels is None:
                raise AudioError("WAV 头缺少 fmt 块")
            return body, channels, rate, bits
        offset = body + size + (size & 1)  # chunk 按 2 字节对齐


class _FunASRStreamSession:
    """FunASR runtime 2pass 流式识别会话：16k PCM 帧 → 事件回调（协议与 DashScope 会话一致）。

    FunASR 的 offline 通道产出整句（带标点），映射为 final；online 通道产出的是
    **增量片段**，而前端契约（DashScope 语义）要求 partial 为累计整句，这里做累积转换。
    连接断开映射 error，触发既有重连路径。
    """

    def __init__(self, ws_url: str, on_event: Callable[[dict], None],
                 connect_timeout: float = CONNECT_TIMEOUT):
        self._ws_url = ws_url
        self._on_event = on_event
        self._connect_timeout = connect_timeout
        self._conn = None
        self._reader = None
        self._closing = False
        self._partial_buf = ""
        self._final_flush = threading.Event()

    def start(self) -> None:
        from websockets.sync.client import connect
        try:
            self._conn = connect(
                self._ws_url, open_timeout=self._connect_timeout,
                close_timeout=2, max_size=None,
            )
        except Exception as e:  # noqa: BLE001 - 连接失败统一包装
            raise AudioError(
                f"FunASR 服务连接失败（{self._ws_url}）：{e}。"
                "请确认本地 ASR 服务已启动"
            ) from None
        self._conn.send(json.dumps({
            "mode": "2pass",
            "chunk_size": [5, 10, 5],
            "chunk_interval": 10,
            "wav_name": "interview",
            "wav_format": "pcm",
            "audio_fs": 16000,
            "is_speaking": True,
            "itn": True,
        }))
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            while True:
                msg = self._conn.recv()
                if not isinstance(msg, str):
                    continue
                data = json.loads(msg)
                text = str(data.get("text") or "").strip()
                if data.get("is_final"):
                    self._final_flush.set()
                if not text:
                    continue
                mode = str(data.get("mode") or "")
                if data.get("is_final") or mode == "2pass-offline":
                    self._on_event({"type": "final", "text": text})
                    self._partial_buf = ""
                else:
                    self._partial_buf += text
                    self._on_event({"type": "partial", "text": self._partial_buf})
        except Exception as e:  # noqa: BLE001 - 断线等异常经 error 事件透出
            if not self._closing:
                self._on_event({"type": "error", "message": f"本地识别会话中断：{e}"})

    def feed(self, pcm: bytes) -> None:
        if self._conn is not None:
            self._conn.send(pcm)

    def stop(self) -> None:
        self._closing = True
        if self._conn is not None:
            try:
                self._conn.send(json.dumps({"is_speaking": False}))
            except Exception:  # noqa: BLE001 - 会话已死时收尾尽力而为
                pass
            # 等服务端 2pass 离线通道补发最后一句 final（is_final 标记）再关连接，
            # 语义对齐 DashScope stop 的优雅收尾
            self._final_flush.wait(timeout=3.0)
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        if self._reader is not None:
            self._reader.join(timeout=2)


class LocalEngine:
    """本地 FunASR + CosyVoice 引擎，接口与 DashScopeEngine 一致。"""

    SYNTH_MIME = "audio/wav"  # synthesize 返回完整 WAV（/api/tts 试听）

    def __init__(self, cosyvoice_url: str = "http://127.0.0.1:9880",
                 funasr_ws_url: str = "ws://127.0.0.1:10095",
                 tts_model: str = "cosyvoice", timeout: float = IO_TIMEOUT):
        self.cosyvoice_url = cosyvoice_url.rstrip("/")
        self.funasr_ws_url = funasr_ws_url
        self.tts_model = tts_model
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg: dict) -> "LocalEngine":
        return cls(
            cosyvoice_url=cfg.get("cosyvoice_url", "http://127.0.0.1:9880"),
            funasr_ws_url=cfg.get("funasr_ws_url", "ws://127.0.0.1:10095"),
            tts_model=cfg.get("tts_model", "cosyvoice"),
        )

    # ---- ASR ----

    def create_stream_recognizer(self, on_event: Callable[[dict], None]) -> _FunASRStreamSession:
        return _FunASRStreamSession(self.funasr_ws_url, on_event)

    def transcribe_file(self, path: Path, fmt: str = "wav") -> str:
        if fmt not in ("wav", "pcm"):
            raise AudioError(f"本地 ASR 不支持 {fmt}，仅支持 wav/pcm")
        from websockets.sync.client import connect
        data = Path(path).read_bytes()
        try:
            with connect(self.funasr_ws_url, open_timeout=CONNECT_TIMEOUT,
                         close_timeout=2, max_size=None) as conn:
                conn.send(json.dumps({
                    "mode": "offline",
                    "wav_name": Path(path).stem,
                    "wav_format": fmt,
                    "audio_fs": 16000,
                    "itn": True,
                }))
                for i in range(0, len(data), 64_000):
                    conn.send(data[i:i + 64_000])
                conn.send(json.dumps({"is_speaking": False}))
                texts: list[str] = []
                while True:
                    msg = conn.recv(timeout=self.timeout)
                    if not isinstance(msg, str):
                        continue
                    resp = json.loads(msg)
                    text = str(resp.get("text") or "").strip()
                    if text:
                        texts.append(text)
                    if resp.get("is_final"):
                        break
        except TimeoutError:
            raise AudioError(f"本地 ASR 识别超时（{self.funasr_ws_url} 无响应）") from None
        except AudioError:
            raise
        except Exception as e:  # noqa: BLE001 - 连接/协议异常统一包装
            raise AudioError(
                f"本地 ASR 识别失败（{self.funasr_ws_url}）：{e}。"
                "请确认 FunASR 服务已启动"
            ) from None
        return "".join(texts).strip()

    # ---- TTS ----

    def _speech_payload(self, text: str, voice: str, speed: float) -> dict:
        return {
            "model": self.tts_model,
            "input": text,
            "voice": voice,
            "speed": speed,
            "response_format": "wav",
        }

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        """整段合成：返回完整 WAV（试听用）。"""
        import httpx
        url = f"{self.cosyvoice_url}/v1/audio/speech"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=self._speech_payload(text, voice, speed))
                resp.raise_for_status()
                return resp.content
        except AudioError:
            raise
        except Exception as e:  # noqa: BLE001 - HTTP 异常统一包装
            raise AudioError(
                f"本地 TTS 合成失败（{url}）：{e}。请确认 CosyVoice 服务已启动"
            ) from None

    def synthesize_stream(self, text: str, voice: str, speed: float,
                          on_chunk: Callable[[bytes], None]) -> None:
        """流式合成：WAV 响应边收边转 22.05kHz 单声道 PCM16，经 on_chunk 透出。"""
        import httpx
        url = f"{self.cosyvoice_url}/v1/audio/speech"
        conv = WavPcmStreamConverter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=self._speech_payload(text, voice, speed)) as resp:
                    resp.raise_for_status()
                    for block in resp.iter_bytes(8192):
                        pcm = conv.feed(block)
                        if pcm:
                            on_chunk(pcm)
        except AudioError:
            raise
        except Exception as e:  # noqa: BLE001 - HTTP 异常统一包装
            raise AudioError(
                f"本地 TTS 合成失败（{url}）：{e}。请确认 CosyVoice 服务已启动"
            ) from None
        tail = conv.flush()
        if tail:
            on_chunk(tail)
