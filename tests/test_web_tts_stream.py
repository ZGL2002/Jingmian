# tests/test_web_tts_stream.py
import base64
import pytest
from fastapi.testclient import TestClient
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

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def transcribe_file(self, path, fmt):
        return ""

    def synthesize(self, text, voice, speed):
        return b""

    def synthesize_stream(self, text, voice, speed, on_chunk):
        self.calls.append((text, voice, speed))
        if self.fail:
            raise RuntimeError("booom")
        on_chunk(b"\x01\x02")
        on_chunk(b"\x03")


def make_client(tmp_path, engine=None, extra_cfg=None):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    if extra_cfg:
        cfg.update(extra_cfg)
    llm = AppLLM([AssistantTurn(content="开场")])
    audio = AudioService(engine) if engine is not None else None
    return TestClient(create_app(cfg, llm=llm, audio=audio))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def test_tts_stream_chunks_and_done(tmp_path):
    eng = FakeStreamEngine()
    c = make_client(tmp_path, engine=eng)
    login(c)
    r = c.post("/api/tts/stream", json={"text": "你好。", "style": "cold"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    assert base64.b64decode(events[0]) == b"\x01\x02"
    assert base64.b64decode(events[1]) == b"\x03"
    assert eng.calls == [("你好。", "longyingjing", 0.95)]


def test_tts_stream_error_event(tmp_path):
    c = make_client(tmp_path, engine=FakeStreamEngine(fail=True))
    login(c)
    r = c.post("/api/tts/stream", json={"text": "你好。", "style": "serious"})
    assert r.status_code == 200
    assert '"type": "error"' in r.text
    assert r.text.strip().endswith("data: [DONE]")


def test_tts_stream_guards(tmp_path):
    c0 = make_client(tmp_path)  # audio=None
    login(c0)
    assert c0.post("/api/tts/stream", json={"text": "x", "style": "serious"}).status_code == 404
    c = make_client(tmp_path, engine=FakeStreamEngine())
    login(c)
    assert c.post("/api/tts/stream", json={"text": "", "style": "serious"}).status_code == 400
    assert c.post("/api/tts/stream", json={"text": "长" * 501, "style": "serious"}).status_code == 400


def test_tts_stream_voice_override(tmp_path):
    eng = FakeStreamEngine()
    c = make_client(tmp_path, engine=eng, extra_cfg={"tts_voice": "single-voice"})
    login(c)
    c.post("/api/tts/stream", json={"text": "好。", "style": "guide"})
    assert eng.calls[-1][1] == "single-voice"
