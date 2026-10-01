# tests/test_web_voice_ws.py
import types
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from interview_agent.web.app import create_app
from interview_agent.web.audio import AudioService
from interview_agent.llm import AssistantTurn, StreamEnd


class AppLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


class FakeEngineSession:
    """引擎级 fake：把 FakeRecognition 的回调翻译成事件协议（与 DashScope 会话同构）。"""

    def __init__(self, on_event):
        self._on_event = on_event
        self._rec = None

    def start(self):
        self._rec = FakeRecognition(callback=self)

    def feed(self, pcm):
        self._rec.send_audio_frame(pcm)

    def stop(self):
        self._rec.stop()

    def on_event(self, result):
        s = result.get_sentence()
        self._on_event({"type": "final" if s.get("end") else "partial", "text": s.get("text", "")})

    def on_error(self, result):
        self._on_event({"type": "error", "message": str(getattr(result, "message", "识别错误"))})


class FakeStreamEngine:
    tts_model = "qwen-audio-3.0-tts-flash"

    def create_stream_recognizer(self, on_event):
        return FakeEngineSession(on_event)

    def transcribe_file(self, path, fmt):
        return ""

    def synthesize(self, text, voice, speed):
        return b""

    def synthesize_stream(self, text, voice, speed, on_chunk):
        pass


class FakeStreamResult:
    def __init__(self, text, end):
        self._s = {"text": text, "end": end}

    def get_sentence(self):
        return self._s


class FakeRecognition:
    last = None

    def __init__(self, callback=None, **kwargs):
        self.callback = callback
        self.kwargs = kwargs
        self.stopped = False
        FakeRecognition.last = self

    def start(self):
        pass

    def send_audio_frame(self, frame):
        if frame == b"ERR":
            self.callback.on_error(types.SimpleNamespace(message="会话已过期"))
            return
        text = frame.decode("utf-8", "replace")
        self.callback.on_event(FakeStreamResult(text, True))  # 直接给 final

    def stop(self):
        self.stopped = True


def make_client(tmp_path, with_audio=True):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    llm = AppLLM([AssistantTurn(content="开场")])
    audio = AudioService(FakeStreamEngine()) if with_audio else None
    return TestClient(create_app(cfg, llm=llm, audio=audio))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def start_session(c):
    return c.post("/api/session/start", data={}).json()["session_id"]


def test_ws_requires_auth(tmp_path):
    c = make_client(tmp_path)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/ws/voice/whatever"):
            pass
    assert e.value.code == 4401


def test_ws_unknown_session(tmp_path):
    c = make_client(tmp_path)
    c.post("/api/login", json={"token": "secret"})
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/ws/voice/2026-01-01_000000_nosuch00"):
            pass
    assert e.value.code == 4404


def test_ws_audio_disabled(tmp_path):
    c = make_client(tmp_path, with_audio=False)
    login(c)
    sid = start_session(c)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/api/ws/voice/{sid}"):
            pass
    assert e.value.code == 4403


def test_ws_streams_final_events(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    with c.websocket_connect(f"/api/ws/voice/{sid}") as ws:
        ws.send_bytes("你好面试官".encode("utf-8"))
        ev = ws.receive_json()
        assert ev == {"type": "final", "text": "你好面试官"}
        ws.send_text('{"type":"stop"}')
    assert FakeRecognition.last.stopped


def test_ws_closes_on_recognition_error(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect(f"/api/ws/voice/{sid}") as ws:
            ws.send_bytes(b"ERR")
            ev = ws.receive_json()
            assert ev == {"type": "error", "message": "会话已过期"}
            ws.receive_json()  # 等待服务端关闭
    assert e.value.code == 1011
