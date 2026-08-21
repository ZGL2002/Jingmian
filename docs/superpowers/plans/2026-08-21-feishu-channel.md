# 飞书渠道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 企业成员在飞书私聊机器人完成"对话式引导 → 问答 → 结束 → 卡片报告"的完整模拟面试，Agent 核心与 Web 层零改动。

**Architecture:** 新增 `interview_agent/feishu/` 适配器包：`lark-oapi` SDK 长连接接收私聊消息 → `FeishuBot` 分发（命令/引导/面试/空闲四语境）→ 复用 `web/` 包内渠道无关的 `SessionManager`/`InterviewTask`/`EventQueue`；每场面试一个事件泵线程把事件队列翻译成飞书消息（"思考中"占位 + PATCH 完整内容）。入站消息规范化与出站渲染两个边界为未来语音（ASR/TTS）预留。

**Tech Stack:** Python 3.11+、`lark-oapi>=1.4`（WS 长连接 + 消息 API）、pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-feishu-channel-design.md`（本计划从 spec 出发，执行者需同时阅读 spec）

## Global Constraints

- Agent 核心（`agent.py`/`session.py`/`llm.py`/`evaluate.py`/`storage.py` 等）与 `interview_agent/web/` 包**零改动**；只允许改：`config.py`、`.env.example`、`pyproject.toml`、`README.md`、新增 `interview_agent/feishu/` 包、新增 `tests/` 测试文件。
- 依赖只新增 `lark-oapi>=1.4`（主依赖）。
- 配置键名固定：`FEISHU_APP_ID` / `FEISHU_APP_SECRET`；配置读取键 `feishu_app_id` / `feishu_app_secret`。
- v1 只处理 `chat_type == "p2p"` 且 `message_type == "text"` 的消息；其余按 spec §8 处理。
- 单用户（open_id）同时仅一场活跃面试。
- 命令精确匹配（strip 后全等），命令集：`开始面试`、`结束`/`exit`/`/end`、`帮助`/`help`、`取消`、`跳过`、`END`。
- 发送失败指数退避重试 3 次后抛错由上层跳过并记日志；PATCH 失败降级为直接发新消息。
- 事件去重按 `event_id`，LRU 容量 1000。
- 所有用户可见文案、代码注释、docstring 用中文（与现有代码一致）。
- 每个任务结束必须跑该任务测试 + `pytest -q` 全量回归通过后才能 commit。

## 文件结构

```
interview_agent/feishu/
  ├─ __init__.py          # 空包标记
  ├─ feishu_client.py     # FeishuAPIError + LarkFeishuClient（发文本/PATCH/卡片，重试）+ require_feishu_credentials
  ├─ onboarding.py        # OnboardingFlow 纯逻辑状态机 + OnboardingResult
  ├─ bot.py               # FeishuBot：事件规范化/去重/分发 + 事件泵 + 报告卡片
  └─ __main__.py          # 入口：装配 + lark.ws.Client 启动
tests/
  ├─ feishu_mocks.py      # 共享测试替身：MockFeishuClient / FakeManager / FakeTask / ScriptLLM / wait_until
  ├─ test_feishu_client.py
  ├─ test_feishu_onboarding.py
  ├─ test_feishu_bot.py
  └─ test_feishu_integration.py
修改：interview_agent/config.py、.env.example、pyproject.toml、README.md、tests/test_config.py
```

---

### Task 1: 配置与依赖

**Files:**
- Modify: `interview_agent/config.py`（`load_config` 返回 dict 内加两项）
- Modify: `.env.example`（追加飞书段）
- Modify: `pyproject.toml`（主依赖加 `lark-oapi>=1.4`）
- Test: `tests/test_config.py`（追加两个测试）

**Interfaces:**
- Produces: `load_config()` 返回 dict 新增键 `"feishu_app_id": str`、`"feishu_app_secret": str`（默认空串），后续任务按这两个键名读取。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 末尾追加（沿用该文件现有的 `load_config` 导入方式）：

```python
def test_load_config_feishu_keys(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    cfg = load_config(str(env_file))
    assert cfg["feishu_app_id"] == ""
    assert cfg["feishu_app_secret"] == ""


def test_load_config_feishu_keys_from_env(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test123")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    cfg = load_config(str(env_file))
    assert cfg["feishu_app_id"] == "cli_test123"
    assert cfg["feishu_app_secret"] == "sec_test"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_config.py -q`
Expected: FAIL（KeyError: 'feishu_app_id'）

- [ ] **Step 3: 最小实现**

`interview_agent/config.py` 的 `load_config` 返回 dict 中、`"web_host"` 行之前插入：

```python
        "feishu_app_id": os.environ.get("FEISHU_APP_ID", ""),
        "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", ""),
```

`.env.example` 末尾追加：

```bash

# 飞书渠道（可选：启用 python -m interview_agent.feishu 时必填）
# 开放平台 → 企业自建应用 → 凭证与基础信息 中获取
FEISHU_APP_ID=cli_请替换
FEISHU_APP_SECRET=请替换
```

`pyproject.toml` `dependencies` 列表追加：`"lark-oapi>=1.4",`，然后执行 `python -m pip install -e ".[dev]"` 安装。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_config.py -q && pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add interview_agent/config.py .env.example pyproject.toml tests/test_config.py
git commit -m "feat: 飞书渠道配置项与 lark-oapi 依赖"
```

---

### Task 2: FeishuClient 封装

**Files:**
- Create: `interview_agent/feishu/__init__.py`（空文件）
- Create: `interview_agent/feishu/feishu_client.py`
- Test: `tests/test_feishu_client.py`

**Interfaces:**
- Produces（后续任务依赖的确切签名）:
  - `class FeishuAPIError(Exception)`
  - `class LarkFeishuClient`：`__init__(self, app_id: str, app_secret: str, api_client=None, retries: int = 3)`（`api_client` 为可注入的 lark API 客户端，测试用）；`send_text(self, open_id: str, text: str) -> str`（返回 message_id，失败重试后仍失败抛 `FeishuAPIError`）；`patch_text(self, message_id: str, text: str) -> bool`（失败返回 False 不抛）；`send_markdown_card(self, open_id: str, title: str, markdown_text: str) -> None`
  - `def require_feishu_credentials(cfg: dict) -> tuple[str, str]`（缺失抛 `KeyError`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_feishu_client.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_feishu_client.py -q`
Expected: FAIL（ModuleNotFoundError: interview_agent.feishu）

- [ ] **Step 3: 最小实现**

创建 `interview_agent/feishu/__init__.py`（空）与 `interview_agent/feishu/feishu_client.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_feishu_client.py -q && pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add interview_agent/feishu/ tests/test_feishu_client.py
git commit -m "feat: 飞书消息客户端封装（发文本/PATCH/卡片+重试）"
```

---

### Task 3: OnboardingFlow 引导状态机

**Files:**
- Create: `interview_agent/feishu/onboarding.py`
- Test: `tests/test_feishu_onboarding.py`

**Interfaces:**
- Produces:
  - `@dataclass OnboardingResult`：字段 `company: str = ""`、`position: str = ""`、`resume_text: str = ""`
  - `class OnboardingFlow`：`feed(self, text: str) -> tuple[list[str], OnboardingResult | None]`（返回待发回复与完成结果）；属性 `opening_question: str`
  - 常量文案 `ASK_COMPANY` / `ASK_POSITION` / `ASK_RESUME`（bot 直接引用展示）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_feishu_onboarding.py`：

```python
from interview_agent.feishu.onboarding import (
    ASK_COMPANY, ASK_POSITION, ASK_RESUME, OnboardingFlow,
)


def test_opening_question():
    assert OnboardingFlow().opening_question == ASK_COMPANY


def test_full_path_company_position_resume():
    f = OnboardingFlow()
    replies, r1 = f.feed("字节")
    assert replies == [ASK_POSITION] and r1 is None
    replies, r2 = f.feed("后端开发")
    assert replies == [ASK_RESUME] and r2 is None
    replies, done = f.feed("熟悉 Python")
    assert replies == [] and done is None
    replies, done = f.feed("做过订单系统")
    assert replies == [] and done is None
    replies, done = f.feed("END")
    assert done is not None
    assert done.company == "字节"
    assert done.position == "后端开发"
    assert done.resume_text == "熟悉 Python\n做过订单系统"


def test_skip_everything():
    f = OnboardingFlow()
    f.feed("跳过")
    f.feed("跳过")
    replies, done = f.feed("跳过")
    assert done is not None
    assert done.company == "" and done.position == "" and done.resume_text == ""
    assert replies  # 有确认文案


def test_end_inside_single_message_truncates():
    f = OnboardingFlow()
    f.feed("字节")
    f.feed("后端开发")
    _, done = f.feed("第一行\n第二行\nEND\n不该出现")
    assert done is not None
    assert done.resume_text == "第一行\n第二行"


def test_end_as_first_resume_message_is_no_resume_mode():
    f = OnboardingFlow()
    f.feed("跳过")
    f.feed("跳过")
    _, done = f.feed("END")
    assert done is not None and done.resume_text == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_feishu_onboarding.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 最小实现**

创建 `interview_agent/feishu/onboarding.py`：

```python
"""对话式引导状态机：目标公司 → 岗位 → 简历收集。纯逻辑，不发消息。"""
from __future__ import annotations
from dataclasses import dataclass

ASK_COMPANY = "请回复目标公司名称（回复「跳过」不指定）"
ASK_POSITION = "请回复岗位名称，如「后端开发」（回复「跳过」不指定）"
ASK_RESUME = (
    "请粘贴简历内容：可分多条消息发送，最后单独发送一条「END」结束；"
    "回复「跳过」进入无简历模式。"
)


@dataclass
class OnboardingResult:
    company: str = ""
    position: str = ""
    resume_text: str = ""


class OnboardingFlow:
    """每用户一个实例；feed() 返回（待发送回复列表, 完成结果或 None）。"""

    COMPANY, POSITION, RESUME = "company", "position", "resume"

    def __init__(self):
        self.state = self.COMPANY
        self._result = OnboardingResult()
        self._resume_parts: list[str] = []

    @property
    def opening_question(self) -> str:
        return ASK_COMPANY

    def feed(self, text: str) -> tuple[list[str], OnboardingResult | None]:
        msg = text.strip()
        if self.state == self.COMPANY:
            if msg and msg != "跳过":
                self._result.company = msg
            self.state = self.POSITION
            return [ASK_POSITION], None
        if self.state == self.POSITION:
            if msg and msg != "跳过":
                self._result.position = msg
            self.state = self.RESUME
            return [ASK_RESUME], None
        # 简历收集：整条「跳过」→ 无简历；任一行为 END → 截到该行结束
        if msg == "跳过":
            return ["已跳过简历，正在开始面试…"], self._result
        lines = [l.strip() for l in text.split("\n")]
        if "END" in lines:
            idx = lines.index("END")
            self._resume_parts.append("\n".join(lines[:idx]).strip("\n"))
            self._result.resume_text = "\n".join(p for p in self._resume_parts if p)
            return ["简历已收到，正在开始面试…"], self._result
        self._resume_parts.append(text.strip("\n"))
        return [], None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_feishu_onboarding.py -q && pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add interview_agent/feishu/onboarding.py tests/test_feishu_onboarding.py
git commit -m "feat: 飞书对话式引导状态机"
```

---

### Task 4: 测试替身与 FeishuBot 消息分发

**Files:**
- Create: `tests/feishu_mocks.py`
- Create: `interview_agent/feishu/bot.py`
- Test: `tests/test_feishu_bot.py`

**Interfaces:**
- Consumes: `LarkFeishuClient`/`FeishuAPIError`（Task 2）、`OnboardingFlow`/`OnboardingResult`/`ASK_COMPANY`（Task 3）、`SessionManager` 协议（`start_session/submit_answer/end_session/get_task`，现有）
- Produces:
  - `class FeishuBot`：`__init__(self, manager, client)`；`handle_event(self, event) -> None`（lark 事件回调入口，内部做规范化+去重+p2p/文本过滤）；`handle_message(self, open_id: str, text: str) -> None`（四语境分发）；`_pump(self, open_id: str, session_id: str, task) -> None`（Task 5 实现，本任务为 `return` 桩）；常量 `THINKING_TEXT`、`IDLE_HINT`、`HELP_TEXT`
  - `tests/feishu_mocks.py`：`MockFeishuClient`（`.sent: list[tuple[str,str]]`、`.patched: list[tuple[str,str]]`、`.cards: list[tuple[str,str,str]]`、`.patch_ok: bool`、`.texts_to(open_id)`）、`FakeManager`（`.started`、`.answers`、`.ends`、`.tasks`、`.submit_error`）、`FakeTask`、`ScriptLLM`、`wait_until`（Task 5/7 复用）

- [ ] **Step 1: 创建共享测试替身**

创建 `tests/feishu_mocks.py`：

```python
"""飞书渠道测试共享替身：内存客户端 / 假 Manager / 假任务 / 脚本 LLM。"""
import time
from types import SimpleNamespace
from interview_agent.llm import AssistantTurn, StreamEnd
from interview_agent.web.events import EventQueue


class MockFeishuClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []        # (open_id, text)
        self.patched: list[tuple[str, str]] = []     # (message_id, text)
        self.cards: list[tuple[str, str, str]] = []  # (open_id, title, markdown)
        self.patch_ok = True
        self._n = 0

    def send_text(self, open_id, text):
        self._n += 1
        self.sent.append((open_id, text))
        return f"om_mock_{self._n}"

    def patch_text(self, message_id, text):
        if not self.patch_ok:
            return False
        self.patched.append((message_id, text))
        return True

    def send_markdown_card(self, open_id, title, markdown_text):
        self.cards.append((open_id, title, markdown_text))

    def texts_to(self, open_id):
        return [t for o, t in self.sent if o == open_id]


class FakeManager:
    """记录调用；get_task 返回注入的假任务。"""

    def __init__(self):
        self.started: list[dict] = []
        self.answers: list[tuple[str, str, str]] = []
        self.ends: list[tuple[str, str]] = []
        self.tasks: dict[tuple[str, str], object] = {}
        self.submit_error: Exception | None = None

    def start_session(self, user_id, *, company="", position="",
                      resume_text=None, jd_text="", experiences=None):
        self.started.append({"user_id": user_id, "company": company,
                             "position": position, "resume_text": resume_text})
        return f"sess{len(self.started)}"

    def get_task(self, user_id, session_id):
        return self.tasks.get((user_id, session_id))

    def submit_answer(self, user_id, session_id, text):
        if self.submit_error is not None:
            raise self.submit_error
        self.answers.append((user_id, session_id, text))

    def end_session(self, user_id, session_id):
        self.ends.append((user_id, session_id))


class FakeTask:
    def __init__(self, session_dir):
        self.queue = EventQueue()
        self.session = SimpleNamespace(session_dir=session_dir)  # 与真实 InterviewTask 同形
        self.ended = False


class ScriptLLM:
    """按脚本出牌的 LLM：chat 与 chat_stream 共用同一队列。"""

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_feishu_bot.py`：

```python
import json
from types import SimpleNamespace
import pytest
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
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/test_feishu_bot.py -q`
Expected: FAIL（ModuleNotFoundError: interview_agent.feishu.bot）

- [ ] **Step 4: 最小实现**

创建 `interview_agent/feishu/bot.py`：

```python
"""飞书机器人：事件规范化、四语境分发、单活跃会话约束、事件泵。"""
from __future__ import annotations
import json
import threading
from collections import deque
from .feishu_client import FeishuAPIError
from .onboarding import OnboardingFlow, OnboardingResult

THINKING_TEXT = "面试官思考中…"
EVALUATING_TEXT = "面试结束，正在生成评估报告…"
IDLE_HINT = "你好，我是模拟面试官。发送「开始面试」开始一场模拟面试，发送「帮助」查看完整用法。"
HELP_TEXT = (
    "【模拟面试官使用说明】\n"
    "1. 发送「开始面试」：按引导填写目标公司、岗位、简历（均可跳过）；\n"
    "2. 面试中直接发送消息即为回答；\n"
    "3. 发送「结束」随时结束并生成评估报告；\n"
    "4. 引导过程中发送「取消」可放弃配置。\n"
    "当前仅支持文字消息，语音面试将在后续版本支持。"
)
EMPTY_TURN_TEXT = "（本回合未产生内容，请再回答一次，或发送「结束」）"

START_CMDS = {"开始面试"}
END_CMDS = {"结束", "exit", "/end"}
HELP_CMDS = {"帮助", "help"}
CANCEL_CMDS = {"取消"}


class FeishuBot:
    def __init__(self, manager, client):
        self._manager = manager
        self._client = client
        self._flows: dict[str, OnboardingFlow] = {}
        self._active: dict[str, str] = {}  # open_id -> session_id
        self._pump_threads: dict[str, threading.Thread] = {}
        self._seen: deque[str] = deque(maxlen=1000)
        self._seen_set: set[str] = set()
        self._dispatch_lock = threading.Lock()

    # ---------- 入站：事件规范化（语音预留边界：未来在此加 audio→ASR） ----------

    def handle_event(self, event) -> None:
        """lark-oapi P2ImMessageReceiveV1 回调入口。"""
        try:
            event_id = event.header.event_id
            chat_type = event.event.message.chat_type
            msg_type = event.event.message.message_type
            open_id = event.event.sender.sender_id.open_id
            text = json.loads(event.event.message.content or "{}").get("text", "")
        except (AttributeError, TypeError, ValueError):
            return
        with self._dispatch_lock:
            if event_id in self._seen_set:
                return
            self._seen_set.add(event_id)
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(event_id)
        if chat_type != "p2p":
            return
        if msg_type != "text":
            self._send(open_id, "暂仅支持文字消息（语音面试将在后续版本支持）")
            return
        self.handle_message(open_id, text)

    # ---------- 分发 ----------

    def handle_message(self, open_id: str, text: str) -> None:
        cmd = text.strip()
        if cmd in HELP_CMDS:
            self._send(open_id, HELP_TEXT)
            return
        if cmd in END_CMDS:
            self._handle_end(open_id)
            return
        if cmd in START_CMDS:
            self._handle_start(open_id)
            return
        if cmd in CANCEL_CMDS and open_id in self._flows:
            self._flows.pop(open_id, None)
            self._send(open_id, "已取消本场配置。")
            return
        flow = self._flows.get(open_id)
        if flow is not None:
            replies, result = flow.feed(text)
            for r in replies:
                self._send(open_id, r)
            if result is not None:
                self._flows.pop(open_id, None)
                self._start_interview(open_id, result)
            return
        sid = self._active.get(open_id)
        if sid is not None:
            try:
                self._manager.submit_answer(open_id, sid, text)
            except (RuntimeError, KeyError) as e:
                self._send(open_id, str(e))
                if isinstance(e, KeyError):
                    self._active.pop(open_id, None)
            return
        self._send(open_id, IDLE_HINT)

    def _handle_start(self, open_id: str) -> None:
        if open_id in self._active:
            self._send(open_id, "已有进行中的面试，请先发送「结束」。")
            return
        flow = OnboardingFlow()
        self._flows[open_id] = flow
        self._send(open_id, flow.opening_question)

    def _handle_end(self, open_id: str) -> None:
        sid = self._active.get(open_id)
        if sid is not None:
            try:
                self._manager.end_session(open_id, sid)
            except (RuntimeError, KeyError) as e:
                self._send(open_id, str(e))
                if isinstance(e, KeyError):
                    self._active.pop(open_id, None)
            return
        if open_id in self._flows:
            self._flows.pop(open_id, None)
            self._send(open_id, "已取消本场配置。")
            return
        self._send(open_id, "当前没有进行中的面试。")

    def _start_interview(self, open_id: str, result: OnboardingResult) -> None:
        sid = self._manager.start_session(
            open_id,
            company=result.company,
            position=result.position,
            resume_text=result.resume_text or None,
        )
        self._active[open_id] = sid
        self._send(open_id, "面试已开始，直接回复消息作答；发送「结束」生成评估报告。")
        # 同步取出任务对象再传给泵：避免泵线程查询 manager 的竞态
        task = self._manager.get_task(open_id, sid)
        t = threading.Thread(
            target=self._pump, args=(open_id, sid, task), daemon=True,
            name=f"feishu-pump-{sid}",
        )
        self._pump_threads[sid] = t
        t.start()

    def _send(self, open_id: str, text: str) -> str | None:
        """发送失败（重试后）记日志跳过，不中断后续流程。"""
        try:
            return self._client.send_text(open_id, text)
        except FeishuAPIError as e:
            print(f"[feishu] 发送消息失败（已跳过）: {e}")
            return None

    # ---------- 出站：事件泵（语音预留边界：未来在此加文本→TTS 渲染） ----------

    def _pump(self, open_id: str, session_id: str, task) -> None:
        return  # Task 5 实现
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_feishu_bot.py -q && pytest -q`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add tests/feishu_mocks.py tests/test_feishu_bot.py interview_agent/feishu/bot.py
git commit -m "feat: FeishuBot 消息分发（四语境路由/命令/去重/p2p+文本过滤）"
```

---

### Task 5: 事件泵与报告交付

**Files:**
- Modify: `interview_agent/feishu/bot.py`（实现 `_pump`、`_send_report`、`_cleanup`）
- Test: `tests/test_feishu_bot.py`（追加测试）

**Interfaces:**
- Consumes: `InterviewTask`（`task.queue: EventQueue`、`task.ended: bool`、`task.session.session_dir: Path`，现有）、`EventQueue.wait_for_events_after(last_seq, timeout) -> list[tuple[int, dict]]`（现有）
- Produces: `_pump` 完整实现；事件→消息映射遵循 spec §5.2（thinking/首个 delta 发占位；delta 攒缓冲；turn_end PATCH 失败降级发送；evaluating/done/error 处理；`task.ended` 且队列排空时发空闲/异常结束提示并清理）

- [ ] **Step 1: 写失败测试**

在 `tests/test_feishu_bot.py` 追加（`Path`、`FakeTask`、`EVALUATING_TEXT`、`EMPTY_TURN_TEXT` 并入文件顶部 import 区）。测试直接同步调用 `bot._pump(open_id, sid, task)`（task 作为参数传入，无竞态）：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_feishu_bot.py -q`
Expected: 新增测试 FAIL（桩实现直接 return，占位/卡片/清理断言全不满足）；Task 4 既有测试仍 PASS

- [ ] **Step 3: 实现 `_pump`**

替换 `bot.py` 中 `_pump` 桩为：

```python
    POLL_INTERVAL = 1.0

    def _pump(self, open_id: str, session_id: str, task) -> None:
        if task is None:
            print(f"[feishu] 会话 {session_id} 无活跃任务，事件泵退出")
            return
        seq = 0
        placeholder_id: str | None = None
        buf: list[str] = []
        while True:
            items = task.queue.wait_for_events_after(seq, timeout=self.POLL_INTERVAL)
            for s, ev in items:
                seq = s
                kind = ev.get("type")
                if kind == "status" and ev.get("status") == "thinking":
                    if placeholder_id is None:
                        placeholder_id = self._send(open_id, THINKING_TEXT)
                elif kind == "delta":
                    if placeholder_id is None:
                        placeholder_id = self._send(open_id, THINKING_TEXT)
                    buf.append(ev.get("text", ""))
                elif kind == "turn_end":
                    content = "".join(buf) or EMPTY_TURN_TEXT
                    buf.clear()
                    if placeholder_id is not None:
                        if not self._client.patch_text(placeholder_id, content):
                            self._send(open_id, content)
                        placeholder_id = None
                    else:
                        self._send(open_id, content)
                elif kind == "status" and ev.get("status") == "evaluating":
                    self._send(open_id, EVALUATING_TEXT)
                elif kind == "status" and ev.get("status") == "done":
                    self._send_report(open_id, task)
                    self._cleanup(open_id, session_id)
                    return
                elif kind == "error":
                    self._send(open_id, f"面试出错了：{ev.get('message', '')}")
                    self._cleanup(open_id, session_id)
                    return
            if not items and task.ended:
                self._send(open_id, "本场面试已结束（空闲超时或异常）。发送「开始面试」可再来一场。")
                self._cleanup(open_id, session_id)
                return

    def _send_report(self, open_id: str, task) -> None:
        p = task.session.session_dir / "report.md"
        if p.is_file():
            self._client.send_markdown_card(
                open_id, "面试评估报告", p.read_text(encoding="utf-8")
            )
        else:
            self._send(open_id, "评估已完成但报告文件缺失，请联系管理员在服务器会话目录查看。")

    def _cleanup(self, open_id: str, session_id: str) -> None:
        if self._active.get(open_id) == session_id:
            del self._active[open_id]
        self._pump_threads.pop(session_id, None)
```

注意 `POLL_INTERVAL` 定义为类属性（放在 `THINKING_TEXT` 等常量之后、`__init__` 之前）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_feishu_bot.py -q && pytest -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add interview_agent/feishu/bot.py tests/test_feishu_bot.py
git commit -m "feat: 飞书事件泵（占位+PATCH 回合输出/报告卡片/空闲结束提示）"
```

---

### Task 6: 入口装配与 README

**Files:**
- Create: `interview_agent/feishu/__main__.py`
- Modify: `README.md`（追加"飞书渠道"章节）

**Interfaces:**
- Consumes: Task 1-5 全部产物。
- Produces: `python -m interview_agent.feishu` 可执行入口。

- [ ] **Step 1: 创建入口**

`interview_agent/feishu/__main__.py`：

```python
"""飞书渠道入口：python -m interview_agent.feishu（长连接模式，无需公网 IP）。"""
from __future__ import annotations
from ..config import load_config, require_api_key
from ..llm import create_llm
from ..web.manager import SessionManager
from .bot import FeishuBot
from .feishu_client import LarkFeishuClient, require_feishu_credentials


def main() -> None:
    cfg = load_config()
    app_id, app_secret = require_feishu_credentials(cfg)
    api_key = require_api_key()
    llm = create_llm(api_key, cfg.get("model", "deepseek-chat"))
    manager = SessionManager(cfg, llm)
    bot = FeishuBot(manager, LarkFeishuClient(app_id, app_secret))

    import lark_oapi as lark
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(bot.handle_event)
        .build()
    )
    ws = lark.ws.Client(
        app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO
    )
    print("飞书机器人已启动（长连接模式），等待私聊消息… Ctrl+C 退出")
    ws.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: README 追加章节**

在 `README.md` 的"Web 界面"章节之后追加：

```markdown
## 飞书渠道

```bash
python -m pip install -e ".[dev]"
# .env 中配置 DEEPSEEK_API_KEY、FEISHU_APP_ID、FEISHU_APP_SECRET 后：
python -m interview_agent.feishu
```

开放平台配置步骤（一次性）：

1. [飞书开放平台](https://open.feishu.cn/) → 创建**企业自建应用** → 记下 `App ID` / `App Secret` 填入 `.env`。
2. 应用能力 → 添加**机器人**。
3. 权限管理 → 开通：`im:message`（读取用户发给机器人的单聊消息）、`im:message:send_as_bot`（以机器人身份发消息）。
4. 事件与回调 → 事件配置 → 订阅方式选择**使用长连接接收事件** → 添加事件 `im.message.receive_v1`（接收消息）。
5. 版本管理与发布 → 创建版本并发布，可用范围设为全员（或按需）。

使用：在飞书中搜索机器人名称，私聊发送 `开始面试`，按引导回答即可；`帮助` 查看全部命令。面试记录保存在 `interviews/<飞书用户 open_id>/`，与 Web 渠道互不影响。
```

- [ ] **Step 3: 冒烟验证**

Run: `python -c "from interview_agent.feishu.__main__ import main; print('ok')" && pytest -q`
Expected: 输出 ok，全部测试 PASS

再验证缺凭证报错（不改真实 `.env`）：

```bash
python - <<'EOF'
from interview_agent.feishu.feishu_client import require_feishu_credentials
try:
    require_feishu_credentials({})
except KeyError as e:
    print("凭证缺失提示:", e)
EOF
```

Expected: 打印含 `FEISHU_APP_ID` 的中文提示。

- [ ] **Step 4: Commit**

```bash
git add interview_agent/feishu/__main__.py README.md
git commit -m "feat: 飞书渠道入口与 README 配置指南"
```

---

### Task 7: 端到端集成测试

**Files:**
- Test: `tests/test_feishu_integration.py`

**Interfaces:**
- Consumes: 真实 `SessionManager`/`InterviewTask`（现有）、`ScriptLLM`/`MockFeishuClient`/`wait_until`（Task 4 的 `tests/feishu_mocks.py`）、`FeishuBot` 全部行为。
- Produces: 无（纯验证任务，证明 spec §6 数据流与多用户隔离）。

- [ ] **Step 1: 写集成测试**

创建 `tests/test_feishu_integration.py`：

```python
import json
from pathlib import Path
from interview_agent.llm import AssistantTurn
from interview_agent.web.manager import SessionManager
from interview_agent.feishu.bot import EVALUATING_TEXT, THINKING_TEXT, FeishuBot
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
    for u in (u1, u2):
        assert wait_until(lambda u=u: any("面试已开始" in t for t in c.texts_to(u)))
        assert wait_until(
            lambda u=u: any(text in ("开场1", "开场2")
                            for _, text in c.patched)
        )
    bot.handle_message(u1, "回答甲")
    bot.handle_message(u2, "回答乙")
    for u in (u1, u2):
        assert wait_until(
            lambda u=u: any(text.startswith("问题") for _, text in c.patched)
        )
        bot.handle_message(u, "结束")
        assert wait_until(lambda u=u: any(t == EVALUATING_TEXT for t in c.texts_to(u)))
        assert wait_until(lambda u=u: len([x for x in c.cards if x[0] == u]) == 1)

    # 存储隔离：各自的 transcript 只含自己的回答
    t1 = list(Path(tmp_path, u1).glob("*/transcript.jsonl"))
    t2 = list(Path(tmp_path, u2).glob("*/transcript.jsonl"))
    assert len(t1) == 1 and len(t2) == 1
    lines1 = [json.loads(l) for l in t1[0].read_text(encoding="utf-8").splitlines()]
    assert any(e.get("content") == "回答甲" for e in lines1 if e.get("role") == "candidate")
    assert not any(e.get("content") == "回答乙" for e in lines1)
    # 报告落盘
    assert list(Path(tmp_path, u1).glob("*/report.md"))
    # 结束后回到空闲
    bot.handle_message(u1, "你好")
    assert c.sent[-1][0] == u1
```

注意：transcript 的 candidate 行结构为 `{"role": "candidate", "content": ..., "timestamp": ...}`（见 `session.py` `_entry`）；若断言失败先打印实际行核对字段名。

- [ ] **Step 2: 运行确认行为**

Run: `pytest tests/test_feishu_integration.py -v`
Expected: PASS（这是验收性测试，实现已由 Task 1-6 完成；若失败按 systematic-debugging 排查，通常是竞态等待条件写得太紧）

- [ ] **Step 3: 全量回归**

Run: `pytest -q`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_feishu_integration.py
git commit -m "test: 飞书渠道端到端集成（双用户并发隔离/报告卡片/存储隔离）"
```

---

## 验收清单（对 spec 的覆盖）

- spec §5.1 四模块 + 入口：Task 2/3/4/6 ✓
- spec §5.2 事件→消息映射（含开场无 thinking、PATCH 降级、ended 自查）：Task 5 ✓
- spec §5.3 最小改动集（config/.env.example/pyproject/README）：Task 1/6 ✓
- spec §6 数据流（引导→建会话→问答→结束→报告）：Task 4/7 ✓
- spec §8 命令表全部语境：Task 4 测试矩阵 ✓
- spec §9 单用户单场/多用户隔离/空闲回收：Task 4/5/7 ✓
- spec §11 错误处理（去重/频控重试/PATCH 降级/报告缺失/busy）：Task 2/4/5 ✓
- spec §12 配置与启动：Task 1/6 ✓
