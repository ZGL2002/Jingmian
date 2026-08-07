"""面经库：interviews/<user_id>/experiences/ 下的本地参考材料。"""
from __future__ import annotations
import json
import random
import re
import string
import time
from pathlib import Path
from .models import ExperienceEntry
from .storage import atomic_write, timestamp
from .security import check_owner


def new_experience_id() -> str:
    return f"exp_{int(time.time())}_{''.join(random.choices(string.ascii_lowercase + string.digits, k=6))}"


def _meta_line(entry: ExperienceEntry) -> str:
    data = {
        "id": entry.entry_id,
        "title": entry.title,
        "source": entry.source,
        "company": entry.company,
        "position": entry.position,
        "created_at": entry.created_at,
    }
    return "<!-- meta: " + json.dumps(data, ensure_ascii=False) + " -->"


def experiences_dir(user_root: Path, user_id: str) -> Path:
    return user_root / user_id / "experiences"


def save_experience(user_root: Path, user_id: str, entry: ExperienceEntry) -> Path:
    d = experiences_dir(user_root, user_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{entry.entry_id}.md"
    text = f"<!-- owner: {user_id} -->\n{_meta_line(entry)}\n{entry.content}\n"
    atomic_write(path, text)
    return path


def read_experience(path: Path) -> ExperienceEntry | None:
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("<!-- owner:"):
        return None
    meta: dict = {}
    if len(lines) > 1 and lines[1].startswith("<!-- meta:"):
        raw = lines[1][len("<!-- meta:"):].strip().removesuffix("-->").strip()
        meta = json.loads(raw)
    content = "\n".join(lines[2:]).strip()
    return ExperienceEntry(
        entry_id=meta.get("id", path.stem),
        title=meta.get("title", path.stem),
        content=content,
        source=meta.get("source", ""),
        company=meta.get("company", ""),
        position=meta.get("position", ""),
        created_at=meta.get("created_at", ""),
    )


def list_experiences(user_root: Path, user_id: str) -> list[ExperienceEntry]:
    d = experiences_dir(user_root, user_id)
    if not d.is_dir():
        return []
    out: list[ExperienceEntry] = []
    for p in sorted(d.glob("*.md")):
        e = read_experience(p)
        if e is not None:
            out.append(e)
    return out


def _find_experience_path(user_root: Path, entry_id: str) -> Path | None:
    """在用户命名空间下按 entry_id 查找面经文件（用于跨用户归属校验）。"""
    if not re.fullmatch(r"[\w\-]+", entry_id):
        return None
    for user_dir in user_root.iterdir():
        if not user_dir.is_dir():
            continue
        p = user_dir / "experiences" / f"{entry_id}.md"
        if p.is_file():
            return p
    return None


def delete_experience(user_root: Path, user_id: str, entry_id: str) -> bool:
    p = _find_experience_path(user_root, entry_id)
    if p is None:
        return False
    check_owner(p, user_id)
    p.unlink()
    return True


def save_experience_ref(session_dir: Path, user_id: str, index: int, entry: ExperienceEntry) -> Path:
    refs = session_dir / "references"
    refs.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", entry.title or f"ref{index}")[:30] or "ref"
    path = refs / f"{index}_{safe}.md"
    text = f"<!-- owner: {user_id} -->\n{_meta_line(entry)}\n{entry.content}\n"
    atomic_write(path, text)
    return path
