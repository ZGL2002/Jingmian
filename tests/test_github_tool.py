"""面试中实时读码工具测试：缓存优先、仓库白名单、路径校验、未缓存回源。"""
import json

from interview_agent.tools import default_registry
from interview_agent.tools.base import ToolContext
from interview_agent.security import PathPolicy


class BoomFetcher:
    """缓存命中时不应触网；误触网直接报错暴露。"""

    def __call__(self, slug, ref, path, token=""):
        raise AssertionError(f"不应发起网络抓取: {slug}/{path}")


class StubFetcher:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def __call__(self, slug, ref, path, token=""):
        self.calls.append((slug, ref, path, token))
        return self.files[path]


def make_ctx(tmp_path):
    d = tmp_path / "s"
    d.mkdir(exist_ok=True)
    return ToolContext(
        user_id="alice", session_dir=d,
        policy=PathPolicy([tmp_path]), transcript_path=d / "t.jsonl",
        wrap_allowed=lambda: True,
    )


def make_repo(root, slug="alice/shop", status="ok", ref="main", reason="", cached=None, tree=None):
    d = root / "s" / "repos" / slug.replace("/", "__")
    (d / "files").mkdir(parents=True, exist_ok=True)
    meta = {"slug": slug, "status": status}
    if ref:
        meta["ref"] = ref
    if reason:
        meta["reason"] = reason
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "tree.txt").write_text("\n".join(tree if tree is not None else ["README.md", "src/app.py"]), encoding="utf-8")
    for rel, text in ({"README.md": "# shop"} if cached is None else cached).items():
        p = d / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def test_registry_includes_github_tools():
    names = {t["function"]["name"] for t in default_registry().schemas()}
    assert {"list_github_files", "read_github_file"} <= names


def test_list_github_files_reads_cache(tmp_path):
    make_repo(tmp_path)
    ctx = make_ctx(tmp_path)
    reg = default_registry()
    out = reg.execute("list_github_files", {}, ctx)
    assert "alice/shop" in out
    assert "README.md" in out
    assert "src/app.py" in out


def test_list_github_files_empty_when_no_repos(tmp_path):
    ctx = make_ctx(tmp_path)
    out = default_registry().execute("list_github_files", {}, ctx)
    assert "未包含" in out or "没有" in out


def test_read_github_file_prefers_cache_without_network(tmp_path):
    from interview_agent.tools.github_tool import build_github_tools

    make_repo(tmp_path)
    reg = default_registry()
    for t in build_github_tools(fetch_file=BoomFetcher()):
        reg.register(t)
    ctx = make_ctx(tmp_path)
    out = reg.execute("read_github_file", {"path": "README.md", "repo": "alice/shop"}, ctx)
    assert out == "# shop"


def test_read_github_file_rejects_unknown_repo(tmp_path):
    make_repo(tmp_path)
    ctx = make_ctx(tmp_path)
    out = default_registry().execute(
        "read_github_file", {"path": "README.md", "repo": "mallory/evil"}, ctx
    )
    assert "不在本场分析范围" in out


def test_read_github_file_rejects_traversal(tmp_path):
    make_repo(tmp_path)
    ctx = make_ctx(tmp_path)
    out = default_registry().execute(
        "read_github_file", {"path": "../../etc/passwd", "repo": "alice/shop"}, ctx
    )
    assert "非法" in out


def test_read_github_file_fetches_on_cache_miss_and_persists(tmp_path):
    from interview_agent.tools.github_tool import build_github_tools

    make_repo(tmp_path)
    fetcher = StubFetcher({"src/app.py": "print(1)"})
    reg = default_registry()
    for t in build_github_tools(fetch_file=fetcher):
        reg.register(t)
    ctx = make_ctx(tmp_path)
    out = reg.execute("read_github_file", {"path": "src/app.py", "repo": "alice/shop"}, ctx)
    assert out == "print(1)"
    assert fetcher.calls == [("alice/shop", "main", "src/app.py", "")]
    cached = tmp_path / "s" / "repos" / "alice__shop" / "files" / "src" / "app.py"
    assert cached.read_text(encoding="utf-8") == "print(1)"
    assert reg.execute("read_github_file", {"path": "src/app.py", "repo": "alice/shop"}, ctx) == "print(1)"
    assert len(fetcher.calls) == 1  # 第二次走缓存


def test_read_github_file_skipped_repo_returns_reason(tmp_path):
    make_repo(tmp_path, status="skipped", ref="", reason="网络错误", cached={})
    ctx = make_ctx(tmp_path)
    out = default_registry().execute(
        "read_github_file", {"path": "README.md", "repo": "alice/shop"}, ctx
    )
    assert "未成功" in out and "网络错误" in out


def test_read_github_file_requires_repo_when_multiple_and_uncached(tmp_path):
    make_repo(tmp_path, slug="alice/shop", cached={})
    make_repo(tmp_path, slug="bob/demo", cached={})
    ctx = make_ctx(tmp_path)
    out = default_registry().execute("read_github_file", {"path": "README.md"}, ctx)
    assert "repo" in out and "错误" in out
