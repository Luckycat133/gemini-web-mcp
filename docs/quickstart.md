# 快速开始指南

先用一条无需 Cookie 的命令证明安装、真实 MCP stdio 握手和文本工具调用都正常，再配置 Gemini 账号。

## 前置条件

- 已安装 [uv](https://docs.astral.sh/uv/)
- 需要实时调用时：一个已登录 Gemini Web 的账号
- 需要接入桌面/编辑器时：Codex、Claude Desktop、Claude Code、VS Code 或其他 MCP 客户端

## 1. 无账号预检

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-onboarding
```

`uvx` 会创建隔离环境并运行 `gemini-mcp-onboarding`。该客户端启动真实
`gemini-mcp-function`，核对 17 个工具并验证空提示的 `gemini_ask` 返回
`INVALID_ARGUMENT`。预检会移除 `GEMINI_PSID`、`GEMINI_PSIDTS` 和
`GEMINI_PSIDCC`，并输出 `mode=offline`、`credentials_accessed=false`；它不会访问 Gemini。

预检应输出 `profile=function`、`enabled_tools=17` 和 `text_tool=gemini_ask`。命令安装 [PR #39 的开发分支](source-update-integration.md)，需要固定版本时，把 `@` 后的分支名换成已审核的 commit SHA。

## 2. 配置实时账号

安装 `browser` extra 后，运行时可从用户已授权的本机浏览器自动发现 Cookie；默认开启自动发现与刷新。
完整桌面功能可使用 `gemini-mcp-server[all]`，其中包含浏览器 Cookie、图像校验和 Task 浏览器依赖。
本机系统可能要求解锁 Cookie 存储。若改用手动注入，从目标账号的浏览器开发者工具复制
`__Secure-1PSID` 和匹配的 `__Secure-1PSIDTS`，通过宿主秘密环境提供：

```bash
export GEMINI_PSID='your __Secure-1PSID value'
export GEMINI_PSIDTS='your __Secure-1PSIDTS value'
```

详细风险与浏览器获取方式见 [Cookie 获取指南](./cookie-setup.md)。
如果启用浏览器自动获取，macOS 钥匙串读取默认最多等待 15 秒；可用
`GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` 调整，超时会返回安全的诊断而不会无限挂起。

## 3. 配置 MCP 客户端

下面的 Claude Desktop 配置会从已授权、已登录的浏览器自动发现会话：

```json
{
  "mcpServers": {
    "gemini": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "gemini-mcp-server[all] @ git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101",
        "gemini-mcp-function"
      ]
    }
  }
}
```

使用手动凭据时，Codex、Claude Desktop、Claude Code 和 VS Code 的逐文件示例见
[客户端安装与验证](./client-examples.md)。

## 4. 选择工具面

| 工具面 | 使用场景 |
| --- | --- |
| `gemini-mcp-function` | 默认 17 个功能工具：问答、理解、会话、Task、图像／视频／音乐及 Research |
| `gemini-mcp-account` | 显式账号管理：历史、Notebook、Skills、偏好、Task 计划及浏览器 |
| `gemini-mcp-skill-server` | 固定四工具兼容入口：chat/create/edit/session |
| `gemini-mcp-assist` | 只需要第二意见、有据搜索、图像/混合输入理解或异步 Deep Research 的专注五工具面 |
| `GEMINI_TOOLS=model` / `core` | 在 `gemini-mcp-server` 中显式选择旧版模型／完整内容工具组 |
| `GEMINI_TOOLS=all` | 维护者验证账号、历史和管理能力；不适合作为通用默认 |

## 5. 显式实时验证

文本验证使用普通 Chat，并启用默认自动清理。命令必须显式允许账号访问：

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 \
  gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt 'Reply with exactly: Gemini MCP is connected'
```

图像验证安装 `image` extra，通过同一后台句柄取回结果，并要求返回位于指定目录内的真实文件、非零大小、
`image/*` MIME、正数尺寸和 `verification=verified`：

```bash
uvx \
  --from 'gemini-mcp-server[image] @ git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101' \
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
