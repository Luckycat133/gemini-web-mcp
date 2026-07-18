# 故障排除

按错误现象组织的诊断流程。若是概念性问题，先看 [FAQ](./faq.md)；若是具体报错，按下方分类定位。

---

## 第一步：跑预检

绝大多数问题都能用一个只读工具提前发现：

```text
gemini_doctor
```

`gemini_doctor` 会一次性检查：工具面加载情况、Cookie 状态、Chrome profile 对齐、媒体生成依赖（Pillow 等）。先看它的输出，再决定往下查哪一段。

> `gemini_doctor` 始终可用，不需要 `GEMINI_TOOLS=all`，也不需要写权限。

---

## Cookie / 认证

### 症状：`401` / `403` / "认证失败" / "未登录"

1. **Cookie 是否过期**：Gemini 的 `__Secure-1PSID` 会定期失效。重新到 gemini.google.com 取一次。
2. **`__Secure-1PSIDTS` 是否同步**：只更新 `__Secure-1PSID` 而忘了 `__Secure-1PSIDTS` 是常见坑。两者要一起换。
3. **是否提交到了 Git**：检查 `cookies.json` 是否被误提交。`.gitignore` 应已忽略；若已泄露，立即重置 Cookie。
4. **环境变量是否生效**：`echo $GEMINI_PSID` 确认 shell 里能读到；Claude Desktop / MCP 客户端要在其配置的 `env` 块里设置，不是 shell。

### 症状：多账号 / 多 Chrome profile 取到错误的 Cookie

用 `gemini_list_browser_cookie_profiles` 列出本机 Chrome profile，确认哪个 profile 对应目标账号，再用 `gemini_get_cookie_from_browser` 的 `profile` 参数显式指定。

> `cookie_manager.py` 会优先选择能读取 scheduled registry 的 profile，但多账号场景下手动指定更稳。

### 症状：从浏览器读 Cookie 失败

- 确认安装了可选依赖：`pip install -e ".[browser]"`（依赖 `browser-cookie3`）
- Chrome 必须处于关闭状态，或允许其他进程读取其 Cookie 数据库（部分系统会锁文件）
- macOS 上可能需要在"系统设置 → 隐私与安全 → 完全磁盘访问"里授权终端

---

## 连接与网络

### 症状：`Failed to perform, curl: (35)` 或 SSL/TLS 错误

OpenSSL / curl 配置问题：

1. 检查 Python 的 OpenSSL 版本：`python -c "import ssl; print(ssl.OPENSSL_VERSION)"`
2. 升级 Python 或重装 `certifi`：`pip install --upgrade certifi`
3. 某些企业网络会拦截 TLS；换网络或关闭 VPN 测试

### 症状：设置了 `GEMINI_PROXY` 但连不上

`GEMINI_PROXY` 指向不可达的端口时会被忽略（避免旧代理拖垮初始化）。如果你确实需要代理：

1. 确认代理端口在监听
2. 用 `curl -x $GEMINI_PROXY https://gemini.google.com` 验证代理本身能通
3. 代理必须是 HTTP/HTTPS 代理，不支持 SOCKS

### 症状：请求一直超时

- 先确认能访问 gemini.google.com（浏览器实测）
- `GEMINI_AUTO_REFRESH` 默认 true，会后台刷新 token；若设为 false 且 token 过期，会卡住——改回 true
- 公司网络可能拦截 WebSocket / 长连接

---

## MCP 客户端连接

### 症状：Claude Desktop 看不到 Gemini 工具

1. **配置文件路径**：macOS 是 `~/Library/Application Support/Claude/claude_desktop_config.json`，Windows 是 `%APPDATA%\Claude\claude_desktop_config.json`
2. **command 路径要用绝对路径**：尤其是 venv 里的 python，例如 `/Users/you/projects/gemini-web-mcp/.venv/bin/python`
3. **args 指向 `src/server.py`**：`["-m", "src.server"]` 或 `["/abs/path/src/server.py"]`
4. **env 块要有 `GEMINI_PSID`**：Claude Desktop 不会继承 shell 环境
5. 改完配置**完全退出** Claude Desktop（不是关窗口），再重启
6. 看 Claude Desktop 日志：`~/Library/Logs/Claude/mcp*.log`

### 症状：用 MCP Inspector 测试

```bash
mcp dev src/server.py
```

需要先装 `mcp[cli]`：`pip install "mcp[cli]"`。Inspector 能列出工具、查看 schema、直接调用，是排查注册/annotation 问题的最快路径。

### 症状：`GEMINI_TOOLS` 没生效

- `GEMINI_TOOLS=core` 是默认（高价值 AI 工具 + 始终可用的 manifest/cookie helpers）
- `GEMINI_TOOLS=all` 才有 history / account / Gems 等管理面
- 值是逗号分隔的 group 名，拼写要和源码里的 profile 一致

---

## 工具调用失败

### Deep Research 超时

1. `timeout_seconds` 调大：默认 600s，复杂主题给 `1200` 或 `1800`
2. `poll_interval_seconds` 不要小于 3（已被 `max(3, ...)` 钳制）
3. 确认账号有 **AI Plus** 订阅——免费账号无法用 Deep Research
4. 缩小研究主题范围，避免一轮要跑几十次搜索
5. 看 `gemini_deep_research` 返回的错误文本，它会区分"超时"和"功能不可用"

### 媒体生成失败

1. 装图像依赖：`pip install -e ".[image]"`（Pillow）
2. 图像首轮固定 Nano Banana 2，`pro` 不会换首轮模型——这是网页端规则，不是 bug
3. 视频最长 60s（Veo 3.1）
4. 音乐按模型分流：`flash` 系 → Lyria 3，`pro` → Lyria 3 Pro
5. 地区限制：部分功能在某些区域不可用，错误文本会提示

### 模型不可用 / 返回空

- 先 `gemini_list_models` 看当前账号实际可见的模型
- 别名（`fast`/`thinking`/`pro`）会解析到运行时注册表；账号没开通的模型不会出现
- `gemini_chat` 的 `model` 参数用别名，不是完整模型名

### 会话相关

- `session_id` 是 `gemini_start_chat` 返回的本地 ID（`sess_N`），不是 Gemini 的 `cid`
- `gemini_reset_session` 只清本地会话状态；要重置底层连接用 `gemini_reset`
- 多轮会话的图片/文件在上传后用 upload 返回的 ID 引用

---

## 历史与账号管理（需 `GEMINI_TOOLS=all`）

### 症状：`gemini_history` / `gemini_account_inventory` 不存在

这些是 facade 工具，只在 `all` 或对应 intent profile（`history` / `account-read`）下注册。默认 `core` 不含。重启服务时设 `GEMINI_TOOLS=all`。

### 症状：删除 / 定时操作"成功"但实际没生效

- `gemini_delete_chat`、`gemini_delete_scheduled_action` 是 destructive 操作，确认调用前看清 `destructiveHint`
- 定时操作的 `verification_status` 区分"RPC 已接受"和"registry 已验证"——前者可能最终不落库
- 用对应的 get/list 工具二次校验，别只信 create/delete 的返回

---

## 日志与调试

服务端日志默认走 stderr。MCP 客户端（Claude Desktop）会把 stderr 写进 `mcp*.log`。

调试技巧：
- 设 `GEMINI_TOOLS=core python -m src.server` 直接在前台跑，看实时输出
- `logging.getLogger("src").setLevel(logging.DEBUG)` 可在代码里临时开 DEBUG
- `gemini_doctor` 的输出可以贴进 issue，它是只读的，不含 Cookie 明文

---

## 还是解决不了

1. 复盘 `gemini_doctor` 输出
2. 查看 [Changelog](./changelog.md) 确认不是已知变更
3. 查看 [FAQ](./faq.md) 排除概念性误解
4. 提 issue 时附上：`gemini_doctor` 输出、`GEMINI_TOOLS` 值、相关工具名、错误文本（**不要附 Cookie**）
