from interview_agent.models import SessionConfig, ExperienceEntry
from interview_agent.session import InterviewSession
from interview_agent.storage import read_jsonl


def test_start_writes_jd_references_and_prompt(tmp_path):
    refs = [ExperienceEntry(entry_id="e1", title="字节面经", content="高频问缓存", source="牛客")]
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1,
                        company="字节跳动", position="后端开发",
                        jd_text="熟悉 Go", experience_refs=refs)
    s = InterviewSession(cfg)
    s.start()
    assert (s.session_dir / "jd.md").is_file()
    ref_files = list((s.session_dir / "references").glob("*.md"))
    assert len(ref_files) == 1
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "字节跳动 后端开发" in prompt
    assert "熟悉 Go" in prompt
    assert "高频问缓存" in prompt
    meta = read_jsonl(s.transcript_path)[0]
    assert meta["company"] == "字节跳动"
    assert meta["position"] == "后端开发"
