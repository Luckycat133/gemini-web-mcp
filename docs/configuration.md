# 环境变量配置

本指南详细介绍所有可用的环境变量配置。

---

将 `gemini-mcp-function` 和 `gemini-mcp-account` 分别配置为独立 MCP 连接，按任务启用。两者各暴露 6 个工具。`gemini-mcp-server` 在未设置 `GEMINI_TOOLS` 时使用功能面。显式指定 `function-full` 或 `account-full` 可加载原先各 17 个工具的目录。

浏览器自动读取需要安装 `browser` extra；完整桌面功能使用 `[all]`，包含 Cookie、图片校验和 Task 浏览器依赖。采用自动读取时省略 Cookie 占位值；显式 PSID 会绑定账号并限制刷新范围。完整配置见[客户端示例](client-examples.md)。

模型由当前登录账号的 Web 注册表发现，不通过环境变量把旧模型标签当作最新版本。Chat 的 `flash` 兼容默认优先选择 Hydrogen；媒体省略 `model` 也选择发现的 Hydrogen。Neon／Carbon 显式选择按当前注册表和套餐校验。普通媒体调用不使用 API Key；只有明确的 `gemini-omni-1.2-flash` API 兼容路线读取 `GEMINI_API_KEY` 并使用单独计费额度。见[模型与媒体路由](models.md)。

## 📋 环境变量列表

| 变量名 | 必填 | 说明 | 默认值 |
|--------|------|------|--------|
| GEMINI_PSID | 条件 | Cookie __Secure-1PSID；可由浏览器自动发现提供 | - |
| GEMINI_PSIDTS | ❌ | Cookie __Secure-1PSIDTS | - |
| GEMINI_PSIDCC | ❌ | Cookie __Secure-1PSIDCC，附加到客户端的 extra cookies | - |
| GEMINI_API_KEY | ❌ | 仅用于显式选择 `gemini-omni-1.2-flash` 的付费 API 视频。通过 MCP 服务环境提供 | - |
| GEMINI_WEB_MEDIA_TRANSPORT | ❌ | 网页音乐和视频使用原生浏览器 ProcessSession；显式 `sdk` 使用兼容路线。图片与付费 API 视频不受影响 | native |
| GEMINI_PROXY | ❌ | 代理地址 | - |
| GEMINI_AUTO_REFRESH | ❌ | 自动刷新 Cookie | true |
| GEMINI_BROWSER_AUTO_LOAD | ❌ | 启动时无 Cookie 则尝试读取浏览器；设为 false 可禁用 | true |
| GEMINI_BROWSER_COOKIE_PROFILE | ❌ | 限定自动读取和同账号认证恢复使用的 Chrome profile 目录，如 `Default` | 自动发现 |
| GEMINI_TASK_AUTO_APPROVE | ❌ | 在 MCP 后台为自有 Task 的受支持浏览器许可卡提交一次性批准。作用范围见 [Task 浏览器与权限偏好](task-preferences.md) | false |
| GEMINI_TASK_WEB_APPROVALS | ❌ | 配合 `GEMINI_TASK_AUTO_APPROVE=true` 检查网页上发起的 Task，处理受支持的一次性浏览器许可 | false |
| GEMINI_TASK_GOOGLE_TASKS_DELETE_RULES | ❌ | 精确 Task ID 和待办标题的 JSON 规则列表，每项授权一个 Google Tasks 单项删除审批。见[规则格式](task-preferences.md#自动批准指定的-google-tasks-删除项) | 空列表 |
| GEMINI_TASK_APPROVAL_CHROME | ❌ | 原生删除卡批准及网页音乐／视频所用 Chrome 或 Chromium 可执行文件路径 | macOS 已安装的 Chrome；Linux 自动查找 |
| GEMINI_OPERATION_DB_PATH | ❌ | 研究与媒体任务元数据 SQLite 文件；assist、compact 与主服务需使用同一路径；普通记录保留七天，仍受清理保护且有上游对话 ID 的过期研究仍可恢复 | `$XDG_STATE_HOME/gemini-web-mcp/operations.sqlite3`，未设置时为 `~/.local/state/gemini-web-mcp/operations.sqlite3` |
| GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS | ❌ | macOS 浏览器 Cookie 读取等待 Keychain 的单次超时（秒，范围 0.01–120） | 15 |
| GEMINI_TOOLS | ❌ | 主入口加载的工具组 | 未设置时加载 6 个功能工具。原先各 17 个工具使用 `function-full` / `account-full` |
| GEMINI_CHAT_RETENTION_SECONDS | ❌ | 一次性请求完成后的清理延迟；0 表示返回前尝试删除并验证，失败或无法验证时留在持久队列中重试 | 0 |
| GEMINI_CLEANUP_DB_PATH | ❌ | 绑定原账号的清理队列 SQLite 文件；只存 ID、期限和执行状态 | `$XDG_STATE_HOME/gemini-web-mcp/cleanup.sqlite3`，未设置时为 `~/.local/state/gemini-web-mcp/cleanup.sqlite3` |
| GEMINI_CONFIG_DIR | ❌ | skill_server 读取的本地 prompt 库目录（`prompts.json` 所在） | `.gemini` |
| GEMINI_COOKIE_PATH | ❌ | 主进程的 gemini_webapi cache 目录；独立 worker 使用自己的 0700 临时目录，保留父进程／用户缓存 | 主进程默认系统临时目录下的 `gemini_web_mcp_webapi_cookie_cache` |

操作和清理记录通过原账号的不可逆标识分区。请求中切换账号时，迟到的对话 ID 仍登记到原账号；不会用新账号继续会话、读回旧任务或删除旧对话。旧版操作库迁移后无法确认归属的记录不会被当前登录账号自动接管；新建任务只允许持有该任务租约的 worker 在首次鉴权后绑定一次。Cookie 和对话正文不写入这两个数据库。

当前凭据的 SHA-256 标识与早期本地安装使用的 PBKDF2 标识，仅在由同一个当前 PSID 派生时视为同一 scope。新记录继续使用 SHA-256；旧操作、清理租约、浏览器动作和 schedule guard 保留原绑定读取，不重建账本或接管空、未知及其他账号的绑定。两个 scope 存在相同动作的冲突记录时拒绝再次派发。

原生音乐和视频要求可用的 Chrome／Chromium 与浏览器依赖。填写提示后才选择并核验模型，发送前再次核验账号及媒体入口；没有核验通过就不发送。兼容路线可显式设置 `GEMINI_WEB_MEDIA_TRANSPORT=sdk`；失败不会自动切换路线重发。新原生路线的真实产物验收仍在进行，见[模型指南](models.md#多模态入口)。

音乐时长和人声由调用参数选择：`music_duration="short"|"standard"`、`music_vocals="instrumental"|"vocals"|"auto"`。两者默认 `None`，保留网页实际默认；用户明确要短纯音乐时才传 `short`／`instrumental`。它们不是环境变量，显式 SDK 路线带这些选项会在提交前拒绝。当前交付包已包含这些参数；实际请求仍可能被上游拒绝，成功与清理按独立补测记录核验。

浏览器鉴权不能只看 PSID 字符串或页面能否打开。原生 Cookie 注入按实际 Chrome 属性保留 JS 鉴权 Cookie 的可读性；已修复一律设置 HttpOnly 导致页面缺少账号历史的问题。用户不需要手动放宽 Cookie 或修改浏览器安全设置。媒体结果与清理仍分别核验，音乐可返回带音轨的 MP4，不保证 MP3。

原生媒体复用既有完成与清理合同：精确 CID／RID／candidate 和完成证据齐全后，先保存、核对本地文件及摘要，再释放操作保护并通过 canonical 精确删除与独立读回。保留设置、schedule guard、账号和租约校验继续生效。只有 CID 或未知结果仍保护，guest 缺少历史不作已删除证据。

显式 API 视频使用原 API Key 的不可逆标识来恢复任务，与 Cookie 账号隔离。请通过本地 MCP 环境或凭据管理器提供 Key，换 Key 后原任务需要原 Key 才能继续读取。API 视频保留原任务 ID 和已验证的本地文件摘要，提示词、Key 和视频字节不进入 SQLite。默认验证 MP4 后删除 API 交互，`retain_chat=true` 可保留。

---

## 🔧 工具组配置

服务支持分层加载，可以根据使用场景选择加载不同的工具组，降低 Token 消耗。

### 可用工具组

| 工具组 | 包含功能 | 用途 | Token 消耗 |
|--------|---------|------|-----------|
| `function-full` | 原先 17 个功能工具 | 按需兼容独立媒体、理解、会话和报告派生工具 | 高 |
| `account-full` | 原先 17 个账号工具 | 按需使用定时计划、Task 写操作、浏览器、Skill、偏好和清理 | 高 |
| `model` / `chat` | 仅对话和会话工具 | 只调用 Gemini 模型 | 低 |
| `history` | `gemini_history` 聚合 list/scan/search/read/export + manifest | 只整理或导出对话历史 | 低 |
| `history-organize` | `gemini_history` + `gemini_notebooks` + Notebook move | 将选定对话整理进 Gemini 原生 Notebook | 中 |
| `account-read` | `gemini_account_inventory` 聚合账号只读盘点 | 账号 Web surface 审计 | 低 |
| `scheduled-admin` | scheduled list/get/create/delete | 明确授权后管理定时操作 | 中 |
| `core` | 对话 + 媒体 + 文件/URL + Deep Research | 通用内容工作流 | 中 |
| `manage` | 聚合入口 + 历史、账号、scheduled、Gems 颗粒工具 | 兼容旧配置；普通 agent 不建议默认使用 | 高 |
| `prompts` | 本地提示词库存取 | 可选附加能力 | 低 |
| `all` | `core` + `manage` | 完整维护/验证工具面 | 高 |

### 配置示例

```bash
# 只调用模型
GEMINI_TOOLS=model

# 只读整理历史
GEMINI_TOOLS=history

# 整理历史到 native Notebook
GEMINI_TOOLS=history-organize

# 通用内容工作流
GEMINI_TOOLS=core

# 账号只读盘点
GEMINI_TOOLS=account-read

# 明确授权后管理定时操作
GEMINI_TOOLS=scheduled-admin

# 完整维护/验证工具面
GEMINI_TOOLS=all
```

### 旧 profile 的 Claude Desktop 配置

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "src.server"],
      "env": {
        "GEMINI_PSID": "your-psid-value",
        "GEMINI_TOOLS": "core"
      }
    }
  }
}
```

如果某个 AI agent 只需要调用模型，把示例中的 `GEMINI_TOOLS` 改为 `model`。
如果只需要整理历史，改为 `history`，让 agent 只看到 `gemini_history` 一个历史入口；
需要把对话移动到 Gemini 原生 Notebook 时再用 `history-organize`。
只做账号盘点时用 `account-read`，让 agent 通过 `gemini_account_inventory` 选择 surface。

---

## 🍪 Cookie 配置

### 必需变量

```bash
# 必填：__Secure-1PSID Cookie 值
GEMINI_PSID=xxxxxxxxxxxxxxx
```

### 可选变量

```bash
# 推荐：__Secure-1PSIDTS Cookie 值
GEMINI_PSIDTS=xxxxxxxxxxxxxxx
```

### 获取方式

1. 打开 Chrome 浏览器
2. 访问 [gemini.google.com](https://gemini.google.com)
3. 登录后按 F12 打开开发者工具
4. Application → Cookies → https://gemini.google.com
5. 复制 `__Secure-1PSID` 和 `__Secure-1PSIDTS` 的值

---

## 🌐 代理配置

如果需要通过代理访问：

```bash
# 代理地址
GEMINI_PROXY=http://proxy.example.com:8080

# 带认证的代理
GEMINI_PROXY=http://user:password@proxy.example.com:8080
```

---

## 🔄 自动刷新配置

```bash
# 启用 Cookie 自动刷新 (默认)
GEMINI_AUTO_REFRESH=true

# 禁用 Cookie 自动刷新
GEMINI_AUTO_REFRESH=false
```

当启用时，系统会自动：
- 监控 Cookie 状态
- 24 小时后提醒更新
- 自动从浏览器获取最新 Cookie（如果安装了 browser-cookie3）

在 macOS 上，`browser-cookie3` 需要通过 Keychain 读取 Chrome Safe Storage 密钥。
`GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` 限制每次 Keychain 等待；超时会返回脱敏的
`BROWSER_COOKIE_ACCESS_TIMEOUT` 诊断并恢复依赖函数，不会输出 Cookie 值。

---

## 📝 完整配置示例

```bash
# .env 文件示例
GEMINI_PSID=xxxxxxxxxxxxxxx
GEMINI_PSIDTS=xxxxxxxxxxxxxxx
GEMINI_PROXY=
GEMINI_AUTO_REFRESH=true
GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS=15
GEMINI_TOOLS=core
```

---

## 🔍 验证配置

图片、图片编辑、视频、音乐和研究默认后台执行；工具返回 `operation_id` 后可关闭原连接，再用同一句柄查询。未指定媒体 `output_dir` 时，文件保存到操作数据库同级的 `artifacts/<operation_id>/`，默认位于 `~/.local/state/gemini-web-mcp/` 下；`GEMINI_OPERATION_DB_PATH` 或 `XDG_STATE_HOME` 会影响该位置。`background=false` 可选择等待本次调用的结果。

使用 Cookie 状态工具验证配置：

```
gemini_get_cookie_status
```

需要验证真实上游连接时，再调用一个需要认证的聊天或管理工具。

---

## ⚠️ 常见问题

**问题：Cookie 总是过期**

解决：
1. 确保设置了 `GEMINI_PSIDTS`
2. 启用 `GEMINI_AUTO_REFRESH=true`
3. 安装 browser-cookie3: `pip install browser-cookie3`

**问题：部分工具不可用**

解决：
1. 检查 `GEMINI_TOOLS` 配置
2. 确保加载了需要的工具组
3. 重启服务器使配置生效

Task 浏览器截图与显式输入需要可选依赖 `gemini-mcp-server[browser-control]`（aiortc 与 Pillow）；`gemini-mcp-server[all]` 也包含它们。操作库记录浏览器动作的账号、身份、帧摘要和状态，不记录输入文本、坐标、截图或 WebRTC 凭据。截图作为单独的本地 PNG Artifact 保存。
