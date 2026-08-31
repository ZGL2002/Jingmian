import pytest
from interview_agent.resume import extract_github_repos, extract_text, parse_resume


def test_extract_github_repos_basic_and_dedup():
    text = "项目一 https://github.com/alice/shop 后端；项目二见 github.com/alice/shop 。"
    assert extract_github_repos(text) == ["alice/shop"]


def test_extract_github_repos_strips_suffixes():
    text = "https://github.com/bob/demo.git 和 https://github.com/bob/demo/issues/1 是同一个仓库"
    assert extract_github_repos(text) == ["bob/demo"]


def test_extract_github_repos_ignores_non_repo_urls():
    text = "https://github.com/ 个人主页 https://gitee.com/a/b https://gist.github.com/x/1 github.com/alice"
    assert extract_github_repos(text) == []


def test_extract_github_repos_limit():
    text = "\n".join(f"https://github.com/u{i}/r{i}" for i in range(5))
    assert extract_github_repos(text, limit=2) == ["u0/r0", "u1/r1"]


def test_extract_github_repos_rejects_invalid_slug_chars():
    assert extract_github_repos("https://github.com/../etc") == []
    assert extract_github_repos("https://github.com/a/b.git") == ["a/b"]


def test_parse_resume_records_github_repos_and_project_url():
    text = (
        "项目经历\n项目A：电商后端 https://github.com/alice/shop\n"
        "用 Python 实现\n个人项目 https://github.com/bob/demo"
    )
    doc = parse_resume(text)
    assert doc.github_repos == ["alice/shop", "bob/demo"]
    urls = [p.github_url for p in doc.projects if p.github_url]
    assert "https://github.com/alice/shop" in urls

def test_extract_text_from_file(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("简历内容", encoding="utf-8")
    assert extract_text(f) == "简历内容"

def test_extract_text_pasted():
    assert extract_text("直接粘贴的内容") == "直接粘贴的内容"

def test_extract_text_scanned_pdf_rejected(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    pdf = tmp_path / "scan.pdf"
    with pdf.open("wb") as f:
        w.write(f)
    with pytest.raises(ValueError):
        extract_text(pdf)

def test_parse_resume_languages():
    doc = parse_resume("熟悉 Python 和 Go，做过后端开发")
    assert "Python" in doc.languages
    assert "Go" in doc.languages

def test_parse_resume_skills():
    doc = parse_resume("使用过 Redis、MySQL、Docker")
    assert "Redis" in doc.skills
    assert "MySQL" in doc.skills

def test_parse_resume_projects():
    text = "## 项目经历\n项目A：订单系统\n使用 Python、Redis 实现高并发订单处理"
    doc = parse_resume(text)
    assert len(doc.projects) >= 1
    assert doc.projects[0].name

def test_parse_resume_empty():
    doc = parse_resume("")
    assert doc.languages == []
    assert doc.skills == []
    assert doc.projects == []


def test_extract_github_repos_adjacent_cjk_punctuation():
    assert extract_github_repos("（https://github.com/alice/shop）") == ["alice/shop"]
    assert extract_github_repos("项目见 https://github.com/alice/shop。已开源") == ["alice/shop"]
    assert extract_github_repos("https://github.com/bob/demo，含 Redis") == ["bob/demo"]
    assert extract_github_repos("https://github.com/bob/demo?tab=readme") == ["bob/demo"]
    assert extract_github_repos("https://github.com/bob/demo#readme") == ["bob/demo"]


def test_project_block_does_not_inherit_next_project_url():
    text = (
        "项目经历\n项目A：内部系统（无链接）\n技术栈 Python\n"
        "项目B：开源商城 https://github.com/bob/demo\n技术栈 Go"
    )
    doc = parse_resume(text)
    assert doc.github_repos == ["bob/demo"]
    assert doc.projects[0].github_url == ""
    assert doc.projects[1].github_url == "https://github.com/bob/demo"
