import json
from pathlib import Path
from types import SimpleNamespace
from interview_agent.feishu.bot import (
    THINKING_TEXT, IDLE_HINT, HELP_TEXT, EVALUATING_TEXT, EMPTY_TURN_TEXT,
    FeishuBot,
)
from interview_agent.feishu.onboarding import ASK_COMPANY
from feishu_mocks import FakeManager, FakeTask, MockFeishuClient


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


# ---------- 事件泵（Task 5）----------

def test_pump_turn_flow_placeholder_then_patch(tmp_path):
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    q = task.queue
    q.publish({"type": "status", "status": "thinking"})
    q.publish({"type": "delta", "text": "请"})
    q.publish({"type": "delta", "text": "介绍自己"})
    q.publish({"type": "turn_end", "question_count": 1})
    q.publish({"type": "status", "status": "evaluating"})
    (Path(tmp_path) / "report.md").write_text("## 报告", encoding="utf-8")
    q.publish({"type": "status", "status": "done", "report_url": "/x"})
    bot._pump("ou_1", "sess1", task)
    assert c.sent[0] == ("ou_1", THINKING_TEXT)
    assert c.patched == [("om_mock_1", "请介绍自己")]
    assert ("ou_1", EVALUATING_TEXT) in c.sent
    assert c.cards == [("ou_1", "面试评估报告", "## 报告")]
    assert "ou_1" not in bot._active  # 已清理


def test_pump_first_delta_creates_placeholder_without_thinking(tmp_path):
    """开场白没有 thinking 前导：首个 delta 也要先建占位。"""
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    q = task.queue
    q.publish({"type": "delta", "text": "你好，我是面试官"})
    q.publish({"type": "turn_end", "question_count": 0})
    q.publish({"type": "status", "status": "done", "report_url": "/x"})
    (Path(tmp_path) / "report.md").write_text("# r", encoding="utf-8")
    bot._pump("ou_1", "sess1", task)
    assert c.sent[0] == ("ou_1", THINKING_TEXT)
    assert c.patched == [("om_mock_1", "你好，我是面试官")]


def test_pump_patch_failure_falls_back_to_send(tmp_path):
    bot, m, c = make_bot()
    c.patch_ok = False
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    q = task.queue
    q.publish({"type": "delta", "text": "问题X"})
    q.publish({"type": "turn_end", "question_count": 1})
    q.publish({"type": "status", "status": "done", "report_url": "/x"})
    (Path(tmp_path) / "report.md").write_text("# r", encoding="utf-8")
    bot._pump("ou_1", "sess1", task)
    assert ("ou_1", "问题X") in c.sent


def test_pump_empty_turn_patches_fallback_text(tmp_path):
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    q = task.queue
    q.publish({"type": "status", "status": "thinking"})
    q.publish({"type": "turn_end", "question_count": 1})
    q.publish({"type": "status", "status": "done", "report_url": "/x"})
    (Path(tmp_path) / "report.md").write_text("# r", encoding="utf-8")
    bot._pump("ou_1", "sess1", task)
    assert c.patched[0][1] == EMPTY_TURN_TEXT


def test_pump_error_and_missing_report(tmp_path):
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    task.queue.publish({"type": "error", "message": "LLM 挂了"})
    bot._pump("ou_1", "sess1", task)
    assert any("LLM 挂了" in t for t in c.texts_to("ou_1"))
    assert "ou_1" not in bot._active
    # done 但报告缺失：提示去服务器看
    bot._active["ou_1"] = "sess2"
    task2 = FakeTask(tmp_path)
    task2.queue.publish({"type": "status", "status": "done", "report_url": "/x"})
    bot._pump("ou_1", "sess2", task2)
    assert any("报告" in t and "服务器" in t for t in c.texts_to("ou_1"))


def test_pump_task_ended_without_events_sends_notice(tmp_path):
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    task = FakeTask(tmp_path)
    task.ended = True  # 空闲回收：无 done/error 事件
    bot._pump("ou_1", "sess1", task)
    assert any("已结束" in t for t in c.texts_to("ou_1"))
    assert "ou_1" not in bot._active


def test_pump_none_task_exits_without_cleanup():
    bot, m, c = make_bot()
    bot._active["ou_1"] = "sess1"
    bot._pump("ou_1", "sess1", None)
    assert bot._active.get("ou_1") == "sess1"  # 不清理，交由后续路径处理
    assert c.sent == []


def _post_event(event_id, content_obj):
    ev = make_event(msg_type="post", text="", event_id=event_id)
    ev.event.message.content = json.dumps(content_obj, ensure_ascii=False)
    return ev


def test_handle_event_post_flattened_to_text():
    """富文本粘贴应拍平为纯文本进入对话，而不是被拒。"""
    bot, _, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    bot.handle_event(_post_event("e9", {
        "title": "自我介绍",
        "content": [
            [{"tag": "text", "text": "三年后端经验，"},
             {"tag": "a", "text": "项目主页", "href": "https://x.com"}],
            [{"tag": "text", "text": "熟悉 Redis"}],
        ],
    }))
    # 第一条引导输入被接受（公司名 = 拍平文本），进入问岗位
    assert any("岗位" in t for t in c.texts_to("ou_1"))


def test_handle_event_post_multiline_joins_paragraphs():
    """富文本多段落应按换行拼接，进入简历收集后保留多行结构。"""
    bot, m, c = make_bot()
    bot.handle_message("ou_1", "开始面试")
    bot.handle_message("ou_1", "字节")
    bot.handle_message("ou_1", "后端")
    bot.handle_event(_post_event("e10", {
        "content": [
            [{"tag": "text", "text": "技能A"}],
            [{"tag": "text", "text": "技能B"}],
        ],
    }))
    bot.handle_message("ou_1", "END")
    assert m.started[0]["resume_text"] == "技能A\n技能B"


def test_handle_event_post_media_only_still_unsupported():
    bot, _, c = make_bot()
    bot.handle_event(_post_event("e12", {
        "content": [[{"tag": "img", "image_key": "img_v2_x"}]],
    }))
    assert any("文字" in t for t in c.texts_to("ou_1"))
