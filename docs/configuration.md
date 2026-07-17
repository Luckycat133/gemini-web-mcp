# 环境变量配置

本指南详细介绍所有可用的环境变量配置。

---

## 📋 环境变量列表

| 变量名 | 必填 | 说明 | 默认值 |
|--------|------|------|--------|
| GEMINI_PSID | ✅ | Cookie __Secure-1PSID | - |
| GEMINI_PSIDTS | ❌ | Cookie __Secure-1PSIDTS | - |
| GEMINI_PSIDCC | ❌ | Cookie __Secure-1PSIDCC，附加到客户端的 extra cookies | - |
| GEMINI_PROXY | ❌ | 代理地址 | - |
| GEMINI_AUTO_REFRESH | ❌ | 自动刷新 Cookie | true |
| GEMINI_TOOLS | ❌ | 加载的工具组 | core |
| GEMINI_CHAT_RETENTION_SECONDS | ❌ | 默认远端对话保留时间，0 表示尽快删除 | 1800 |
| GEMINI_CONFIG_DIR | ❌ | skill_server 读取的本地 prompt 库目录（`prompts.json` 所在） | `.gemini` |
| GEMINI_COOKIE_PATH | ❌ | gemini_webapi cookie cache 目录；浏览器刷新 Cookie 时由 `client_manager` 写入临时目录隔离 | 系统临时目录下的 `gemini_web_mcp_webapi_cookie_cache` |

---

## 🔧 工具组配置 (v2.0 新增)

v2.0 支持分层加载，可以根据使用场景选择加载不同的工具组，降低 Token 消耗。

### 可用工具组

| 工具组 | 包含功能 | 用途 | Token 消耗 |
|--------|---------|------|-----------|
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

### Claude Desktop 配置

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

---

## 📝 完整配置示例

```bash
# .env 文件示例
GEMINI_PSID=xxxxxxxxxxxxxxx
GEMINI_PSIDTS=xxxxxxxxxxxxxxx
GEMINI_PROXY=
GEMINI_AUTO_REFRESH=true
GEMINI_TOOLS=core
```

---

## 🔍 验证配置

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
