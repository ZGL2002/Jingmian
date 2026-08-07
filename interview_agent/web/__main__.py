"""Web 入口：python -m interview_agent.web"""
from __future__ import annotations
import uvicorn
from ..config import load_config, require_api_key, require_web_token
from ..llm import create_llm
from .app import create_app


def main() -> None:
    cfg = load_config()
    api_key = require_api_key()
    require_web_token(cfg)
    llm = create_llm(api_key=api_key, model=cfg["model"])
    app = create_app(cfg, llm=llm)
    uvicorn.run(
        app,
        host=cfg["web_host"],
        port=cfg["web_port"],
        # 浏览器 SSE 长连接会让优雅关闭等待；超时后强制退出，保证 Ctrl+C 快速生效
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
