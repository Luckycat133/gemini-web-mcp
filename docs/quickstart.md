# 快速开始指南

先用一条无需 Cookie 的命令证明安装、真实 MCP stdio 握手和文本工具调用都正常，再配置 Gemini 账号。

## 前置条件

- 已安装 [uv](https://docs.astral.sh/uv/)
- 需要实时调用时：一个已登录 Gemini Web 的账号
- 需要接入桌面/编辑器时：Codex、Claude Desktop、Claude Code、VS Code 或其他 MCP 客户端

## 1. 无账号预检

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

`uvx` 会创建隔离环境并运行 `gemini-mcp-onboarding`。该客户端启动真实
`gemini-mcp-server`，协商 MCP 协议，然后调用静态文本工具
`gemini_get_tool_manifest`。预检会移除 `GEMINI_PSID`、`GEMINI_PSIDTS` 和
`GEMINI_PSIDCC`，并输出 `mode=offline`、`credentials_accessed=false`；它不会访问 Gemini。

公开文档使用 `@main` 指向当前已审核源码。生产或可复现实验应把它替换为已审核 commit SHA。

## 2. 配置实时账号

1. 登录 [gemini.google.com](https://gemini.google.com)。
2. 在浏览器开发者工具的 Application → Cookies 中复制 `__Secure-1PSID`；
   `__Secure-1PSIDTS` 可选但推荐。
3. 把 Cookie 放入客户端的密码输入或宿主环境变量，不要写入命令行、仓库或日志。

```bash
export GEMINI_PSID='your __Secure-1PSID value'
export GEMINI_PSIDTS='your __Secure-1PSIDTS value'
```

详细风险与浏览器获取方式见 [Cookie 获取指南](./cookie-setup.md)。
如果启用浏览器自动获取，macOS 钥匙串读取默认最多等待 15 秒；可用
`GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` 调整，超时会返回安全的诊断而不会无限挂起。

## 3. 配置 MCP 客户端

Claude Desktop 最小配置如下；请仅在本机替换占位符：

```json
{
  "mcpServers": {
    "gemini": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Luckycat133/gemini-web-mcp@main",
        "gemini-mcp-server"
      ],
      "env": {
        "GEMINI_PSID": "REPLACE_WITH_SECURE_1PSID",
        "GEMINI_PSIDTS": "REPLACE_OR_REMOVE_IF_UNAVAILABLE",
        "GEMINI_TOOLS": "model",
        "GEMINI_AUTO_REFRESH": "false"
      }
    }
  }
}
```

Codex、Claude Desktop、Claude Code 和 VS Code 的逐文件示例见
[客户端安装与验证](./client-examples.md)。

## 4. 选择工具面

| 工具面 | 使用场景 |
| --- | --- |
| `GEMINI_TOOLS=model` | 文本/模型调用的首选起点 |
| `GEMINI_TOOLS=core` | 图片、视频、音乐、文件、URL 或 Deep Research |
| `gemini-mcp-skill-server` | 需要固定十一工具、低 token facade |
| `GEMINI_TOOLS=all` | 维护者验证账号、历史和管理能力；不适合作为通用默认 |

## 5. 显式实时验证

文本验证使用 temporary chat，必须显式允许账号访问：

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main \
  gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt 'Reply with exactly: Gemini MCP is connected'
```

图像验证安装 `image` extra，并要求返回位于指定目录内的真实文件、非零大小、
`image/*` MIME、正数尺寸和 `verification=verified`：

```bash
uvx \
  --from 'gemini-mcp-server[image] @ git+https://github.com/Luckycat133/gemini-web-mcp@main' \
  gemini-mcp-onboarding image \
  --allow-live-account \
  --prompt 'A two-color geometric cat icon on a plain background' \
  --output-dir ./gemini-artifacts \
  --filename onboarding-cat
```

文档和 PR CI 只验证离线 fixture/协议/打包路径；没有专用测试账号时，不应把预期路由写成已观察到的 Gemini 后端行为。

## 下一步

- [工具使用手册](./tools.md)
- [模型选择指南](./models.md)
- [客户端安装与验证](./client-examples.md)
- [常见问题](./faq.md)
