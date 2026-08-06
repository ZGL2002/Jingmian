import pytest
from interview_agent.resume import extract_text, parse_resume

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
