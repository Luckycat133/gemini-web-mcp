# Gemini Web MCP Server

> ⚠️ **免责声明**: 本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。

基于 Gemini Web 网页版逆向工程的 MCP (Model Context Protocol) 服务器，使得 Claude、VS Code 等 AI 应用能够通过 MCP 协议调用 Gemini 的能力。

## 功能特性

- ✅ 文本生成与多轮对话
- ✅ 图片生成（Nano Banana）
- ✅ 文件/图片上传与分析
- ✅ URL 分析（YouTube、网页等）
- ✅ 深度研究分析
- ✅ Cookie 自动刷新
- ✅ 支持多个 Gemini 模型

## 可用模型

- `unspecified` (默认)
- `gemini-3.0-pro`
- `gemini-3.0-flash`
- `gemini-3.0-flash-thinking`
- `gemini-2.5-pro`

## 快速开始

### 1. 获取 Cookie

1. 打开 Chrome 浏览器，访问 [gemini.google.com](https://gemini.google.com) 并登录
2. 按 F12 打开 DevTools → Application → Cookies → https://gemini.google.com
3. 复制 `__Secure-1PSID` 的值（必填）
4. 复制 `__Secure-1PSIDTS` 的值（可选，但推荐）

### 2. 配置 Claude Desktop

编辑 Claude Desktop 配置文件：

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/server.py"],
      "env": {
        "GEMINI_PSID": "your-__Secure-1PSID-value-here",
        "GEMINI_PSIDTS": "your-__Secure-1PSIDTS-value-here"
      }
    }
  }
}
```

或者如果你想用 pip 安装：

```json
{
  "mcpServers": {
    "gemini": {
      "command": "pipx",
      "args": ["run", "--spec", "/path/to/gemini-mcp-server", "gemini-mcp-server"],
      "env": {
        "GEMINI_PSID": "your-__Secure-1PSID-value-here",
        "GEMINI_PSIDTS": "your-__Secure-1PSIDTS-value-here"
      }
    }
  }
}
```

### 3. 安装依赖

在项目目录中：

```bash
pip install gemini-webapi mcp fastmcp
```

或者使用 uv：

```bash
uv pip install gemini-webapi mcp fastmcp
```

### 4. 重启 Claude Desktop

重启 Claude Desktop 后，你就可以使用 Gemini 的各种能力了！

## 可用工具

| 工具名称 | 描述 |
|---------|------|
| `gemini_chat` | 单次对话（非流式） |
| `gemini_start_chat` | 创建多轮对话会话 |
| `gemini_send_message` | 在现有会话中发送消息 |
| `gemini_list_sessions` | 列出活跃会话 |
| `gemini_reset_session` | 重置会话 |
| `gemini_generate_image` | 图片生成 |
| `gemini_upload_file` | 上传并分析文件 |
| `gemini_analyze_url` | 分析 URL 内容 |
| `gemini_research` | 深度研究分析 |
| `gemini_list_models` | 列出可用模型 |
| `gemini_health_check` | 检查连接健康 |
| `gemini_reset` | 重置客户端 |

## 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `GEMINI_PSID` | 是 | Cookie `__Secure-1PSID` 的值 |
| `GEMINI_PSIDTS` | 否 | Cookie `__Secure-1PSIDTS` 的值 |
| `GEMINI_PROXY` | 否 | 代理地址（可选） |

## 开发调试

### 1. 使用 MCP Inspector

```bash
pip install "mcp[cli]"
mcp dev src/server.py
```

### 2. 使用 TypeScript Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

## 项目结构

```
gemini-mcp-server/
├── pyproject.toml       # 项目配置
├── README.md            # 使用文档
├── .env.example         # 环境变量示例
├── src/
│   ├── __init__.py
│   ├── server.py        # MCP 服务器入口
│   ├── auth.py          # 认证与会话管理
│   └── tools/           # MCP 工具
│       ├── chat.py      # 聊天工具
│       ├── image.py     # 图片工具
│       ├── file.py      # 文件工具
│       └── research.py  # 研究工具
└── tests/
    └── test_imports.py  # 导入测试
```

## 已知限制

- 图片生成功能可能受地区限制
- 需要持续维护 cookie 以保持连接
- 功能可能随 Gemini Web 更新而变化
- 建议使用独立的 Google 账户用于研究

## 致谢

- [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) - 核心逆向工程库

## 许可证

AGPL-3.0
