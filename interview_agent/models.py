"""核心数据模型。"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SessionState(Enum):
    READY = "ready"
    OPENING = "opening"
    QUESTIONING = "questioning"
    WRAPPING = "wrapping"
    EVALUATING = "evaluating"
    DONE = "done"


@dataclass
class ResumeProject:
    name: str
    description: str = ""
    tech_stack: list[str] = field(default_factory=list)
    github_url: str = ""


@dataclass
class ResumeDocument:
    raw_text: str
    languages: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    projects: list[ResumeProject] = field(default_factory=list)
    summary: str = ""
    github_repos: list[str] = field(default_factory=list)


@dataclass
class RepoAnalysis:
    """单个 GitHub 仓库的分析结果（ok=有分析文本，skipped=降级跳过）。"""
    slug: str
    status: str = "ok"
    reason: str = ""
    text: str = ""


@dataclass
class SessionConfig:
    user_id: str
    session_root: Path
    min_questions: int = 20
    language: str = "zh"
    model: str = "deepseek-chat"
    answer_offload_threshold: int = 100_000
    context_safety_ratio: float = 0.8
    max_context_chars: int = 60_000
    keep_recent_messages: int = 30
    company: str = ""
    position: str = ""
    jd_text: str = ""
    experience_refs: list[ExperienceEntry] = field(default_factory=list)
    style: str = "serious"
    github_analysis_enabled: bool = True
    github_token: str = ""
    github_max_repos: int = 3


@dataclass
class ExperienceEntry:
    entry_id: str
    title: str
    content: str
    source: str = ""
    company: str = ""
    position: str = ""
    created_at: str = ""
