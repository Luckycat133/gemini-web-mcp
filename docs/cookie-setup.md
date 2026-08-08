# Cookie 获取与安全配置

实时 Gemini Web 调用需要账号 Cookie。离线 onboarding、manifest 和协议验证不需要 Cookie；先运行
[`gemini-mcp-onboarding`](./quickstart.md)，确认安装与 MCP stdio 正常后再配置账号。

> Gemini Web 是逆向接入面。Cookie 等同于账号访问凭据，请优先使用专用测试账号，并自行评估 Google
> 服务条款和账号风控风险。

## 所需 Cookie

| 名称 | 要求 | 用途 |
| --- | --- | --- |
| `__Secure-1PSID` | 必需 | 主要 Gemini Web 认证 Cookie |
| `__Secure-1PSIDTS` | 推荐 | 提高会话稳定性；刷新时应与 PSID 一起更新 |
| `__Secure-1PSIDCC` | 可选 | 作为额外认证 Cookie 传给客户端 |

不要在 issue、日志、命令行参数或 Git 仓库中粘贴这些值。

## 方法一：手动复制

1. 使用目标账号登录 [gemini.google.com](https://gemini.google.com)。
2. 打开浏览器开发者工具：macOS 使用 `Cmd+Option+I`，Windows/Linux 使用 `F12`。
3. 进入 Application（Firefox 为 Storage）→ Cookies → `https://gemini.google.com`。
4. 完整复制 `__Secure-1PSID`，并建议同时复制 `__Secure-1PSIDTS`。
5. 通过 MCP 客户端的 secret/password 输入或宿主环境变量注入，不要写进共享配置。

```bash
export GEMINI_PSID='REPLACE_LOCALLY'
export GEMINI_PSIDTS='REPLACE_LOCALLY'
```

修改客户端配置后，需要完全重启对应 MCP 宿主进程。

## 方法二：从本地浏览器加载

安装 browser extra：

```bash
pip install -e ".[browser]"
```

先列出不含 Cookie 原值的 profile 诊断：

```text
gemini_list_browser_cookie_profiles(browser="chrome", validate=false)
```

多账号时显式选择目标 profile：

```text
gemini_get_cookie_from_browser(browser="chrome", profile="Profile 1")
```

`gemini_get_cookie_from_browser` 会把选中的 Cookie 加载到当前 MCP 进程；它不会把原值放进工具响应。
primary 和 compact 入口也提供对应的 `cookie(action="profiles|get")` 工作流。

## macOS Keychain

`browser-cookie3` 需要通过 macOS Keychain 读取 Chrome Safe Storage 密钥。首次读取可能弹出授权窗口。
仓库会限制每次 Keychain 等待，避免未响应的授权请求无限挂起：

```bash
export GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS=15
```

允许范围为 0.01–120 秒。超时时 profile JSON 会包含
`error_code="BROWSER_COOKIE_ACCESS_TIMEOUT"`，正文只说明 Keychain 超时；不会返回 Cookie 值。

如果确实要允许更长时间完成系统授权，可临时提高该值并重试。不要通过脚本绕过 Keychain，也不要把
Chrome profile 数据库复制进仓库。

## 验证

1. `gemini_get_cookie_status`：确认当前运行时是否已有 Cookie，不访问 Gemini。
2. `gemini_doctor(validate_browser=false)`：执行本地预检；profile 读取仍可能访问本机 Keychain。
3. 只有明确允许账号访问时，再调用 temporary chat 或专用账号的只读 live canary。

浏览器 profile 中存在 PSID 只证明本地凭据可读，不证明 Cookie 仍有效，也不证明这是专用测试账号。

## 常见问题

### 没有找到 profile 或 PSID

- 确认目标账号已在浏览器中登录 Gemini。
- 关闭 Chrome 后重试，排除 Cookie 数据库锁。
- macOS 检查终端或 MCP 宿主的完全磁盘访问和 Keychain 授权。
- 多 profile 场景先用 `gemini_list_browser_cookie_profiles`，再显式传入 `profile`。

### 返回 Keychain timeout

- 查找并处理 macOS 授权提示。
- 需要时提高 `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS`，上限 120 秒。
- 无法确认授权来源时改用手动 Cookie 注入，不要禁用系统安全机制。

### 认证突然失效

- 同时刷新 `__Secure-1PSID` 与 `__Secure-1PSIDTS`。
- 重启 MCP 宿主或调用 `gemini_reset` 让客户端使用新凭据。
- 不要把失败误判成模型或 RPC 漂移；先检查 `gemini_get_cookie_status` 和 `gemini_doctor`。

## 安全清单

- 使用专用、非个人账号进行 live 验证。
- 永远不要提交 `.env`、Cookie 数据库、`cookies.json` 或日志中的凭据。
- Cookie 泄露后立即在 Google 账号侧撤销会话并重新登录。
- 仅在用户明确要求时读取私人聊天或执行远端删除。
