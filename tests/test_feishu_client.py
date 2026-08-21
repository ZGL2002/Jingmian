import json
import pytest
from interview_agent.feishu.feishu_client import (
    FeishuAPIError, LarkFeishuClient, require_feishu_credentials,
)


class FakeApi:
    """仿 lark API 客户端：记录请求，可编程响应。"""

    def __init__(self, fail_times=0):
        self.im = self
        self.v1 = self
        self.message = self
        self.created: list = []
        self.patched: list = []
        self.fail_times = fail_times

    class _Resp:
        def __init__(self, ok, message_id="om_x"):
            self._ok, self._mid = ok, message_id
            self.code, self.msg = (0, "") if ok else (999, "频控")

        def success(self):
            return self._ok

        @property
        def data(self):
            return type("D", (), {"message_id": self._mid})()

    def create(self, req):
        self.created.append(req)
        if self.fail_times > 0:
            self.fail_times -= 1
            return self._Resp(False)
        return self._Resp(True)

    def patch(self, req):
        self.patched.append(req)
        if self.fail_times > 0:
            self.fail_times -= 1
            return self._Resp(False)
        return self._Resp(True)


def make_client(api):
    return LarkFeishuClient("cli_x", "sec", api_client=api, retries=1)


def test_require_feishu_credentials_missing():
    with pytest.raises(KeyError):
        require_feishu_credentials({})


def test_require_feishu_credentials_ok():
    assert require_feishu_credentials(
        {"feishu_app_id": "cli_x", "feishu_app_secret": "s"}
    ) == ("cli_x", "s")


def test_send_text_returns_message_id():
    api = FakeApi()
    mid = make_client(api).send_text("ou_1", "你好")
    assert mid == "om_x"
    req = api.created[0]
    assert req.body.receive_id == "ou_1"
    assert req.body.msg_type == "text"
    assert json.loads(req.body.content)["text"] == "你好"


def test_send_text_retries_then_raises():
    api = FakeApi(fail_times=5)
    with pytest.raises(FeishuAPIError):
        make_client(api).send_text("ou_1", "你好")


def test_patch_text_returns_false_on_failure():
    api = FakeApi(fail_times=5)
    assert make_client(api).patch_text("om_x", "更新") is False


def test_send_markdown_card_builds_interactive_card():
    api = FakeApi()
    make_client(api).send_markdown_card("ou_1", "面试评估报告", "## 报告")
    req = api.created[0]
    assert req.body.msg_type == "interactive"
    card = json.loads(req.body.content)
    assert card["header"]["title"]["content"] == "面试评估报告"
    assert card["elements"][0]["tag"] == "markdown"
    assert card["elements"][0]["content"] == "## 报告"
