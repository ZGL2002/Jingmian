# 面试 Agent Web 界面设计文档

日期：2026-08-07
状态：待用户评审
上游：2026-08-06-interview-agent-design.md（CLI 版设计）

## 1. 背景与目标

CLI 版面试 Agent 已具备多轮追问、自然结束、评估报告等基础能力。本阶段为其拓展 **Web 界面**：在浏览器中完成从"面试配置 → 流式问答 → 评估报告 → 历史回看"的完整闭环，并把渠道抽象做扎实，为后续飞书接入与语音/视频面试预留同一套事件协议。

核心成功标准：

1. 浏览器完成一整场面试（配置公司/简历/JD/面经 → 问答 → 结束 → 报告），流式打字体验。
2. 一人可同时开多场不同公司/岗位的面试，互不干扰；同一场问答不串线。
3. 历史列表与报告标注目标公司；面经参考支持直接粘贴与库内勾选。
4. 局域网直连 + 简单访问口令；API key 只存在于服务器。
5. Agent 核心零改动，新增代码收敛在 `interview_agent/web/` 包与少量钩子内。

## 2. 范围

### 本版包含

- FastAPI + SSE + 原生 JS 单页（无前端构建工具链）。
- 面试配置区：目标公司（可选）、简历（粘贴/上传 `.txt`/`.md`/`.pdf`，支持无简历模式）、JD（可选，作为出题输入）、面经参考（库内勾选或直接粘贴，粘贴自动入库并用于本场）。
- 流式打字效果：SSE 事件协议（状态 / 增量文字 / 回合结束 / 错误 / 快照），未来语音/视频复用同一协议。
- 本地面经库：按用户隔离，条目带可选"来源/公司"标签；支持新增/列表/查看/删除。
- 简单历史列表：时间 / 目标公司 / 题数 / 是否有报告；可查看对话回放与报告（报告服务端渲染 HTML）。
- 一人可同时多场面试（`user_id → {session_id: 活跃面试}` 注册表），未来多人多场仅需接认证。
- 访问口令：`INTERVIEW_WEB_TOKEN`（环境变量），登录后 HttpOnly cookie，常量时间比较。
- API key：第一版服务器统一密钥（沿用 `.env`），LLM 创建收敛到工厂函数，为未来"用户自带密钥"预留单一改动点。

### 本版明确不做（未来扩展）

- 飞书 / 微信渠道接入。
- 语音 / 视频面试（ASR / TTS / WebRTC）。
- MCP 工具抓取网上面经、按目标公司精准拉取（数据模型已预留来源/公司字段）。
- 多用户认证、用户自带密钥（BYOK）加密存储。
- 完整历史管理（搜索 / 删除 / 重命名 / 导出）。
- 简历 OCR、题库系统、跨场长期记忆。

## 3. 需求汇总

| 维度 | 决定 |
| --- | --- |
| 渠道 | Web 优先；飞书后续；微信不做 |
| 用户 | 单人起步（固定 user_id），接口预留多用户 |
| 并发 | 一人可同时多场；未来多人同时多场 |
| 回复方式 | SSE 流式打字；事件协议为语音/视频预留 |
| 出题输入 | 简历（必填或空）+ JD（可选）+ 目标公司（可选）+ 面经参考（可选） |
| 面经 | v1 用户粘贴入库；未来 MCP 抓取 + 目标公司精准拉取 |
| 历史 | 简单列表，标注目标公司；报告可回看 |
| 访问 | 局域网直连（0.0.0.0）+ 简单访问口令 |
| 密钥 | v1 服务器统一密钥 + `create_llm` 工厂钩子；未来混合模式（平台 key + 用户 BYOK） |
| 技术栈 | FastAPI + uvicorn + SSE + 原生 JS 单页 |

## 4. 架构总览

核心原则不变：**Agent 大脑与界面无关，Web 只是新的表现层**。新增代码全部在 `interview_agent/web/` 包内，Agent 核心保持零改动（仅加少量可选钩子）。

```
浏览器单页（原生 JS：配置区 / 聊天区 / 历史 / 面经库）
   ▲ fetch 提交          ▼ EventSource(SSE) 事件流
FastAPI 应用层（interview_agent/web/）
   ├─ auth.py     访问口令中间件（token → HttpOnly cookie）
   ├─ manager.py  SessionManager（user_id → {session_id: 活跃面试}）
   ├─ app.py      路由（登录/配置/回答/结束/SSE/历史/报告/面经库）
   ├─ events.py   SSE 事件协议 + 可重放队列
   └─ runner.py   后台线程：开场 → 回合 → 结束评估，发事件
        │
        ▼ 复用现有接口（核心零改动）
Agent 核心：ToolAgent / InterviewSession / 工具注册表 / 简历解析 / 评估
        │
        ▼
基础设施：LLM 客户端工厂（DeepSeek） / interviews/ 文件存储 / .env 配置
```

未来飞书：新增"飞书适配器"，把同一事件协议映射为飞书消息推送/回调，其余不动。未来语音/视频：事件协议 + 浏览器媒体层，TTS/ASR 只是渲染器。

## 5. 核心组件与职责

### 5.1 新增模块（`interview_agent/web/`）

| 模块 | 职责 |
| --- | --- |
| `app.py` | FastAPI 应用与路由：页面、登录、面试配置、回答、结束、SSE、历史、报告、面经库管理 |
| `auth.py` | 口令中间件：校验 `INTERVIEW_WEB_TOKEN`，登录设 HttpOnly cookie；未登录访问除登录页/静态资源外一律重定向或 401 |
| `manager.py` | `SessionManager`：`user_id → {session_id: ActiveInterview}` 两层注册表；管理会话生命周期、busy 锁、空闲超时回收 |
| `runner.py` | 每个活跃面试一个后台线程：开场 → 等待回答 → `run_turn`（流式回调发 delta）→ 结束 → 评估 → 报告事件；异常捕获转 `error` 事件 |
| `events.py` | 事件协议 dataclass 与线程安全队列；队列保留未消费事件，新 SSE 订阅先重放再实时 |

`ActiveInterview` 持有一场面试的全部运行时状态：`InterviewSession`、`ToolAgent`、工具注册表与 `ToolContext`、事件队列、`busy` 标志、最后活动时间。

### 5.2 核心代码改动（最小集）

1. **`llm.py`**：`LLMClient` 增加 `chat_stream(messages, tools, max_tokens)` 流式接口；`DeepSeekClient` 用 OpenAI 兼容 `stream=True` 实现。
2. **`agent.py`**：`run_turn` 增加可选 `on_delta` 回调；工具调用轮次不展示内容（实践上为空），最终回答逐 token 推送；保留 `_strip_role_leak` 串台防御（流式模式下浏览器以最终事件为准修正）。
3. **`prompts.py`**：`build_system_prompt` 增加可选段：目标公司上下文、JD 要求、面经参考（高频题提示）；"简历深挖优先 / 至少一道场景题 / 单问硬规则"等既有约束不变。
4. **`models.py`**：新增 `ExperienceEntry`（标题 / 内容 / 来源公司标签 / 创建时间）；`SessionConfig` 增加 `company`、`jd_text`、`experience_refs` 等字段。
5. **`llm` 创建收敛**：新增 `create_llm(provider, model, api_key)` 工厂函数（默认 DeepSeek），`runner.py` 创建会话时统一走工厂；未来多用户 BYOK 只改 key 来源。

## 6. 数据流（一次完整 Web 面试）

1. **登录**：访问任意页面 → 未带 cookie 则跳 `/login` → 输入 `INTERVIEW_WEB_TOKEN` → 校验通过设 HttpOnly cookie。
2. **新建面试**：页面填配置（目标公司 / 简历 / JD / 面经）→ `POST /api/session/start` → 后端解析简历（复用现有管线）、生成会话目录、写入 `resume.md` / `jd.md` / `references/`（面经快照）与 `prompt.md` → 返回 `session_id`。
3. **流式问答循环**：
   - 页面 `POST /api/answer?session_id=...` 提交回答（Enter 发送，Shift+Enter 换行；复用长回答落盘）。
   - `runner` 后台线程执行 `run_turn(on_delta)`；前端先收到 `status: thinking`，再收到逐条 `delta`，最后 `turn_end`（含题数）。
   - 多场并行：每场独立线程、独立事件队列、独立 SSE 连接，互不阻塞。
4. **结束**：`POST /api/end?session_id=...`（或 wrap 请求）→ `status: evaluating` → `run_evaluation`（复用现有评估管线）→ 报告落盘 → `status: done` + 报告地址。
5. **历史回看**：`GET /api/history` 读取各会话 meta（时间 / 公司 / 题数 / 有报告）；点击进入回放（transcript）与报告（服务端渲染 HTML）。

## 7. 存储结构扩展

沿用 `interviews/<user_id>/` 命名空间，会话目录内新增：

```
interviews/<user_id>/
  ├─ experiences/                      # 面经库（本版新增，按用户隔离）
  │   ├─ <experience_id>.md            # 标题行 + 来源/公司标签 + 内容（owner 元数据）
  │   └─ ...
  └─ <session_id>/                     # 每场面试
      ├─ meta 信息（扩展 transcript 首行 role=meta：company / 是否有简历 / JD / 面经数）
      ├─ jd.md                         # JD 副本（可选，本版新增）
      ├─ references/                   # 本场勾选/粘贴的面经快照（本版新增）
      ├─ resume.md / prompt.md / transcript.jsonl / answers/ / report.md  # 沿用
```

隔离规则沿用：owner 元数据 + 路径强校验；跨用户访问拒绝。面经库同样走原子写。

## 8. API 与事件协议

### 8.1 REST 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/login` | 口令页 |
| POST | `/api/login` | 校验口令，设 cookie |
| POST | `/api/logout` | 清除 cookie |
| GET | `/` | 主页面（单页应用） |
| GET | `/api/session?session_id=` | 会话状态快照（state / 题数 / 最近消息 / busy） |
| POST | `/api/session/start` | 建会话（company / resume 文本或文件 / jd / experience_ids / experience_text） |
| POST | `/api/answer?session_id=` | 提交回答，触发回合 |
| POST | `/api/end?session_id=` | 主动结束并评估 |
| GET | `/api/stream?session_id=` | SSE 事件流（重连先重放） |
| GET | `/api/history` | 历史列表（含目标公司） |
| GET | `/api/sessions/{id}/report` | 报告（HTML） |
| GET | `/api/sessions/{id}/transcript` | 对话回放 |
| GET | `/api/experiences` | 面经库列表 |
| POST | `/api/experiences` | 新增面经（标题 / 内容 / 来源公司标签） |
| DELETE | `/api/experiences/{id}` | 删除面经 |

### 8.2 SSE 事件

```json
{"type":"status","status":"thinking"}
{"type":"delta","text":"…"}
{"type":"turn_end","question_count":21}
{"type":"status","status":"evaluating"}
{"type":"status","status":"done","report_url":"/api/sessions/xxx/report"}
{"type":"error","message":"…"}
{"type":"snapshot","state":"questioning","question_count":20,"recent":[…]}  // 重连时
```

## 9. 并发与恢复

- **一人多场**：`SessionManager` 按 `(user_id, session_id)` 管理；每场独立线程、独立队列、独立 SSE；同场内部回合串行（busy 锁），不同场并行。
- **未来多人多场**：认证层产出 `user_id`，注册表结构不变。
- **刷新 / 断线**：SSE 重连先重放未消费事件，再补 `snapshot`；进行中的回合不受影响。
- **关闭页面 / 重启服务**：会话全程落盘；重启后从历史列表可见，进程内活跃会话按空闲超时（默认 30 分钟，可配置）回收。
- **文件一致性**：transcript 单一写入者（同场回合串行保证）；面经库/报告原子写。

## 10. 安全设计

- **访问口令**：`INTERVIEW_WEB_TOKEN` 必填（缺失启动报错）；`hmac.compare_digest` 比较；cookie 设 HttpOnly；未登录一律拦截。
- **API key**：只存在于服务器 `.env` / 环境变量，页面不提供 key 输入，任何记录/日志/报告不含 key；`create_llm` 工厂为未来 BYOK 预留单一改动点（届时密钥加密存储、按用户隔离、永不回显）。
- **存储隔离**：沿用 owner 元数据 + 路径强校验；面经库与面试记录同属用户命名空间，跨用户访问拒绝。
- **工具面**：沿用现有白名单（仅文件工具，无 shell / 任意命令执行）。

## 11. 错误处理

| 场景 | 处理 |
| --- | --- |
| LLM 网络错误 / 超时 | 沿用指数退避重试；仍失败发 `error` 事件，候选回答已落盘，可重发 |
| API key 无效（401） | `error` 事件明确提示，不打印 key |
| 口令缺失 / 错误 | 未登录跳转登录页；错误口令提示重试 |
| 简历 / PDF 解析失败 | 表单校验 4xx，前端直接展示 |
| 回合中异常 | runner 捕获 → `error` 事件 → 会话回到可继续状态，不崩溃 |
| 同场回合进行中再提交 | 返回"面试官思考中，请稍候" |
| 长回答 | 复用现有阈值落盘与摘要逻辑 |

## 12. 配置（`.env.example` 同步更新）

```bash
DEEPSEEK_API_KEY=sk-你的密钥        # 沿用：服务器统一密钥
INTERVIEW_WEB_HOST=0.0.0.0          # 默认
INTERVIEW_WEB_PORT=8765             # 默认
INTERVIEW_WEB_TOKEN=一串随机字符     # 必填：访问口令
INTERVIEW_MODEL=deepseek-chat       # 沿用，可选
INTERVIEW_MIN_QUESTIONS=20          # 沿用，可选
INTERVIEW_SESSION_IDLE_TIMEOUT=1800 # 新增，可选：会话空闲回收秒数
```

## 13. 测试

- **单元**：auth（未登录拦截 / 口令错误 / cookie 生效）；事件协议（序列化 + 重放）；manager（多会话注册、同场 busy 锁、空闲超时回收）；面经库 CRUD 与 owner 隔离；提示词组装（公司 / JD / 面经组合矩阵）；历史列表读取公司字段与排序；`create_llm` 工厂。
- **集成**（FastAPI TestClient + mock LLM）：登录 → 起面试（公司 / JD / 面经）→ 回答 → SSE 事件流 → 结束 → 报告；**两场不同公司面试并发跑，验证事件不互串**；SSE 重连重放；LLM 失败发 error 且可重发；简历解析失败 4xx。
- **手动验收**：局域网浏览器 + 真实 DeepSeek 并行开两场不同公司/岗位面试；完整一场（公司 + 简历 + JD + 面经）验证报告标注；口令登录与端口转发各验证一次。

## 14. 未来扩展点（仅预留，不实现）

1. **飞书适配器**：同一事件协议 → 飞书消息推送/回调。
2. **语音 / 视频**：TTS/ASR 渲染器 + WebRTC 媒体层，Agent 核心不变。
3. **MCP 抓取面经**：复用面经条目的来源/公司标签与"目标公司"输入，按公司精准拉取入库。
4. **多人多场 + 混合密钥**：认证接 `user_id`；`create_llm` 工厂 + 用户密钥加密存储（BYOK 覆盖平台 key）。
5. **历史管理增强**：搜索 / 删除 / 重命名 / 导出。
6. **报告增强**：对比多场同公司面试的进步曲线（依赖跨场记忆，另行设计）。

## 15. 依赖变更

新增：`fastapi`、`uvicorn`、`python-multipart`（文件上传）、`markdown`（报告渲染）。
