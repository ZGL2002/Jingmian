# tests/test_web_backgrounds.py
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
    return TestClient(create_app(cfg, llm=AppLLM([AssistantTurn(content="开场")])))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def upload(c, style, name, data, mime):
    return c.post(f"/api/backgrounds/{style}",
                  files={"file": (name, io.BytesIO(data), mime)})


def test_upload_get_and_replace(tmp_path):
    c = make_client(tmp_path)
    login(c)
    r = upload(c, "serious", "bg.gif", b"GIF89a", "image/gif")
    assert r.status_code == 200
    assert r.json()["url"] == "/api/backgrounds/serious"
    assert (Path(tmp_path) / "local" / "backgrounds" / "serious.gif").exists()

    g = c.get("/api/backgrounds/serious")
    assert g.status_code == 200
    assert g.content == b"GIF89a"

    # 换 mp4 后旧 gif 被清理，GET 返回新文件
    upload(c, "serious", "bg.mp4", b"MP4DATA", "video/mp4")
    assert not (Path(tmp_path) / "local" / "backgrounds" / "serious.gif").exists()
    assert c.get("/api/backgrounds/serious").content == b"MP4DATA"


def test_missing_background_404(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert c.get("/api/backgrounds/gentle").status_code == 404


def test_unknown_style_and_bad_ext_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert upload(c, "evil", "x.gif", b"X", "image/gif").status_code == 400
    assert upload(c, "gentle", "x.txt", b"X", "text/plain").status_code == 400


def test_backgrounds_dir_not_in_history(tmp_path):
    c = make_client(tmp_path)
    login(c)
    upload(c, "serious", "bg.gif", b"GIF89a", "image/gif")
    assert all(s["session_id"] != "backgrounds" for s in c.get("/api/history").json())
