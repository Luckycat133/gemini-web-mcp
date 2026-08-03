# 技术架构

深入了解 Gemini MCP Server v1.3.0 的设计与实现。

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│              MCP Host (Claude Desktop)                   │
│                 (JSON-RPC 2.0)                           │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│         Gemini MCP Server (MCPServer / SDK v2)           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Tools      │  │   Session    │  │    Client    │  │
│  │  对话/媒体  │  │   Manager    │  │   Wrapper    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│      Gemini Web API (gemini-webapi)                     │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Cookie 管理   │  Auto Refresh  │ TLS Fingerprint│   │
│  └──────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS/HTTP2
                         ▼
              ┌──────────────────────┐
              │  gemini.google.com   │
              └──────────────────────┘
```

---

## 📦 项目结构

```
gemini-mcp-server/
├── pyproject.toml          # 项目配置
├── README.md               # 项目文档（英文公开首页）
├── README.zh-CN.md         # 中文 README
├── AGENTS.md               # 仓库协作规范
├── .env.example            # 环境变量示例
├── .gitignore             # Git 忽略规则
├── src/
│   ├── __init__.py        # 包初始化（安装包元数据版本）
│   ├── resources.py       # importlib.resources 包内数据入口
│   ├── data/              # wheel 内不可变资源（默认 prompts）
│   ├── server.py          # MCP 服务器主入口（primary surface）
│   ├── skill_server.py    # 低 token skill 服务器（facade surface）
│   ├── client_wrapper.py  # Gemini 客户端封装
│   ├── client_manager.py  # 客户端生命周期管理
│   ├── cookie_manager.py  # Cookie 加载/验证/刷新
│   ├── session_manager.py # 本地会话管理
│   ├── domain/            # 领域结果、artifact、错误、告警与操作状态
│   ├── adapters/          # MCP 文本兼容、artifact 展示和结构化结果适配
│   ├── infrastructure/    # Gemini Web RPC registry、payload builder 与纯 parser
│   ├── services/          # primary/compact 共用的应用服务与读回验证
│   ├── thinking_client.py # Thinking/Learning 模式传输层
│   ├── error_handler.py   # 错误处理装饰器
│   ├── constants.py       # 模型常量与配置
│   ├── remote_chat_cleanup_manager.py  # 远程聊天清理
│   └── tools/             # 工具模块
│       ├── __init__.py    # 分层工具注册入口
│       ├── annotations.py # MCP 工具安全/隐私注解常量
│       ├── manifest_data.py # 静态 UI 能力/工具清单；RPC probe 来自 registry
│       ├── utils.py       # 跨工具共享 helper（extract_remote_chat_id 等）
│       ├── chat.py        # 对话工具
│       ├── research.py    # Deep Research
│       ├── media.py       # 媒体生成
│       ├── image.py       # media.py 向后兼容别名
│       ├── file.py        # 文件工具
│       ├── prompts.py     # 本地 prompt 管理
│       └── manage.py      # 管理工具兼容注册适配器，不是 compact 依赖
├── tests/                 # pytest 测试套件（test_*.py）
├── evaluations/           # MCP contract evaluation prompts（gemini_web_mcp_contract.xml）
├── compatibility/         # Live canary 报告 schema 与上游依赖矩阵
├── scripts/               # 打包/发布、协议 smoke 与 opt-in canary CLI
├── .github/workflows/     # 离线 CI/release 与隔离的 live-canary workflow
├── .agents/skills/        # 公开分发用 Codex skill 副本
├── .codex/skills/         # 本地开发用 Codex skill 副本
└── docs/                  # 完整文档系统
    ├── README.md          # 文档中心
    ├── quickstart.md      # 快速开始
    ├── tools.md           # 工具使用
    ├── models.md          # 模型选择
    ├── configuration.md   # 配置说明
    ├── faq.md             # 常见问题
    ├── architecture.md    # 技术架构
    ├── changelog.md       # 更新历史
    ├── troubleshooting.md # 故障排查
    ├── contributing.md    # 贡献指南
    ├── manual-testing.md  # 实机测试清单
    ├── live-ui-coverage.md # 网页端能力对照
    └── launch-kit.md      # 发布分发套件
```

---

## 🔧 核心模块

### 1. Server (server.py)

**职责：**
- MCPServer 服务器初始化
- 所有工具注册
- 健康检查与管理工具
- 服务器入口点

**关键组件：**
- MCPServer 实例
- 工具注册函数调用
- 管理工具实现

---

### 2. Client Wrapper (client_wrapper.py)

**职责：**
- GeminiClient 封装
- Cookie 管理
- 会话存储与管理
- 客户端初始化

**关键对象：**
- `_client` - GeminiClient 单例
- `_sessions` - 会话存储字典

**核心函数：**
```python
get_gemini_client()     # 获取或创建客户端
initialize_client()     # 初始化与验证连接
store_session()         # 存储多轮会话
get_session()           # 获取会话
remove_session()        # 删除会话
reset_client()          # 完全重置
```

---

### 3. Constants (constants.py)

**职责：**
- 模型配置映射
- 常量定义
- 模型 Header 构建

**关键内容：**
```python
MODEL_CONFIG = {
    "fast": {
        "name": "gemini-3-flash",
        "hex_id": "...",
        "capacity_tail": 1,
        ...
    },
    ...
}
```

---

### 4. Tools 模块

#### Chat Tools (chat.py)
- 单次对话
- 多轮会话管理
- 图片输入支持

#### Research Tools (research.py)
- Deep Research 调用
- 报告格式化

#### Media Tools (media.py)
- 图像生成
- 视频生成
- 音乐生成

#### File Tools (file.py)
- 文件上传
- URL 分析

#### Management Services and Adapter
- `services/history.py`：历史记录的共享分页、读取、导出 helper
- `services/account.py`：账号 inventory parser 与只读 feature probe
- `services/notebooks.py`：原生 Notebook 读取及带读回校验的 chat move
- `services/scheduled.py`：定时操作读取、创建、删除与 registry/GetTask 双读回
- `services/gems.py`：Gem CRUD 与 mutation 后读回比较
- `services/manifest.py` / `services/doctor.py`：工具清单、Web 能力和本地预检
- `tools/manage.py`：保留既有 primary 工具名、参数和展示文本的注册/兼容层

---

### 5. 类型化领域结果

P0.3 在业务服务与 MCP 展示层之间加入统一结果契约：

```text
client / session / chat operation
             │
             ▼
DomainResult[data, error, warnings, meta]
             │
             ▼
MCP adapter ── TextContent.text（兼容）
             └─ TextContent._meta.domain_result（机器可读）
```

`src/domain/results.py` 定义稳定错误码、重试性、建议动作、操作状态和诊断关联 ID。
`src/adapters/mcp_results.py` 保留既有文本，并只把 JSON 安全的公开数据写入
`_meta.domain_result`。上游客户端、Cookie、session 实例、异步锁和原始响应对象不会序列化。
`src/adapters/mcp_sdk.py` 是唯一的 SDK v2 / `mcp-types` 运行时边界；MCPServer 进一步返回带
`resultType` 和经过 `outputSchema` 校验的 `structuredContent`，领域服务不依赖此协议适配层。
未知异常在适配边界分类；结构化结果通过 request/diagnostic ID 与包含原始证据的服务端日志关联。
compact 入口原有的 `Error: ...` 正文暂时保留，仅用于文本兼容，不属于稳定领域契约。

### 6. 共享应用服务

P0.4 把聊天域的请求构造、模型解析、客户端准备、会话发送、流式聚合与远端清理决策集中到
`src/services/chat.py`。两个 MCP 表面只负责各自的参数校验和文本格式：

```text
primary: src/tools/chat.py ─┐
                            ├─ ChatService ─ client/session/cleanup facades
compact: src/skill_server.py┘
```

适配器差异是显式配置：primary 继续传递 `gem` / `temporary`，compact 继续保持原有精简请求形状；
两边共享同一类型化 `DomainResult[ChatOperationData]`。迁移后的聊天处理器不再复制上游请求与清理逻辑。
`skill_server.py` 中仍有其他管理域对 `tools.manage` 私有 helper 的历史依赖，将在后续服务迁移阶段处理。

### 7. 统一 Artifact 模型

P1.1 在 media、file/URL 和 research report 工作流之间加入共享的 artifact 领域模型：

```text
Gemini response / local renderer
              │
              ▼
Artifact service
  ├─ deterministic identity
  ├─ remote URI extraction
  ├─ local file verification
  ├─ backend evidence
  └─ state classification
              │
              ▼
ArtifactResultData
  ├─ artifacts          # 输出产物
  ├─ input_artifacts    # 文件、URL、参考图
  └─ state              # remote/local/queued/empty/failed
              │
              ▼
primary / compact MCP adapters
```

`src/domain/artifacts.py` 定义 `Artifact`、`ArtifactResultData`、artifact 类型、状态与验证状态。
`src/services/artifacts.py` 是唯一的身份、响应提取、合并、文件验证和结果分类实现；两个 MCP
入口不再各自猜测媒体 URI。相同类型和远端 URI 会生成相同 `artifact_<sha256-prefix>` ID，因此
primary `gemini_generate_media` 与 compact `create` 可稳定引用同一产物。

远端 URI 的验证状态是 `unverified`，只表示在上游响应中观测到 URI，不声称已经下载或解码。
本地文件只有在路径存在且大小非零时才是 `local/verified`；同时记录 MIME、字节数，并在可用时
记录图像尺寸或音视频时长。不存在、空文件或写入失败分别进入 `failed` 或 `partial` 结果，不能
只凭上游返回文本宣称保存成功。请求别名、实际请求模型、声明的有效后端和响应中观测到的后端
分别保存在 `requested_model`、`request_model`、`effective_backend`、`observed_backend`，避免把
路由规则和运行时证据混为一谈。

### 8. 管理域与 RPC 合约

P1.2 把管理能力的 upstream contract 从 MCP handler 中提取到
`src/infrastructure/rpc_contracts.py`。registry 统一保存 RPC ID、source path、payload builder、
parser 名称、观测日期、稳定性和 mutation 校验策略；`rpc_parsers.py` 只接收已解码 body，返回
`success`、`empty`、`rejected` 或 `changed_shape`。每个注册 parser 都由
`tests/fixtures/rpc_management_cases.json` 的四类 fixture 覆盖。

```text
primary manage adapter ─┐
                       ├─ history/account/notebook/scheduled/Gem service
compact skill adapter ─┘          │
                                  ▼
                         RPC registry + pure parser
                                  │
                                  ▼
                           gemini-webapi client
```

`src/tools/__init__.py` 使用按需导入；导入 compact server 不会加载 `src.tools.manage`。Notebook
移动、scheduled create/delete 和 Gem create/update/delete 均返回 `verification_status`：mutation
响应只代表上游接受请求，最终状态由目标 Notebook 列表、scheduled registry/GetTask 或 Gem 列表
读回决定。未观察到目标、读回失败和响应无法确认都是显式状态，不等同于已验证成功。

### 9. 流与长任务语义

P1.7 将 Gemini Web 的传输形状和 MCP 客户端实际看到的交付方式分开描述：

```text
Gemini upstream chunks          Deep Research plan/start/wait
           │                                │
           ▼                                ▼
StreamTextAccumulator                deadline boundary
 delta / cumulative / mixed          cancellation propagation
           │                                │
           ▼                                ▼
one MCP TextContent                  LongOperationData
delivery=collected                   queued/running/completed/timed_out
```

`*_stream` 名称为兼容性保留，当前适配器不会声称 MCP 增量推送。`StreamTextAccumulator`
按状态归一化显式 delta、累计全文和混合片段，忽略重复或过时的累计 chunk；公开元数据只描述
观测到的语义与计数。

Deep Research 使用 `LongOperationData` 保存上游 research/chat ID、最新状态、轮询次数和报告
可用性。超时是本次等待的终态，不会被取消后迟到的协程结果改写；如果已经拿到上游 ID，
`continuation_possible` 仍为 true。调用方取消会继续向子任务传播，不会被一般异常边界吞掉。

---

## 📡 数据流

### 单次对话流程

```
用户请求
   │
   ▼
MCPServer 工具调用 (gemini_chat)
   │
   ▼
共享 ChatService
   │
   ▼
获取 GeminiClient
   │
   ▼
初始化 (client.init)
   │
   ▼
生成响应 (client.generate_content)
   │
   ▼
解析响应 (文本、图像、视频、音乐)
   │
   ▼
返回 TextContent
```

### 多轮会话流程

```
用户请求
   │
   ▼
gemini_start_chat
   │
   ▼
共享 ChatService
   │
   ▼
创建会话 (client.start_chat)
   │
   ▼
生成 session_id
   │
   ▼
存储会话
   │
   ▼
返回 session_id

后续对话：
gemini_send_message
   │
   ▼
查找会话
   │
   ▼
发送消息 (session.send_message)
```

---

## 🔐 认证架构

### Cookie 认证流程

1. **环境变量**：从环境读取 `GEMINI_PSID` 和 `GEMINI_PSIDTS`
2. **初始化**：传递 Cookie 到 `GeminiClient`
3. **刷新**：
   - 自动：每 9 分钟刷新
   - 手动：使用 `gemini_reset`

### Cookie 管理

```python
# client_wrapper.py
client = GeminiClient(psid, psidts, ...)
```

### 安全性

- Cookie 保存在内存中
- 不写入持久存储
- 通过环境变量配置
- 使用专门的研究账户

---

## 📊 模型选择系统

### 模型映射表

```python
"flash-lite" -> {"name": "3.1 Flash-Lite", ...}
"flash"      -> {"name": "gemini-3-flash", ...}
"fast"       -> {"name": "gemini-3-flash", ...}  # compatible alias
"pro"        -> {"name": "gemini-3-pro", ...}
```

聊天、文件、媒体和研究工具会先解析这些 MCP 别名；别名之外的模型
字符串会原样交给 `gemini-webapi` 的运行时模型注册表处理。网页端
`standard` / `extended` 思考等级作为独立 `thinking_level` 传输字段处理。

### 媒体模型绑定

| 聊天模型 | 图像模型 | 视频模型 | 音乐模型 |
|---------|---------|---------|---------|
| flash-lite | Nano Banana 2 | Veo 3.1 | Lyria 3 |
| flash | Nano Banana 2 | Veo 3.1 | Lyria 3 |
| pro | Nano Banana 2 | Veo 3.1 | Lyria 3 Pro |

实现上，`image` 首轮请求会统一落到 `Nano Banana 2`，而不是沿用聊天模型。
`pro` 图像 redo 属于网页生成后的二次 UI 动作，不作为单独首轮模型暴露。

---

## ⚡ 性能与可靠性

### 请求策略

- 自动重试（gemini-webapi 库处理）
- Cookie 自动刷新（9 分钟）
- 错误处理与优雅降级

### 会话管理

- 内存存储（无持久化）
- 会话 ID 随机生成
- 支持重置与清理

---

## 🔌 扩展性

### 如何添加新工具

1. 在 `src/tools/` 创建新文件（例如 `my_tool.py`）
2. 定义注册函数 `register_xxx_tools(mcp)`
3. 在 `server.py` 中调用
4. 可选：添加到文档

### 如何修改现有工具

1. 找到相应工具模块
2. 更新工具函数
3. 保持 MCPServer 装饰器与 v2 schema 契约
4. 更新文档（docs/tools.md）

---

## Live compatibility boundary

`src/services/compatibility.py` 从中央 RPC registry 执行只读 probe，并把 transport、
envelope、RPC rejection、body parser 和完成状态分开分类。该 service 只构造
schema allowlist 中的结构诊断，不让 raw response 或账号内容进入持久化结果。

`scripts/run_live_canary.py` 在导入账号 client lifecycle 之前验证 CLI flag、仓库启用变量
和专用账号确认变量。`.github/workflows/live-canary.yml` 进一步用独立 GitHub environment
隔离 secrets，并在上传脱敏 artifact 后创建/更新固定 drift issue。日常 CI 与 release
workflow 不引用这些 secrets，也不调用 live canary。

---

## 📚 依赖与技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.11 | 开发语言（受 `pyproject.toml` 约束） |
| MCPServer | mcp >= 2, < 3 | MCP SDK v2 服务器框架（`@mcp.tool(annotations=...)` 注册工具） |
| mcp-types | >= 2, < 3 | 独立协议模型、snake_case Python 字段与 wire alias |
| gemini-webapi | >= 2.0.0, < 3 | Gemini Web API 封装（依赖 `types.RPCData`、`constants.GRPC` 等 2.x API） |
| orjson | >= 3.11.7, < 4 | 媒体和 Thinking 请求的直接 JSON 编解码依赖 |

---

## 🔧 开发者注意事项

- 遵循代码风格与规范
- 更新文档与示例
- 测试功能完整性
