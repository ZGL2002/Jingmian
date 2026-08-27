# tests/test_web_styles.py
import time
from pathlib import Path
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.llm import AssistantTurn, StreamEnd
from interview_agent.storage import read_jsonl


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
    llm = AppLLM([AssistantTurn(content="开场"), AssistantTurn(content="问一"), AssistantTurn(content="## 报告")])
    return TestClient(create_app(cfg, llm=llm))


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def login(c):
    c.post("/api/login", json={"token": "secret"})


def test_styles_endpoint(tmp_path):
    c = make_client(tmp_path)
    login(c)
    data = c.get("/api/styles").json()
    assert [s["key"] for s in data] == ["serious", "cold", "gentle", "guide"]
    assert data[0]["label"] == "严肃"


def test_start_with_style_cold(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = c.post("/api/session/start", data={"style": "cold"}).json()["session_id"]
    session_dir = Path(tmp_path) / "local" / sid
    meta = read_jsonl(session_dir / "transcript.jsonl")[0]
    assert meta["style"] == "cold"
    assert "公事公办" in (session_dir / "prompt.md").read_text(encoding="utf-8")


def test_start_with_unknown_style_falls_back(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = c.post("/api/session/start", data={"style": "hacker"}).json()["session_id"]
    meta = read_jsonl(Path(tmp_path) / "local" / sid / "transcript.jsonl")[0]
    assert meta["style"] == "serious"
