# tests/test_local_audio.py
"""LocalEngine 协议契约测试：fake websockets/httpx 注入，不依赖真实本地服务。"""
import json
import queue
import struct
import sys
import threading
import time
import types
import pytest

from interview_agent.web.audio import AudioError
from interview_agent.web.local_audio import LocalEngine, WavPcmStreamConverter


def wav_bytes(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    bits = 16
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, bits) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def wait_for(cond, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeConnectionClosed(Exception):
    pass


class FakeWSConnection:
    last = None
    urls = []

    def __init__(self, handler=None):
        self.json_sent = []
        self.binary_sent = []
        self.queue = queue.Queue()
        self.closed = False
        self._handler = handler
        self._lock = threading.Lock()
        FakeWSConnection.last = self

    def push(self, obj):
        self.queue.put(json.dumps(obj, ensure_ascii=False))

    def close(self):
        self.closed = True

    def send(self, payload):
        with self._lock:
            if isinstance(payload, str):
                self.json_sent.append(json.loads(payload))
            else:
                self.binary_sent.append(payload)
        if self._handler:
            self._handler(payload, self)

    def recv(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.closed and self.queue.empty():
                raise FakeConnectionClosed("closed")
            try:
                return self.queue.get(timeout=0.02)
            except queue.Empty:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("recv timeout") from None
                continue

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def fake_connect_factory(handler=None, fail=False):
    def connect(url, open_timeout=None, close_timeout=None, max_size=None):
        if fail:
            raise ConnectionRefusedError(f"Connection refused: {url}")
        FakeWSConnection.urls.append(url)
        conn = FakeWSConnection(handler)
        conn.url = url
        return conn
    return connect


def install_fake_websockets(monkeypatch, connect):
    ws_mod = types.ModuleType("websockets")
    sync_mod = types.ModuleType("websockets.sync")
    client_mod = types.ModuleType("websockets.sync.client")
    client_mod.connect = connect
    ws_mod.sync = sync_mod
    sync_mod.client = client_mod
    for name, mod in [("websockets", ws_mod), ("websockets.sync", sync_mod),
                      ("websockets.sync.client", client_mod)]:
        monkeypatch.setitem(sys.modules, name, mod)


class FakeResponse:
    def __init__(self, chunks=None, content=None, status_code=200):
        self._chunks = list(chunks or [])
        self.content = content if content is not None else b"".join(self._chunks)
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, chunk_size=None):
        yield from self._chunks


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


class FakeHttpClient:
    last = None
    response = None  # 测试前设置

    def __init__(self, timeout=None):
        FakeHttpClient.last = self
        self.requests = []

    def post(self, url, json=None):
        self.requests.append(("POST", url, json))
        return FakeHttpClient.response

    def stream(self, method, url, json=None):
        self.requests.append((method, url, json))
        return _StreamCtx(FakeHttpClient.response)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def install_fake_httpx(monkeypatch):
    mod = types.ModuleType("httpx")
    mod.Client = FakeHttpClient
    monkeypatch.setitem(sys.modules, "httpx", mod)


# ---- 流式 ASR（FunASR 2pass WebSocket）----


def test_stream_session_protocol(monkeypatch):
    def handler(payload, conn):
        # 模拟真实服务端：收到 is_speaking=false 后补发最后一句 final（is_final 标记）
        if isinstance(payload, str) and json.loads(payload).get("is_speaking") is False:
            conn.push({"mode": "2pass-offline", "text": "收尾句。", "is_final": True})
    install_fake_websockets(monkeypatch, fake_connect_factory(handler))
    engine = LocalEngine(funasr_ws_url="ws://127.0.0.1:10095")
    events = []
    s = engine.create_stream_recognizer(events.append)
    s.start()
    conn = FakeWSConnection.last
    assert conn.url == "ws://127.0.0.1:10095"
    # 连接后首条消息为 2pass 配置
    cfg = conn.json_sent[0]
    assert cfg["mode"] == "2pass"
    assert cfg["wav_format"] == "pcm"
    assert cfg["audio_fs"] == 16000
    # online 通道 → partial（FunASR 给的是增量片段，须累积为整句）；offline 通道（整句带标点）→ final
    conn.push({"mode": "2pass-online", "text": "欢迎大"})
    assert wait_for(lambda: {"type": "partial", "text": "欢迎大"} in events)
    conn.push({"mode": "2pass-online", "text": "家来"})
    assert wait_for(lambda: {"type": "partial", "text": "欢迎大家来"} in events)
    conn.push({"mode": "2pass-offline", "text": "欢迎大家来体验。"})
    assert wait_for(lambda: {"type": "final", "text": "欢迎大家来体验。"} in events)
    # final 之后 partial 缓冲重置：新一轮增量从头累积
    conn.push({"mode": "2pass-online", "text": "下一"})
    conn.push({"mode": "2pass-online", "text": "句话"})
    assert wait_for(lambda: {"type": "partial", "text": "下一句话"} in events)
    # 空文本结果不透出（FunASR 闲置时会发空 text）
    n = len(events)
    conn.push({"mode": "2pass-online", "text": ""})
    time.sleep(0.1)
    assert len(events) == n
    # 16k PCM 帧以二进制直传
    s.feed(b"\x01\x00" * 100)
    assert b"\x01\x00" * 100 in conn.binary_sent
    # stop 发送 is_speaking=false 结束标志，并等服务端 final flush（is_final）后再关连接
    s.stop()
    assert conn.json_sent[-1] == {"is_speaking": False}
    assert wait_for(lambda: {"type": "final", "text": "收尾句。"} in events)


def test_stream_session_connect_error(monkeypatch):
    install_fake_websockets(monkeypatch, fake_connect_factory(fail=True))
    engine = LocalEngine()
    with pytest.raises(AudioError, match="FunASR"):
        engine.create_stream_recognizer(lambda ev: None).start()


def test_stream_session_error_on_disconnect(monkeypatch):
    # 服务端断开 → error 事件 → 既有断线重连路径接管
    install_fake_websockets(monkeypatch, fake_connect_factory())
    events = []
    s = LocalEngine().create_stream_recognizer(events.append)
    s.start()
    FakeWSConnection.last.close()
    assert wait_for(lambda: any(e["type"] == "error" for e in events))
    s.stop()


# ---- 整段识别（FunASR offline）----


def test_transcribe_file_chunked_2pass(tmp_path, monkeypatch):
    # 实测整文件直灌（offline/bulk）该服务版本不响应：整段识别走分帧 2pass 协议
    def handler(payload, conn):
        if isinstance(payload, str) and json.loads(payload).get("is_speaking") is False:
            conn.push({"mode": "2pass-offline", "text": "整段文字，", "is_final": False})
            conn.push({"mode": "2pass-offline", "text": "第二句。", "is_final": True})
    install_fake_websockets(monkeypatch, fake_connect_factory(handler))
    p = tmp_path / "a.wav"
    p.write_bytes(wav_bytes(b"\0" * 6400, 16000))
    assert LocalEngine().transcribe_file(p, "wav") == "整段文字，第二句。"
    conn = FakeWSConnection.last
    assert conn.json_sent[0]["mode"] == "2pass"
    assert conn.json_sent[0]["wav_format"] == "pcm"
    assert len(b"".join(conn.binary_sent)) == 6400  # WAV 头已剥，PCM 全量分帧发出
    assert all(len(b) <= 3200 for b in conn.binary_sent)


def test_transcribe_file_rejects_unsupported_format(tmp_path):
    with pytest.raises(AudioError, match="wav/pcm"):
        LocalEngine().transcribe_file(tmp_path / "a.mp3", "mp3")


def test_transcribe_file_timeout(tmp_path, monkeypatch):
    install_fake_websockets(monkeypatch, fake_connect_factory())  # 服务端永不响应
    p = tmp_path / "a.wav"
    p.write_bytes(wav_bytes(b"\0" * 8, 16000))
    with pytest.raises(AudioError, match="超时"):
        LocalEngine(timeout=0.2).transcribe_file(p, "wav")


# ---- TTS（CosyVoice OpenAI 兼容端点）----


def test_synthesize_returns_wav(monkeypatch):
    FakeHttpClient.response = FakeResponse(content=wav_bytes(b"PCM", 22050))
    install_fake_httpx(monkeypatch)
    out = LocalEngine(cosyvoice_url="http://127.0.0.1:9880/").synthesize("文本", "中文女", 1.0)
    assert out.startswith(b"RIFF")
    method, url, payload = FakeHttpClient.last.requests[0]
    assert (method, url) == ("POST", "http://127.0.0.1:9880/v1/audio/speech")
    assert payload["input"] == "文本"
    assert payload["voice"] == "中文女"
    assert payload["speed"] == 1.0
    assert payload["response_format"] == "wav"


def test_synthesize_stream_passthrough_22050(monkeypatch):
    pcm = struct.pack("<400h", *range(400))
    body = wav_bytes(pcm, 22050)
    FakeHttpClient.response = FakeResponse(chunks=[body[:10], body[10:200], body[200:]])  # 头跨块
    install_fake_httpx(monkeypatch)
    chunks = []
    LocalEngine().synthesize_stream("一句话", "v", 1.0, chunks.append)
    assert b"".join(chunks) == pcm  # 22.05k 单声道直通：剥头后逐字节保真


def test_synthesize_stream_resamples_24000_stereo(monkeypatch):
    # 24k 立体声：下混（L+R 平均恒为 500）+ 重采样至 22.05k
    n = 2400
    frames = b"".join(struct.pack("<hh", i % 1000, 1000 - i % 1000) for i in range(n))
    body = wav_bytes(frames, 24000, channels=2)
    FakeHttpClient.response = FakeResponse(chunks=[body[:7], body[7:5000], body[5000:]])
    install_fake_httpx(monkeypatch)
    chunks = []
    LocalEngine().synthesize_stream("一句话", "v", 1.0, chunks.append)
    out = b"".join(chunks)
    samples = struct.unpack(f"<{len(out) // 2}h", out)
    assert abs(len(samples) - n * 22050 // 24000) <= 3  # 时长按采样率比例保持
    assert all(s == 500 for s in samples)


def test_synthesize_stream_rejects_non_wav(monkeypatch):
    FakeHttpClient.response = FakeResponse(chunks=[b"NOTAWAV12345" * 4])
    install_fake_httpx(monkeypatch)
    with pytest.raises(AudioError, match="WAV"):
        LocalEngine().synthesize_stream("一句话", "v", 1.0, lambda c: None)


def test_synthesize_http_error(monkeypatch):
    FakeHttpClient.response = FakeResponse(status_code=500)
    install_fake_httpx(monkeypatch)
    with pytest.raises(AudioError, match="CosyVoice"):
        LocalEngine().synthesize("文本", "v", 1.0)


# ---- WavPcmStreamConverter 单元 ----


def test_converter_resample_length_and_boundary():
    conv = WavPcmStreamConverter()
    pcm = struct.pack("<24000h", *[((i * 7) % 30000 - 15000) for i in range(24000)])  # 1s @24k
    body = wav_bytes(pcm, 24000)
    out = b"".join(
        conv.feed(body[i:i + 777]) for i in range(0, len(body), 777)
    ) + conv.flush()
    assert abs(len(out) // 2 - 22050) <= 3  # 1s 音频重采样后仍约 1s


def test_converter_passthrough_odd_chunks_exact():
    conv = WavPcmStreamConverter()
    pcm = bytes(range(256)) * 3
    body = wav_bytes(pcm, 22050)
    out = b"".join(
        filter(None, (conv.feed(body[i:i + 13]) for i in range(0, len(body), 13)))
    ) + conv.flush()
    assert out == pcm


def test_converter_rejects_non_16bit():
    conv = WavPcmStreamConverter()
    bits = 8
    body = (b"RIFF" + struct.pack("<I", 36 + 4) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, 22050, 22050, 1, bits) + b"data" +
            struct.pack("<I", 4) + b"\1\2\3\4")
    with pytest.raises(AudioError, match="16bit PCM 或 32bit float"):
        conv.feed(body)


def test_converter_accepts_float32_wav():
    # 本地 CosyVoice 常见输出：32bit 浮点（format=3）→ 转 int16
    conv = WavPcmStreamConverter()
    floats = [0.5, -0.5, 0.25, -1.0, 1.0, 0.0]
    pcm = struct.pack(f"<{len(floats)}f", *floats)
    body = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 3, 1, 22050, 22050 * 4, 4, 32) + b"data" +
            struct.pack("<I", len(pcm)) + pcm)
    out = b"".join(filter(None, (conv.feed(body[i:i + 7]) for i in range(0, len(body), 7)))) + conv.flush()
    got = struct.unpack(f"<{len(out) // 2}h", out)
    assert got == (16384, -16384, 8192, -32767, 32767, 0)  # -1.0 按非对称缩放（×32767）
