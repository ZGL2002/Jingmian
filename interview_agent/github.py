"""GitHub 仓库抓取层：只读 REST API、宿主白名单、请求预算与文件过滤。"""
from __future__ import annotations
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from urllib.request import urlopen

API_BASE = "https://api.github.com"
API_HOSTS = {"api.github.com"}

_SLUG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+$")

# 依赖目录、二进制/资源文件不进入分析，避免浪费预算
NOISE_DIRS = (
    "node_modules/", "vendor/", "dist/", "build/", ".git/", "__pycache__/",
    ".venv/", "venv/", "target/", ".idea/", ".vscode/", "assets/",
)
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tar", ".jar", ".class", ".pyc",
    ".so", ".dylib", ".dll", ".exe", ".bin",
    ".mp3", ".mp4", ".webm", ".mov", ".avi", ".csv", ".parquet",
}


class GitHubError(Exception):
    """仓库抓取失败（网络、限流、私有、预算耗尽等），上层据此降级跳过。"""


def check_slug(slug: str) -> str:
    if not _SLUG_RE.fullmatch(slug):
        raise GitHubError(f"非法仓库标识: {slug!r}")
    return slug


@dataclass
class GitHubClient:
    token: str = ""
    timeout: float = 10.0
    max_requests: int = 30
    max_file_bytes: int = 100_000
    requests_used: int = field(default=0, repr=False)

    def _get_json(self, url: str) -> dict:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in API_HOSTS:
            raise GitHubError(f"非白名单主机: {parsed.hostname}")
        if self.requests_used >= self.max_requests:
            raise GitHubError(f"请求预算耗尽（{self.max_requests} 次），停止抓取")
        req = urllib.request.Request(url, headers=self._headers())
        self.requests_used += 1
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise GitHubError(f"仓库或文件不存在（404）: {url}") from None
            if e.code == 403:
                raise GitHubError("GitHub 拒绝访问（403，可能限流或私有仓库，可配置 GITHUB_TOKEN）") from None
            raise GitHubError(f"GitHub 请求失败（{e.code}）: {url}") from None
        except urllib.error.URLError as e:
            raise GitHubError(f"网络错误，无法访问 GitHub: {e.reason}") from None
        except TimeoutError:
            raise GitHubError("网络错误，访问 GitHub 超时") from None
        except json.JSONDecodeError:
            raise GitHubError(f"GitHub 响应解析失败: {url}") from None

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "interview-agent"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch_repo(self, slug: str) -> dict:
        data = self._get_json(f"{API_BASE}/repos/{check_slug(slug)}")
        return {
            "slug": slug,
            "default_branch": data.get("default_branch") or "main",
            "description": data.get("description") or "",
            "language": data.get("language") or "",
            "stars": data.get("stargazers_count") or 0,
        }

    def fetch_tree(self, slug: str, ref: str) -> list[str]:
        check_slug(slug)
        quoted_ref = urllib.parse.quote(ref, safe="")
        data = self._get_json(f"{API_BASE}/repos/{slug}/git/trees/{quoted_ref}?recursive=1")
        out = []
        for entry in data.get("tree", []):
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            if any(path.startswith(d) or f"/{d}" in path for d in NOISE_DIRS):
                continue
            basename = path.rsplit("/", 1)[-1]
            if "." in basename and basename.rsplit(".", 1)[-1].lower() in BINARY_EXTS:
                continue
            size = entry.get("size") or 0
            if size > self.max_file_bytes:
                continue
            out.append(path)
        if data.get("truncated"):
            out.append("…（文件树过大被 GitHub 截断，以上仅为部分文件）")
        return sorted(out)

    def fetch_file(self, slug: str, ref: str, path: str) -> str:
        check_slug(slug)
        if not path or path.startswith(("/", "\\")) or ".." in path.split("/"):
            raise GitHubError(f"非法文件路径: {path!r}")
        quoted = urllib.parse.quote(path, safe="/")
        data = self._get_json(f"{API_BASE}/repos/{slug}/contents/{quoted}?ref={ref}")
        if data.get("content") is None:
            raise GitHubError(f"文件过大或不可读（size={data.get('size')}）: {path}")
        if not data["content"]:
            return ""
        raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        if len(raw) > self.max_file_bytes:
            raw = raw[: self.max_file_bytes] + "\n…（文件过大，已截断）"
        return raw
