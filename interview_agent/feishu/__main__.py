"""飞书渠道入口：python -m interview_agent.feishu（长连接模式，无需公网 IP）。"""
from __future__ import annotations
import os
from ..config import load_config, require_api_key
from ..llm import create_llm
from ..web.manager import SessionManager
from .bot import FeishuBot
from .feishu_client import LarkFeishuClient, require_feishu_credentials


def _run_ws(app_id: str, app_secret: str, handler) -> None:
    """阻塞运行长连接；Ctrl+C 时捕获中断、干净退出（SDK 自身会打印栈回溯）。"""
    import lark_oapi as lark
    log_level = (lark.LogLevel.DEBUG
                 if os.environ.get("INTERVIEW_FEISHU_LOG", "").upper() == "DEBUG"
                 else lark.LogLevel.INFO)
    ws = lark.ws.Client(
        app_id, app_secret, event_handler=handler, log_level=log_level
    )
    try:
        ws.start()
    except KeyboardInterrupt:
        print("\n飞书机器人已停止（收到 Ctrl+C）。")


def main() -> None:
    cfg = load_config()
    app_id, app_secret = require_feishu_credentials(cfg)
    api_key = require_api_key()
    llm = create_llm(
        api_key, cfg.get("model", "deepseek-chat"),
        provider=cfg.get("provider", "deepseek"),
    )
    manager = SessionManager(cfg, llm)
    bot = FeishuBot(manager, LarkFeishuClient(app_id, app_secret))

    import lark_oapi as lark
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(bot.handle_event)
        .build()
    )
    print("飞书机器人已启动（长连接模式），等待私聊消息… Ctrl+C 退出")
    _run_ws(app_id, app_secret, handler)


if __name__ == "__main__":
    main()
