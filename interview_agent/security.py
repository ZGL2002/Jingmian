"""路径安全与归属校验。"""
from __future__ import annotations
import json
from pathlib import Path


class PathPolicyError(Exception):
    """路径越界或归属校验失败。"""


class PathPolicy:
    def __init__(self, roots: list[Path]):
        self.roots = [r.resolve() for r in roots]

    def resolve(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self.roots[0] / p
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self.roots):
            raise PathPolicyError(f"路径越界: {raw}")
        return p


def _first_line(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    return lines[0] if lines else ""


def read_owner(path: Path) -> str | None:
    line = _first_line(path)
    if line.startswith("{"):
        try:
            return json.loads(line).get("user_id")
        except json.JSONDecodeError:
            return None
    if line.startswith("<!-- owner:"):
        return line[len("<!-- owner:") :].strip().removesuffix("-->").strip()
    return None


def check_owner(path: Path, expected: str) -> None:
    owner = read_owner(path)
    if owner != expected:
        raise PathPolicyError(f"文件归属校验失败: {path} (期望 {expected}, 实际 {owner})")


def check_owner_if_marked(path: Path, expected: str) -> None:
    """若文件带有 owner 标记则校验归属；无标记视为会话内草稿文件放行。"""
    if not path.exists():
        return
    line = _first_line(path)
    if line.startswith("<!-- owner:") or line.startswith("{"):
        check_owner(path, expected)


def contains_secret(text: str, secret: str) -> bool:
    return bool(secret) and secret in text
