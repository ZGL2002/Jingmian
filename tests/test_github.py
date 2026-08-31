"""GitHub 抓取层测试：mock urlopen，验证白名单、预算、token、过滤与截断。"""
import base64
import io
import json
import urllib.error

import pytest

from interview_agent.github import GitHubClient, GitHubError


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class UrlopenSpy:
    """记录 Request 并按队列返回预设响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(json.dumps(item).encode())


def make_client(**kw):
    return GitHubClient(**kw)


def test_fetch_repo_parses_meta(monkeypatch):
    spy = UrlopenSpy([{"default_branch": "main", "description": "商城", "language": "Python", "stargazers_count": 7}])
    monkeypatch.setattr("interview_agent.github.urlopen", spy)
    meta = make_client().fetch_repo("alice/shop")
    assert spy.requests[0].full_url == "https://api.github.com/repos/alice/shop"
    assert meta["default_branch"] == "main"
    assert meta["stars"] == 7
    assert meta["language"] == "Python"


def test_token_sent_as_bearer_header(monkeypatch):
    spy = UrlopenSpy([{"default_branch": "main"}])
    monkeypatch.setattr("interview_agent.github.urlopen", spy)
    make_client(token="tok123").fetch_repo("a/b")
    assert spy.requests[0].headers.get("Authorization") == "Bearer tok123"


def test_invalid_slug_rejected_without_request(monkeypatch):
    spy = UrlopenSpy([])
    monkeypatch.setattr("interview_agent.github.urlopen", spy)
    for bad in ["../etc", "a", "a/../b", "a/b?c", "a/b/c"]:
        with pytest.raises(GitHubError):
            make_client().fetch_repo(bad)
    assert spy.requests == []


def test_host_allowlist_enforced(monkeypatch):
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([]))
    with pytest.raises(GitHubError):
        make_client()._get_json("https://evil.com/repos/a/b")


def test_request_budget_exceeded(monkeypatch):
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([
        {"default_branch": "main"}, {"default_branch": "main"},
    ]))
    c = make_client(max_requests=2)
    c.fetch_repo("a/b")
    c.fetch_repo("a/b")
    with pytest.raises(GitHubError, match="预算"):
        c.fetch_repo("a/b")


def test_http_404_wrapped(monkeypatch):
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([err]))
    with pytest.raises(GitHubError, match="404"):
        make_client().fetch_repo("a/missing")


def test_fetch_tree_filters_noise_and_binaries(monkeypatch):
    tree = {
        "tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "src/app.py", "type": "blob", "size": 100},
            {"path": "node_modules/x/y.js", "type": "blob", "size": 10},
            {"path": "vendor/z.go", "type": "blob", "size": 10},
            {"path": "dist/bundle.js", "type": "blob", "size": 10},
            {"path": ".git/config", "type": "blob", "size": 10},
            {"path": "assets/logo.png", "type": "blob", "size": 10},
            {"path": "big/data.csv", "type": "blob", "size": 10_000_000},
            {"path": "src", "type": "tree"},
        ]
    }
    spy = UrlopenSpy([{"default_branch": "main"}, tree])
    monkeypatch.setattr("interview_agent.github.urlopen", spy)
    c = make_client()
    meta = c.fetch_repo("a/b")
    paths = c.fetch_tree("a/b", meta["default_branch"])
    assert spy.requests[1].full_url.endswith("/repos/a/b/git/trees/main?recursive=1")
    assert paths == ["README.md", "src/app.py"]


def test_fetch_file_decodes_and_truncates(monkeypatch):
    content = "x" * 5000
    encoded = base64.b64encode(content.encode()).decode()
    spy = UrlopenSpy([
        {"default_branch": "main"},
        {"content": encoded, "encoding": "base64", "size": 5000},
    ])
    monkeypatch.setattr("interview_agent.github.urlopen", spy)
    c = make_client(max_file_bytes=1000)
    c.fetch_repo("a/b")
    text = c.fetch_file("a/b", "main", "src/app.py")
    assert spy.requests[1].full_url.endswith("/repos/a/b/contents/src/app.py?ref=main")
    assert text.startswith("x" * 1000)
    assert "截断" in text


def test_fetch_file_too_large_for_api(monkeypatch):
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([
        {"default_branch": "main"},
        {"content": None, "encoding": "none", "size": 9_000_000},
    ]))
    c = make_client()
    c.fetch_repo("a/b")
    with pytest.raises(GitHubError, match="过大"):
        c.fetch_file("a/b", "main", "big.bin")


def test_network_error_wrapped(monkeypatch):
    import urllib.request
    err = urllib.error.URLError("conn refused")
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([err]))
    with pytest.raises(GitHubError, match="网络"):
        make_client().fetch_repo("a/b")


def test_fetch_tree_marks_truncation(monkeypatch):
    tree = {"truncated": True, "tree": [{"path": "a.py", "type": "blob", "size": 1}]}
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([
        {"default_branch": "main"}, tree,
    ]))
    c = make_client()
    c.fetch_repo("a/b")
    paths = c.fetch_tree("a/b", "main")
    assert "a.py" in paths
    assert any("截断" in p for p in paths)


def test_fetch_file_empty_content_returns_empty(monkeypatch):
    monkeypatch.setattr("interview_agent.github.urlopen", UrlopenSpy([
        {"default_branch": "main"},
        {"content": "", "encoding": "base64", "size": 0},
    ]))
    c = make_client()
    c.fetch_repo("a/b")
    assert c.fetch_file("a/b", "main", "empty.py") == ""


def test_malformed_json_wrapped_as_github_error(monkeypatch):
    class BadResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("interview_agent.github.urlopen", lambda req, timeout=None: BadResponse(b"not json"))
    with pytest.raises(GitHubError):
        make_client().fetch_repo("a/b")
