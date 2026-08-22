"""飞书入口：Ctrl+C 优雅退出（捕获 KeyboardInterrupt，不打印栈回溯）。"""
import lark_oapi as lark
from interview_agent.feishu.__main__ import _run_ws


class FakeWsClient:
    """start() 直接模拟被 Ctrl+C 打断。"""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise KeyboardInterrupt


def test_run_ws_keyboard_interrupt_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(lark.ws, "Client", FakeWsClient)
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(lambda ev: None).build())
    _run_ws("cli_x", "sec", handler)  # 不应抛出异常
    out = capsys.readouterr().out
    assert "已停止" in out
