import json
from pathlib import Path
from interview_agent.llm import AssistantTurn
from interview_agent.web.manager import SessionManager
from interview_agent.feishu.bot import EVALUATING_TEXT, FeishuBot
from feishu_mocks import MockFeishuClient, ScriptLLM, wait_until


def make_bot(tmp_path):
    cfg = {
        "session_root": str(tmp_path),
        "min_questions": 1,
        "language": "zh",
        "model": "deepseek-chat",
    }
    # 两个用户各需：开场 + 问题一 + 评估，共 6 个回合
    llm = ScriptLLM([AssistantTurn(content=c) for c in [
        "开场1", "问题1", "## 评估",
        "开场2", "问题2", "## 评估",
    ]])
    manager = SessionManager(cfg, llm, idle_timeout=60, sweep_interval=60)
    client = MockFeishuClient()
    return FeishuBot(manager, client), client


def start_via_onboarding(bot, open_id):
    bot.handle_message(open_id, "开始面试")
    bot.handle_message(open_id, "跳过")
    bot.handle_message(open_id, "跳过")
    bot.handle_message(open_id, "跳过")


def test_two_users_full_interview_isolated(tmp_path):
    bot, c = make_bot(tmp_path)
    u1, u2 = "ou_user1", "ou_user2"
    start_via_onboarding(bot, u1)
    start_via_onboarding(bot, u2)
    # 两个 runner 线程共享一个脚本 LLM，回合内容按调度顺序分配——断言只看
    # 每用户的回合补丁数与消息归属，不依赖具体回合文本。
    for u in (u1, u2):
        assert wait_until(lambda u=u: any("面试已开始" in t for t in c.texts_to(u)))
        # 开场回合已输出（busy 已释放）
        assert wait_until(lambda u=u: len(c.patched_to(u)) >= 1)
    bot.handle_message(u1, "回答甲")
    bot.handle_message(u2, "回答乙")
    for u in (u1, u2):
        # 本回合输出完成后再结束，避免撞上思考中
        assert wait_until(lambda u=u: len(c.patched_to(u)) >= 2)
        bot.handle_message(u, "结束")
        assert wait_until(lambda u=u: any(t == EVALUATING_TEXT for t in c.texts_to(u)))
        assert wait_until(lambda u=u: len([x for x in c.cards if x[0] == u]) == 1)

    # 存储隔离：各自的 transcript 只含自己的回答
    t1 = list(Path(tmp_path, u1).glob("*/transcript.jsonl"))
    t2 = list(Path(tmp_path, u2).glob("*/transcript.jsonl"))
    assert len(t1) == 1 and len(t2) == 1
    lines1 = [json.loads(l) for l in t1[0].read_text(encoding="utf-8").splitlines()]
    contents1 = [e.get("content") for e in lines1 if e.get("role") == "candidate"]
    assert "回答甲" in contents1
    assert "回答乙" not in contents1
    # 报告落盘
    assert list(Path(tmp_path, u1).glob("*/report.md"))
    # 结束后回到空闲
    bot.handle_message(u1, "你好")
    assert c.sent[-1][0] == u1
