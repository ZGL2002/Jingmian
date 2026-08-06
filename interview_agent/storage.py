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


def init_transcript(path: Path, user_id: str, session_id: str) -> None:
    append_jsonl(path, {"role": "meta", "user_id": user_id, "session_id": session_id})


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
