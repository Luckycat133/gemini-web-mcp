# 技术架构

深入了解 Gemini MCP Server v2.1 的设计与实现。

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
│         Gemini MCP Server (FastMCP)                      │
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
├── README.md               # 项目文档
├── .env.example            # 环境变量示例
├── .gitignore             # Git 忽略规则
├── src/
│   ├── __init__.py        # 包初始化（版本号）
│   ├── server.py          # MCP 服务器主入口
│   ├── client_wrapper.py  # Gemini 客户端封装
│   ├── constants.py       # 模型常量与配置
│   └── tools/             # 工具模块
│       ├── __init__.py
│       ├── chat.py        # 对话工具
│       ├── research.py    # Deep Research
│       ├── media.py       # 媒体生成
│       ├── file.py        # 文件工具
│       └── manage.py      # 管理工具
└── docs/                  # 完整文档系统
    ├── README.md          # 文档中心
    ├── quickstart.md      # 快速开始
    ├── tools.md           # 工具使用
    ├── models.md          # 模型选择
    ├── configuration.md   # 配置说明
    └── faq.md             # 常见问题
```

---

## 🔧 核心模块

### 1. Server (server.py)

**职责：**
- FastMCP 服务器初始化
- 所有工具注册
- 健康检查与管理工具
- 服务器入口点

**关键组件：**
- FastMCP 实例
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

#### Manage Tools (manage.py)
- 聊天记录管理
- Gem 管理
- 模型与功能列表

---

## 📡 数据流

### 单次对话流程

```
用户请求
   │
   ▼
FastMCP 工具调用 (gemini_chat)
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
3. 保持 FastMCP 装饰器
4. 更新文档（docs/tools.md）

---

## 📚 依赖与技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 开发语言 |
| FastMCP | latest | MCP 服务器框架 |
| gemini-webapi | latest | Gemini Web API 封装 |

---

## 🔧 开发者注意事项

- 遵循代码风格与规范
- 更新文档与示例
- 测试功能完整性
