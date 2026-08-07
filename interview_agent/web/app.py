"""FastAPI 应用：路由、口令、SSE、历史、报告、面经库。"""
from __future__ import annotations
import re
import uuid
from pathlib import Path
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import markdown
from ..library import (
    new_experience_id, save_experience, list_experiences, delete_experience,
)
from ..models import ExperienceEntry
from ..resume import extract_text
from ..security import check_owner
from ..storage import list_sessions, read_jsonl, timestamp
from .auth import TokenAuthMiddleware, AUTH_COOKIE, token_matches
from .events import sse_format, sse_stream_async
from .manager import SessionManager

WEB_USER_ID = "local"
SID_PATTERN = re.compile(r"[\w\-]+")
STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: dict, llm=None) -> FastAPI:
    if llm is None:
        raise ValueError("create_app 需要 llm 实例")
    app = FastAPI(title="面试 Agent")
    token = config.get("web_token", "")
    app.add_middleware(TokenAuthMiddleware, token=token)
    manager = SessionManager(config, llm)
    app.state.manager = manager
    app.state.config = config
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")

    def _session_path(session_id: str) -> Path:
        if not SID_PATTERN.fullmatch(session_id or ""):
            raise HTTPException(400, "非法的会话 ID")
        root = (Path(config["session_root"]) / WEB_USER_ID).resolve()
        p = (root / session_id).resolve()
        if root not in p.parents:
            raise HTTPException(400, "越界路径")
        return p

    @app.get("/login")
    def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/")
    def index_page():
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/login")
    def login(payload: dict):
        given = str(payload.get("token", ""))
        if not token_matches(given, token):
            raise HTTPException(401, "口令错误")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(AUTH_COOKIE, given, httponly=True, samesite="lax", max_age=30 * 86400)
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(AUTH_COOKIE)
        return resp

    @app.post("/api/session/start")
    async def start_session(
        company: str = Form(""),
        position: str = Form(""),
        jd_text: str = Form(""),
        resume_text: str = Form(""),
        experience_ids: list[str] = Form([]),
        resume: UploadFile | None = File(None),
    ):
        if resume is not None:
            data = await resume.read()
            if not data:
                raise HTTPException(400, "上传文件为空")
            suffix = Path(resume.filename or "").suffix.lower()
            if suffix not in {".txt", ".md", ".pdf"}:
                raise HTTPException(400, "仅支持 .txt/.md/.pdf")
            tmp = Path(config["session_root"]) / WEB_USER_ID / ".uploads" / f"{uuid.uuid4().hex}{suffix}"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            try:
                resume_text = extract_text(str(tmp))
            finally:
                tmp.unlink(missing_ok=True)
        experiences = [
            e for e in list_experiences(Path(config["session_root"]), WEB_USER_ID)
            if e.entry_id in set(experience_ids)
        ]
        sid = manager.start_session(
            WEB_USER_ID,
            company=company.strip(),
            position=position.strip(),
            resume_text=resume_text or None,
            jd_text=jd_text,
            experiences=experiences,
        )
        return {"session_id": sid}

    @app.post("/api/answer")
    def answer(payload: dict):
        try:
            manager.submit_answer(WEB_USER_ID, payload["session_id"], payload["text"])
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True}

    @app.post("/api/end")
    def end(payload: dict):
        try:
            manager.end_session(WEB_USER_ID, payload["session_id"])
        except KeyError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True}

    @app.get("/api/session")
    def session_state(session_id: str):
        return manager.snapshot(WEB_USER_ID, session_id)

    @app.get("/api/stream")
    def stream(session_id: str, request: Request, last_id: int = 0):
        task = manager.get_task(WEB_USER_ID, session_id)
        if task is None:
            raise HTTPException(404, "会话不存在")
        header_id = request.headers.get("last-event-id", "")
        if header_id:
            try:
                last_id = int(header_id)
            except ValueError:
                last_id = 0

        async def gen():
            yield sse_format(manager.snapshot_event(WEB_USER_ID, session_id))
            async for ev in sse_stream_async(
                task.queue,
                stop_when=lambda e: e.get("type") == "status" and e.get("status") == "done",
                last_id=last_id,
            ):
                yield ev

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/history")
    def history():
        return list_sessions(Path(config["session_root"]), WEB_USER_ID)

    @app.get("/api/sessions/{session_id}/report")
    def report(session_id: str):
        p = _session_path(session_id) / "report.md"
        if not p.is_file():
            raise HTTPException(404, "报告不存在")
        check_owner(p, WEB_USER_ID)
        return HTMLResponse(f"<article class='report'>{markdown.markdown(p.read_text(encoding='utf-8'))}</article>")

    @app.get("/api/sessions/{session_id}/transcript")
    def transcript(session_id: str):
        p = _session_path(session_id) / "transcript.jsonl"
        if not p.is_file():
            raise HTTPException(404, "记录不存在")
        return read_jsonl(p)

    @app.get("/api/experiences")
    def get_experiences():
        return [
            {
                "entry_id": e.entry_id, "title": e.title, "content": e.content,
                "source": e.source, "company": e.company, "position": e.position,
                "created_at": e.created_at,
            }
            for e in list_experiences(Path(config["session_root"]), WEB_USER_ID)
        ]

    @app.post("/api/experiences")
    def add_experience(payload: dict):
        content = str(payload.get("content", "")).strip()
        if not content:
            raise HTTPException(400, "内容不能为空")
        entry = ExperienceEntry(
            entry_id=new_experience_id(),
            title=str(payload.get("title", "")).strip() or "未命名面经",
            content=content,
            source=str(payload.get("source", "")).strip(),
            company=str(payload.get("company", "")).strip(),
            position=str(payload.get("position", "")).strip(),
            created_at=timestamp(),
        )
        save_experience(Path(config["session_root"]), WEB_USER_ID, entry)
        return {
            "entry_id": entry.entry_id, "title": entry.title, "content": entry.content,
            "source": entry.source, "company": entry.company, "position": entry.position,
            "created_at": entry.created_at,
        }

    @app.delete("/api/experiences/{exp_id}")
    def remove_experience(exp_id: str):
        if not re.fullmatch(r"[\w\-]+", exp_id):
            raise HTTPException(400, "非法的面经 ID")
        if not delete_experience(Path(config["session_root"]), WEB_USER_ID, exp_id):
            raise HTTPException(404, "面经不存在")
        return {"ok": True}

    return app
