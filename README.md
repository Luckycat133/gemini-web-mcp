# Gemini Web MCP Server

> ⚠️ **免责声明**: 本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。

基于 Gemini Web 网页版逆向工程的 MCP (Model Context Protocol) 服务器，使得 Claude、VS Code 等 AI 应用能够通过 MCP 协议调用 Gemini 的能力。

## 功能特性

- ✅ 文本生成与多轮对话
- ✅ 图片生成（Imagen 4）
- ✅ 文件/图片上传与分析
- ✅ URL 分析（YouTube、网页）
- ✅ Deep Research 深度研究
- ✅ Cookie 自动刷新
- ✅ 流式响应支持
- ✅ 无需官方 API Key，使用普通 Google 账户即可

## 快速开始

### 1. 获取 Cookie

1. 打开 Chrome 浏览器，访问 [gemini.google.com](https://gemini.google.com) 并登录
2. 按 F12 打开 DevTools → Application → Cookies
3. 复制 `__Secure-1PSID` 的值（必需）
4. 复制 `__Secure-1PSIDTS` 的值（推荐）

### 2. 安装

```bash
# 使用 uv 安装
uv pip install gemini-mcp-server[all]
```

### 3. 配置 Claude Desktop

编辑 Claude Desktop 配置文件：

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uv",
      "args": [
        "run",
        "--with", "gemini-mcp-server[all]",
        "gemini-mcp-server"
      ],
      "env": {
        "GEMINI_PSID": "your__Secure-1PSID_value",
        "GEMINI_PSIDTS": "your__Secure-1PSIDTS_value"
      }
    }
  }
}
```

### 4. 重启 Claude Desktop

重启 Claude Desktop 后，你就可以使用 Gemini 的各种能力了！

## 可用工具

| 工具名称 | 描述 |
|---------|------|
| `gemini_chat` | 单次对话（非流式） |
| `gemini_start_chat` | 创建多轮对话会话 |
| `gemini_send_message` | 在现有会话中发送消息 |
| `gemini_generate_image` | 图片生成/编辑 |
| `gemini_upload_file` | 上传文件供分析 |
| `gemini_analyze_url` | 分析 URL 内容 |
| `gemini_deep_research` | 启动 Deep Research 深度研究 |
| `gemini_list_chats` | 列出历史对话 |

## 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `GEMINI_PSID` | 是 | Cookie `__Secure-1PSID` 的值 |
| `GEMINI_PSIDTS` | 否 | Cookie `__Secure-1PSIDTS` 的值 |
| `GEMINI_LANGUAGE` | 否 | 响应语言（默认: `en`） |
| `GEMINI_PROXY` | 否 | 代理地址 |

## 开发调试

```bash
# 使用 MCP Inspector 调试
mcp dev src/server.py

# 或使用 TypeScript Inspector
npx -y @modelcontextprotocol/inspector
```

## 致谢

- [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) - 核心逆向工程库
- [AndyShaman/gemini-webapi-mcp](https://github.com/AndyShaman/gemini-webapi-mcp) - 参考实现

## 许可证

AGPL-3.0
