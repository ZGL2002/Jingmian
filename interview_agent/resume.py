"""简历解析：抽取语言、技能、项目经历。"""
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
    return ResumeDocument(raw_text=text, languages=languages, skills=skills, projects=projects, summary=summary)


def _extract_projects(text: str) -> list[ResumeProject]:
    out: list[ResumeProject] = []
    pattern = re.compile(r"(?:^|\n)\s*(?:[#*\-]*\s*)?项目(?:经历|经验|介绍)?\s*[:：]?\s*([^\n]+)", re.MULTILINE)
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        block = text[m.end() : m.end() + 500]
        stack = [k for k in LANGUAGES + SKILL_KEYWORDS if k in block]
        out.append(ResumeProject(name=name, description=block[:200].strip(), tech_stack=stack))
    return out
