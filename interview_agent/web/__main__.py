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
    uvicorn.run(app, host=cfg["web_host"], port=cfg["web_port"])


if __name__ == "__main__":
    main()
