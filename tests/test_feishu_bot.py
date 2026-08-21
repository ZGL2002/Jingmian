import json
from types import SimpleNamespace
from interview_agent.feishu.bot import (
    THINKING_TEXT, IDLE_HINT, HELP_TEXT, FeishuBot,
)
from interview_agent.feishu.onboarding import ASK_COMPANY
from feishu_mocks import FakeManager, MockFeishuClient


def make_bot():
    m = FakeManager()
    c = MockFeishuClient()
    return FeishuBot(m, c), m, c


def make_event(open_id="ou_1", text="你好", chat_type="p2p",
               msg_type="text", event_id="ev_1"):
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_type=chat_type, message_type=msg_type,
                content=json.dumps({"text": text}, ensure_ascii=False),
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=open_id)),
        ),
    )


def test_help_and_idle_hint():
    bot, _, c = make_bot()
    bot.handle_message("ou_1", "帮助")
    assert c.sent[-1] == ("ou_1", HELP_TEXT)
    bot.handle_message("ou_1", "随便说点什么")
    assert c.sent[-1] == ("ou_1", IDLE_HINT)


def test_start_then_full_onboarding_creates_session():
    bot, m, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    assert c.sent[-1] == ("ou_1", ASK_COMPANY)
    bot.handle_message("ou_1", "字节")
    bot.handle_message("ou_1", "后端开发")
    bot.handle_message("ou_1", "熟悉 Python")
    bot.handle_message("ou_1", "END")
    assert len(m.started) == 1
    assert m.started[0]["company"] == "字节"
    assert m.started[0]["position"] == "后端开发"
    assert m.started[0]["resume_text"] == "熟悉 Python"


def test_start_while_active_prompts_end_first():
    bot, m, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    for t in ("跳过", "跳过", "跳过"):
        bot.handle_message("ou_1", t)
    assert len(m.started) == 1
    bot.handle_message("ou_1", "开始面试")
    assert len(m.started) == 1
    assert any("先" in t and "结束" in t for t in c.texts_to("ou_1"))


def test_answer_and_end_route_to_manager():
    bot, m, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    for t in ("跳过", "跳过", "跳过"):
        bot.handle_message("ou_1", t)
    sid = f"sess{len(m.started)}"
    bot.handle_message("ou_1", "我的回答")
    assert m.answers == [("ou_1", sid, "我的回答")]
    bot.handle_message("ou_1", "结束")
    assert m.ends == [("ou_1", sid)]
    # 注：结束后活跃状态的清理由事件泵在 done/error 时执行，Task 5/7 覆盖


def test_cancel_and_end_during_onboarding():
    bot, _, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    bot.handle_message("ou_1", "取消")
    bot.handle_message("ou_1", "随便说")
    assert c.sent[-1] == ("ou_1", IDLE_HINT)  # 引导已取消，回到空闲提示
    bot.handle_message("ou_1", "开始面试")
    bot.handle_message("ou_1", "结束")
    bot.handle_message("ou_1", "随便说")
    assert c.sent[-1] == ("ou_1", IDLE_HINT)


def test_busy_answer_gets_hint():
    bot, m, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    for t in ("跳过", "跳过", "跳过"):
        bot.handle_message("ou_1", t)
    m.submit_error = RuntimeError("面试官思考中，请稍候")
    bot.handle_message("ou_1", "我的回答")
    assert any("思考中" in t for t in c.texts_to("ou_1"))


def test_handle_event_filters_group_and_non_text_and_duplicates():
    bot, _, c = make_bot()
    bot.handle_event(make_event(chat_type="group", event_id="e1"))
    assert c.sent == []  # 群聊静默忽略
    bot.handle_event(make_event(msg_type="audio", event_id="e2"))
    assert any("文字" in t for t in c.texts_to("ou_1"))
    n = len(c.sent)
    bot.handle_event(make_event(text="帮助", event_id="e3"))
    assert c.sent[-1] == ("ou_1", HELP_TEXT)
    bot.handle_event(make_event(text="帮助", event_id="e3"))  # 重推
    assert len(c.sent) == n + 1


def test_handle_event_malformed_is_ignored():
    bot, _, c = make_bot()
    bot.handle_event(SimpleNamespace(header=None))
    bot.handle_event("not an event")
    assert c.sent == []
