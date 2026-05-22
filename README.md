# Gemini Web MCP Server (v2.0)

> ⚠️ 免责声明: 本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。

基于 Gemini Web 网页版逆向工程的 MCP 服务器，支持 Claude Desktop、VS Code 等任何 MCP/Skills 兼容的 AI 应用。

---

## ✨ 主要功能 (v2.0)

### 🤖 模型支持
- **flash-lite** → Web UI `3.1 Flash-Lite`
- **flash** / **fast** → Web UI `3.5 Flash`
- **pro** → Web UI `3.1 Pro`
- 上述三个模型都支持 `thinking_level=standard` / `extended`
- **thinking** 仍保留为旧兼容别名

### 🎨 媒体生成
- **图像**: 首轮生成固定为 Nano Banana 2；`pro` 只对应网页生成后的 Pro redo 语义
- **视频**: Veo 3.1 (最长60秒，所有模型)
- **音乐**: `flash` 系列 → Lyria 3，`pro` → Lyria 3 Pro

### 💬 对话功能
- 单次对话 (支持流式输出 + 图片输入)
- 多轮会话 (支持流式输出)
- Temporary chat (不进入 Gemini 历史记录)
- 使用已保存 Gem 进行对话
- 会话管理

### 🖼️ 参考图像
- 媒体生成可附带参考图像
- 提示词可要求 Gemini 编辑或变换上传图像

### 🔧 管理功能
- Cookie 自动刷新
- Cookie 浏览器自动获取
- 智能错误处理

---

## 🚀 快速开始

### 1. 获取 Cookie

#### 方法 1: 手动获取
1. 打开 Chrome，访问 [gemini.google.com](https://gemini.google.com) 并登录
2. F12 → Application → Cookies → https://gemini.google.com
3. 复制 `__Secure-1PSID` 的值 (必填)
4. 复制 `__Secure-1PSIDTS` 的值 (可选)

#### 方法 2: 自动从浏览器获取
```bash
pip install browser-cookie3
```
然后使用 MCP 工具 `gemini_get_cookie_from_browser(browser="chrome")`

### 2. 配置 (Claude Desktop / 其他 MCP 客户端)

编辑配置文件 (Claude Desktop):
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "src.server"],
      "env": {
        "GEMINI_PSID": "your-__Secure-1PSID-value-here",
        "GEMINI_PSIDTS": "your-__Secure-1PSIDTS-value-here",
        "GEMINI_TOOLS": "core"
      }
    }
  }
}
```

### 3. 安装依赖

```bash
cd /workspace
pip install "gemini-webapi>=2.0.0" mcp fastmcp
```

可选功能:
```bash
# 浏览器 Cookie 自动获取
pip install browser-cookie3

# 图像处理
pip install pillow
```

### 4. 启动服务器

```bash
# 推荐默认工具面
GEMINI_TOOLS=core python -m src.server

# 全部功能
GEMINI_TOOLS=all python -m src.server
```

---

## 📦 环境变量

| 变量名 | 必填 | 说明 | 默认值 |
|--------|------|------|--------|
| GEMINI_PSID | ✅ | Cookie __Secure-1PSID | - |
| GEMINI_PSIDTS | ❌ | Cookie __Secure-1PSIDTS | - |
| GEMINI_PROXY | ❌ | 代理地址 | - |
| GEMINI_AUTO_REFRESH | ❌ | 自动刷新 Cookie | true |
| GEMINI_TOOLS | ❌ | 加载的工具组 | core |
| GEMINI_CHAT_RETENTION_SECONDS | ❌ | 默认远端对话保留时间；到期自动删除，设为 0 表示尽快删除 | 1800 |

---

## 🔧 工具组 (分层加载)

| 工具组 | 包含功能 | 用途 | Token 消耗 |
|--------|---------|------|-----------|
| `core` | 对话 + 媒体 + 文件/URL + Deep Research | 推荐默认组合 | 中 |
| `manage` | 历史对话、模型、Gems 管理 | 账户内容管理 | 中 |
| `prompts` | 本地提示词库存取 | 可选附加能力 | 低 |
| `all` | `core` + `manage` | 完整 Gemini 工作流 | 高 |

---

## 🛠️ 可用工具

### 对话工具
- `gemini_chat`: 单次对话
- `gemini_chat_stream`: 单次流式对话
- `gemini_start_chat`: 创建多轮会话，可指定 Gem 和 Temporary chat
- `gemini_send_message`: 会话消息，可沿用或覆盖 Temporary chat
- `gemini_send_message_stream`: 会话流式消息，可沿用或覆盖 Temporary chat
- `gemini_list_sessions`: 列会话
- `gemini_reset_session`: 重置会话

默认情况下，工具调用产生的 Gemini 网页端对话会在一段时间后自动删除。需要保留时传入 `retain_chat=true`；需要调整本次调用保留时间时传入 `delete_after_seconds`。

### 媒体工具
- `gemini_generate_media`: 图像/视频/音乐生成；视频/音乐是 Gemini Web 长任务，建议设置较长 `timeout_seconds`
- `gemini_generate_music`: 音乐生成便捷工具；默认走 `flash` → Lyria 3

### 文件和 URL
- `gemini_upload_file`: 上传并分析本地文件
- `gemini_analyze_url`: 分析网页或 YouTube 等 URL

### Deep Research
- `gemini_deep_research`: 创建研究计划、启动研究，并轮询最终报告或返回清晰进度状态

### 账户和内容管理
- `gemini_list_chats`: 列出历史对话
- `gemini_list_models`: 列出可用模型说明
- `gemini_manage_gems`: Gems 的 list/create/update/delete

### Cookie 管理
- `gemini_get_cookie_status`: 查看 Cookie 状态
- `gemini_get_cookie_from_browser`: 从浏览器自动获取 Cookie

### 管理工具
- `gemini_reset`: 重置客户端

---

## 🛡️ 智能错误处理

v2.0 新增智能错误处理，让 AI 可以自主解决常见问题：

| 错误类型 | 自动解决方案 | 建议工具 |
|---------|-------------|---------|
| 无 Cookie | 提示设置 PSID 或从浏览器获取 | `gemini_get_cookie_from_browser` |
| Cookie 过期 | 提示更新 Cookie | `gemini_get_cookie_from_browser` |
| 会话不存在 | 提示创建会话 | `gemini_start_chat` |
| 模型不可用 | 提示切换模型 | `gemini_list_models` |
| 网络错误 | 提示检查网络/代理 | - |
| 限流 | 提示稍后重试 | - |
| 图片加载失败 | 提示检查路径/安装 pillow | - |

---

## 📁 项目结构

```
gemini-mcp-server/
├── pyproject.toml          # 项目配置
├── README.md               # 使用文档
├── .env.example            # 环境变量示例
├── src/
│   ├── __init__.py
│   ├── server.py           # MCP 服务器主入口
│   ├── client_wrapper.py   # Gemini 客户端封装
│   ├── cookie_manager.py   # Cookie 管理模块
│   ├── constants.py        # 模型常量、配置
│   ├── error_handler.py    # 智能错误处理 (v2.0 新增)
│   └── tools/              # 工具集
│       ├── __init__.py     # 分层加载入口 (v2.0 新增)
│       ├── utils.py        # 共享工具函数 (v2.0 新增)
│       ├── chat.py         # 对话工具
│       ├── media.py        # 媒体生成
│       ├── image.py        # media.py 向后兼容别名
│       ├── file.py         # 文件和 URL 工具
│       ├── manage.py       # 聊天、模型、Gems 管理
│       ├── research.py     # Deep Research
│       └── prompts.py      # 预设提示词库
└── tests/                  # 测试
```

---

## 🔬 开发与调试

测试导入:
```bash
python -c "import sys; sys.path.insert(0, '.'); from src import client_wrapper, constants; print('✓ OK')"
```

使用 MCP Inspector 调试:
```bash
pip install "mcp[cli]"
mcp dev src/server.py
```

---

## ⚠️ 限制与注意事项

- AI Plus 功能需要订阅 (Pro 模型)
- 免费账户有每日配额限制
- Cookie 需要定期更新
- 部分功能可能有地区限制
- 建议使用独立的 Google 账户进行研究

---

## 📖 参考项目

- [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) - 核心逆向工程库

---

## 📄 许可证

AGPL-3.0
