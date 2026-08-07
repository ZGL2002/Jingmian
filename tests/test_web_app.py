import json
import time
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
        "session_root": str(tmp_path),
        "min_questions": 1,
        "language": "zh",
        "model": "deepseek-chat",
        "web_token": "secret",
    }
    llm = AppLLM([
        AssistantTurn(content="开场"),
        AssistantTurn(content="问题一"),
        AssistantTurn(content="## 评估"),
    ])
    return TestClient(create_app(cfg, llm=llm))


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_full_web_flow(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/", follow_redirects=False).status_code == 302
    assert c.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"token": "secret"}).status_code == 200

    sid = c.post("/api/session/start", data={
        "company": "字节跳动", "position": "后端开发", "jd_text": "熟悉 Go",
    }).json()["session_id"]

    assert c.post("/api/answer", json={"session_id": sid, "text": "回答1"}).status_code == 200
    assert wait_until(lambda: c.get("/api/session", params={"session_id": sid}).json()["question_count"] == 1)
    assert c.post("/api/end", json={"session_id": sid}).status_code == 200
    assert wait_until(lambda: c.get("/api/session", params={"session_id": sid}).json()["state"] == "done")

    events = []
    with c.stream("GET", "/api/stream", params={"session_id": sid}) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if any(e.get("status") == "done" for e in events):
                    break
    assert any(e.get("status") == "done" for e in events)
    assert any(e["type"] == "delta" and e["text"] == "开场" for e in events)
    assert any(e["type"] == "turn_end" and e["question_count"] == 1 for e in events)

    history = c.get("/api/history").json()
    assert history[0]["company"] == "字节跳动"
    assert history[0]["position"] == "后端开发"
    assert history[0]["has_report"] is True
    report = c.get(f"/api/sessions/{sid}/report")
    assert report.status_code == 200
    assert "评估" in report.text


def test_experience_crud_via_api(tmp_path):
    c = make_client(tmp_path)
    c.post("/api/login", json={"token": "secret"})
    r = c.post("/api/experiences", json={
        "title": "字节面经", "content": "高频问缓存",
        "source": "牛客", "company": "字节跳动", "position": "后端开发",
    })
    assert r.status_code == 200
    eid = r.json()["entry_id"]
    entries = c.get("/api/experiences").json()
    assert entries[0]["company"] == "字节跳动"
    assert c.delete(f"/api/experiences/{eid}").status_code == 200
    assert c.get("/api/experiences").json() == []


class SlowLLM(AppLLM):
    def chat_stream(self, messages, tools=None, max_tokens=None):
        time.sleep(0.3)
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def test_busy_answer_rejected(tmp_path):
    cfg = {
        "session_root": str(tmp_path),
        "min_questions": 1,
        "language": "zh",
        "model": "deepseek-chat",
        "web_token": "secret",
    }
    c = TestClient(create_app(cfg, llm=SlowLLM([
        AssistantTurn(content="开场"),
        AssistantTurn(content="问题一"),
        AssistantTurn(content="## 评估"),
    ])))
    c.post("/api/login", json={"token": "secret"})
    sid = c.post("/api/session/start", data={}).json()["session_id"]
    r = c.post("/api/answer", json={"session_id": sid, "text": "回答1"})
    assert r.status_code == 200
    # 立即再次提交：mock 流式接口 sleep 0.3s，busy 必然仍为 True
    r2 = c.post("/api/answer", json={"session_id": sid, "text": "回答2"})
    assert r2.status_code == 400
    assert "思考中" in r2.json()["error"]
