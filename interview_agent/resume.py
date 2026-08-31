"""简历解析：抽取语言、技能、项目经历、GitHub 仓库链接。"""
from __future__ import annotations
import re
from pathlib import Path
from .models import ResumeDocument, ResumeProject

LANGUAGES = [
    "Python", "Java", "Go", "C++", "C#", "JavaScript", "TypeScript",
    "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Scala",
]
SKILL_KEYWORDS = [
    "Redis", "MySQL", "PostgreSQL", "MongoDB", "Kafka", "RabbitMQ",
    "Docker", "Kubernetes", "Linux", "Nginx", "Spring", "FastAPI",
    "Django", "Flask", "微服务", "分布式", "消息队列", "缓存", "高并发",
    "Elasticsearch", "ClickHouse", "AI", "大模型", "LangChain", "RAG",
    "gRPC", "REST",
]

# github.com/<owner>/<repo>；lookbehind 排除 gist.github.com 等子域。
# repo 部分贪婪匹配后由后处理剥离 .git 与紧邻的中英文标点（中文简历链接后常直接跟全角标点）
_GITHUB_REPO_RE = re.compile(
    r"(?<![\w.])github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9_.-]+)"
)
_REPO_TRAILING_PUNCT = ".,;:!?，。；：、）)】》」』"


def extract_github_repos(text: str, limit: int = 3) -> list[str]:
    """按出现顺序提取简历中的 GitHub 仓库 slug（owner/repo），去重、上限 limit。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _GITHUB_REPO_RE.finditer(text):
        owner = m.group(1)
        repo = m.group(2).rstrip(_REPO_TRAILING_PUNCT).removesuffix(".git")
        if not repo or not re.search(r"[A-Za-z0-9]", repo):
            continue
        slug = f"{owner}/{repo}"
        if slug.lower() not in seen:
            seen.add(slug.lower())
            out.append(slug)
    return out[:limit]


def github_url(slug: str) -> str:
    return f"https://github.com/{slug}"


def extract_text(source: str | Path) -> str:
    p = Path(source)
    if p.is_file():
        if p.suffix.lower() == ".pdf":
            return _extract_pdf(p)
        return p.read_text(encoding="utf-8", errors="replace")
    return source


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(text.strip()) < 20:
        raise ValueError("PDF 无文字层（可能是扫描版），请转为文本后重试")
    return text


def parse_resume(text: str) -> ResumeDocument:
    languages = [k for k in LANGUAGES if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", text)]
    skills = [k for k in SKILL_KEYWORDS if k in text]
    projects = _extract_projects(text)
    summary = " ".join(text.split())[:500]
    return ResumeDocument(
        raw_text=text, languages=languages, skills=skills, projects=projects,
        summary=summary, github_repos=extract_github_repos(text),
    )


def _extract_projects(text: str) -> list[ResumeProject]:
    out: list[ResumeProject] = []
    pattern = re.compile(r"(?:^|\n)\s*(?:[#*\-]*\s*)?项目(?:经历|经验|介绍)?\s*[:：]?\s*([^\n]+)", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for idx, m in enumerate(matches):
        name = m.group(1).strip()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        # 块截止到下一个项目标题（防止继承下一个项目的链接与技术栈），仍保留 500 字上限
        block = text[m.start(1) : min(end, m.end() + 500)]
        stack = [k for k in LANGUAGES + SKILL_KEYWORDS if k in block]
        slugs = extract_github_repos(block, limit=1)
        out.append(ResumeProject(
            name=name, description=block[:200].strip(), tech_stack=stack,
            github_url=github_url(slugs[0]) if slugs else "",
        ))
    return out
