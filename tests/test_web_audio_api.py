# tests/test_web_audio_api.py
import io
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.web.audio import AudioError, AudioService
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


class FakeEngine:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def transcribe_file(self, path, fmt):
        self.calls.append(("asr", fmt))
        if self.fail == "asr":
            raise AudioError("识别失败：mock")
        return "识别出的文字"

    def synthesize(self, text, voice, speed):
        self.calls.append(("tts", text, voice, speed))
        if self.fail == "tts":
            raise AudioError("合成失败：mock")
        return b"MP3BYTES"


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


def test_asr_roundtrip(tmp_path):
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng)
    login(c)
    r = c.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"WAVDATA"), "audio/wav")},
               data={"fmt": "wav"})
    assert r.status_code == 200
    assert r.json() == {"text": "识别出的文字"}
    assert eng.calls == [("asr", "wav")]


def test_tts_roundtrip_resolves_style_voice(tmp_path):
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng)
    login(c)
    r = c.post("/api/tts", json={"text": "你好。", "style": "cold"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == b"MP3BYTES"
    assert eng.calls == [("tts", "你好。", "longyingjing", 0.95)]


def test_tts_voice_overrides(tmp_path):
    # 单音色账号：全局 INTERVIEW_TTS_VOICE 对所有风格生效，语速仍按风格
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng, extra_cfg={"tts_voice": "single-voice"})
    login(c)
    c.post("/api/tts", json={"text": "你好。", "style": "cold"})
    assert eng.calls[-1] == ("tts", "你好。", "single-voice", 0.95)
    # 按风格覆盖优先于全局
    eng2 = FakeEngine()
    c2 = make_client(tmp_path, engine=eng2, extra_cfg={
        "tts_voice": "single-voice", "tts_voice_by_style": {"serious": "serious-voice"}})
    login(c2)
    c2.post("/api/tts", json={"text": "你好。", "style": "serious"})
    assert eng2.calls[-1][2] == "serious-voice"
    c2.post("/api/tts", json={"text": "你好。", "style": "guide"})
    assert eng2.calls[-1][2] == "single-voice"


def test_tts_text_limits(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine())
    login(c)
    assert c.post("/api/tts", json={"text": "", "style": "serious"}).status_code == 400
    assert c.post("/api/tts", json={"text": "长" * 501, "style": "serious"}).status_code == 400


def test_audio_disabled_returns_404(tmp_path):
    c = make_client(tmp_path)  # audio=None
    login(c)
    assert c.post("/api/tts", json={"text": "x", "style": "serious"}).status_code == 404
    assert c.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"x"), "audio/wav")}).status_code == 404


def test_asr_bad_format_and_size(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine())
    login(c)
    r = c.post("/api/asr", files={"file": ("a.webm", io.BytesIO(b"x"), "audio/webm")},
               data={"fmt": "webm"})
    assert r.status_code == 400
    big = io.BytesIO(b"\0" * (20 * 1024 * 1024 + 1))
    r = c.post("/api/asr", files={"file": ("a.wav", big, "audio/wav")}, data={"fmt": "wav"})
    assert r.status_code == 400


def test_sdk_error_maps_502(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine(fail="tts"))
    login(c)
    assert c.post("/api/tts", json={"text": "你好。", "style": "serious"}).status_code == 502
    c2 = make_client(tmp_path, engine=FakeEngine(fail="asr"))
    login(c2)
    r = c2.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"x"), "audio/wav")})
    assert r.status_code == 502
