"""文件存储：会话目录、原子写、JSONL 记录、长回答落盘。"""
from __future__ import annotations
import json
import random
import string
import time
from pathlib import Path


def new_session_id() -> str:
    return f"{time.strftime('%Y-%m-%d_%H%M%S')}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"


def create_session_dir(session_root: Path, user_id: str) -> Path:
    d = session_root / user_id / new_session_id()
    d.mkdir(parents=True, exist_ok=False)
    (d / "answers").mkdir()
    (d / "blocks").mkdir()
    return d


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_owned(path: Path, user_id: str, content: str) -> None:
    """写入带 owner 元数据首行的文件。"""
    atomic_write(path, f"<!-- owner: {user_id} -->\n{content}")


def append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def init_transcript(path: Path, user_id: str, session_id: str, company: str = "", position: str = "",
                    style: str = "") -> None:
    entry = {"role": "meta", "user_id": user_id, "session_id": session_id}
    if company:
        entry["company"] = company
    if position:
        entry["position"] = position
    if style:
        entry["style"] = style
    append_jsonl(path, entry)


def list_sessions(user_root: Path, user_id: str) -> list[dict]:
    """列出用户命名空间下的面试记录（按会话目录名倒序）。"""
    root = user_root / user_id
    if not root.is_dir():
        return []
    out: list[dict] = []
    for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        transcript = d / "transcript.jsonl"
        if not transcript.exists():
            continue
        entries = read_jsonl(transcript)
        meta = next((e for e in entries if e.get("role") == "meta"), {})
        out.append({
            "session_id": d.name,
            "company": meta.get("company", ""),
            "position": meta.get("position", ""),
            "question_count": sum(1 for e in entries if e.get("role") == "interviewer"),
            "has_report": (d / "report.md").exists(),
            "created_at": d.name[:19],
        })
    return out


def offload_long_answer(session_dir: Path, index: int, text: str, user_id: str) -> Path:
    p = session_dir / "answers" / f"answer_{index}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_owned(p, user_id, text)
    return p


def save_report(session_dir: Path, markdown: str, user_id: str) -> Path:
    p = session_dir / "report.md"
    write_owned(p, user_id, markdown)
    return p


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
