"""飞书消息 API 薄封装：发文本 / PATCH 更新 / markdown 卡片，带重试。"""
from __future__ import annotations
import json
import time
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    PatchMessageRequest, PatchMessageRequestBody,
)


class FeishuAPIError(Exception):
    pass


def _content(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class LarkFeishuClient:
    """出站渲染器 v1：文本消息。未来语音在此加 TTS 渲染分支。"""

    def __init__(self, app_id: str, app_secret: str, api_client=None, retries: int = 3):
        self._retries = max(1, retries)
        self._api = api_client if api_client is not None else (
            lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        )

    def _request(self, fn):
        """指数退避重试；仍失败抛 FeishuAPIError。"""
        last: FeishuAPIError | None = None
        for attempt in range(self._retries):
            try:
                resp = fn()
                if resp.success():
                    return resp
                last = FeishuAPIError(f"飞书 API 错误: code={resp.code} msg={resp.msg}")
            except FeishuAPIError:
                raise
            except Exception as e:  # noqa: BLE001 - 网络/SDK 异常统一包装
                last = FeishuAPIError(f"飞书 API 调用失败: {e}")
            time.sleep(0.5 * (2 ** attempt))
        assert last is not None
        raise last

    def _create(self, open_id: str, msg_type: str, content: str):
        req = (CreateMessageRequest.builder()
               .receive_id_type("open_id")
               .request_body(CreateMessageRequestBody.builder()
                             .receive_id(open_id).msg_type(msg_type)
                             .content(content).build())
               .build())
        return self._request(lambda: self._api.im.v1.message.create(req))

    def send_text(self, open_id: str, text: str) -> str:
        resp = self._create(open_id, "text", _content({"text": text}))
        return resp.data.message_id

    def patch_text(self, message_id: str, text: str) -> bool:
        req = (PatchMessageRequest.builder()
               .message_id(message_id)
               .request_body(PatchMessageRequestBody.builder()
                             .content(_content({"text": text})).build())
               .build())
        try:
            self._request(lambda: self._api.im.v1.message.patch(req))
            return True
        except FeishuAPIError:
            return False

    def send_markdown_card(self, open_id: str, title: str, markdown_text: str) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [{"tag": "markdown", "content": markdown_text}],
        }
        self._create(open_id, "interactive", _content(card))


def require_feishu_credentials(cfg: dict) -> tuple[str, str]:
    app_id = cfg.get("feishu_app_id", "")
    app_secret = cfg.get("feishu_app_secret", "")
    if not app_id or not app_secret:
        raise KeyError("缺少飞书应用凭证：请在 .env 中配置 FEISHU_APP_ID 与 FEISHU_APP_SECRET")
    return app_id, app_secret
