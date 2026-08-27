# tests/test_web_session_audio.py
import io
from pathlib import Path
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
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


def make_client(tmp_path):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    llm = AppLLM([AssistantTurn(content="开场")])
    return TestClient(create_app(cfg, llm=llm))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def start_session(c):
    return c.post("/api/session/start", data={}).json()["session_id"]


def test_upload_and_download_roundtrip(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    r = c.post(f"/api/sessions/{sid}/audio",
               files={"file": ("interview.webm", io.BytesIO(b"WEBMAUDIO"), "audio/webm")})
    assert r.status_code == 200
    assert r.json()["url"] == f"/api/sessions/{sid}/audio"
    assert (Path(tmp_path) / "local" / sid / "audio" / "interview.webm").read_bytes() == b"WEBMAUDIO"

    g = c.get(f"/api/sessions/{sid}/audio")
    assert g.status_code == 200
    assert g.content == b"WEBMAUDIO"
    assert g.headers["content-type"].startswith("audio/webm")


def test_download_before_upload_404(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    assert c.get(f"/api/sessions/{sid}/audio").status_code == 404


def test_invalid_sid_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert c.get("/api/sessions/..%2F..%2Fetc/audio").status_code in (400, 404)


def test_history_has_audio_flag(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    assert c.get("/api/history").json()[0]["has_audio"] is False
    c.post(f"/api/sessions/{sid}/audio",
           files={"file": ("interview.webm", io.BytesIO(b"X"), "audio/webm")})
    assert c.get("/api/history").json()[0]["has_audio"] is True


def test_empty_upload_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    r = c.post(f"/api/sessions/{sid}/audio",
               files={"file": ("interview.webm", io.BytesIO(b""), "audio/webm")})
    assert r.status_code == 400
