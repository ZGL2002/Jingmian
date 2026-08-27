# tests/test_web_voice_ws.py
import sys
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


class FakeStreamEngine:
    asr_model = "qwen-audio-3.0-asr-flash-streaming"
    tts_model = "qwen-audio-3.0-tts-flash"

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


class FakeRecognitionResult:
    @staticmethod
    def is_sentence_end(s):
        return s.get("end") is True


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
        text = frame.decode("utf-8", "replace")
        self.callback.on_event(FakeStreamResult(text, True))  # 直接给 final

    def stop(self):
        self.stopped = True


def install_fake(monkeypatch):
    dashscope = types.ModuleType("dashscope")
    audio_mod = types.ModuleType("dashscope.audio")
    asr_mod = types.ModuleType("dashscope.audio.asr")
    asr_mod.Recognition = FakeRecognition
    asr_mod.RecognitionCallback = object
    asr_mod.RecognitionResult = FakeRecognitionResult
    tts_mod = types.ModuleType("dashscope.audio.tts_v2")
    tts_mod.SpeechSynthesizer = object
    tts_mod.ResultCallback = object
    tts_mod.AudioFormat = types.SimpleNamespace(PCM_22050HZ_MONO_16BIT="pcm16")
    audio_mod.asr = asr_mod
    audio_mod.tts_v2 = tts_mod
    dashscope.audio = audio_mod
    for name, mod in [("dashscope", dashscope), ("dashscope.audio", audio_mod),
                      ("dashscope.audio.asr", asr_mod), ("dashscope.audio.tts_v2", tts_mod)]:
        monkeypatch.setitem(sys.modules, name, mod)


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


def test_ws_streams_final_events(tmp_path, monkeypatch):
    install_fake(monkeypatch)
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    with c.websocket_connect(f"/api/ws/voice/{sid}") as ws:
        ws.send_bytes("你好面试官".encode("utf-8"))
        ev = ws.receive_json()
        assert ev == {"type": "final", "text": "你好面试官"}
        ws.send_text('{"type":"stop"}')
    assert FakeRecognition.last.stopped
    assert FakeRecognition.last.kwargs["format"] == "pcm"
