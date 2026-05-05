# 环境变量配置详解

完整的环境变量配置说明。

---

## 📋 环境变量列表

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|-------|
| `GEMINI_PSID` | ✅ | - | Cookie `__Secure-1PSID` 的值 |
| `GEMINI_PSIDTS` | ❌ | - | Cookie `__Secure-1PSIDTS` 的值 |
| `GEMINI_PROXY` | ❌ | - | HTTP/HTTPS 代理地址 |
| `GEMINI_AUTO_REFRESH` | ❌ | `true` | 自动刷新 Cookie |

---

## 🔑 Cookie 配置

### GEMINI_PSID (必需配置

这是最重要的环境变量，必须设置。

**获取方式：**
1. 访问 [gemini.google.com](https://gemini.google.com)
2. 登录 Google 账户
3. F12 → Application → Cookies
4. 复制 `__Secure-1PSID`

**值通常很长，以 `g.a000` 开头。

**配置示例：
```env
GEMINI_PSID=g.a000...[rest_of_your_cookie_here
```

### GEMINI_PSIDTS (推荐)

虽然可选，但强烈建议设置，提高稳定性。

**获取方式：**
同样在 Cookie 中找到 `__Secure-1PSIDTS`

**配置示例：**
```env
GEMINI_PSIDTS=sidts-CjE...
```

---

## 🔀 代理配置

如果您需要使用代理访问 Gemini：

```env
GEMINI_PROXY=http://127.0.0.1:7890
```

**支持的代理类型：
- HTTP 代理
- SOCKS5 代理

---

## 🤖 Claude Desktop 配置

### 完整配置示例

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/server.py"],
      "env": {
        "GEMINI_PSID": "your___Secure-1PSID_value",
        "GEMINI_PSIDTS": "your___Secure-1PSIDTS_value",
        "GEMINI_AUTO_REFRESH": "true",
        "GEMINI_PROXY": ""
      }
    }
  }
}
```

### macOS 配置位置

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

**Linux**: `~/.config/Claude/claude_desktop_config.json`

---

## 📄 .env 文件配置

### 创建 `.env` 文件（推荐）：

```bash
# 复制示例
cp .env.example .env

# 编辑配置
nano .env  # 或其他编辑器
```

### .env 示例内容

```env
# Gemini MCP Server 配置

# 认证配置 (必需)
GEMINI_PSID=g.a000...

# Cookie 配置 (推荐)
GEMINI_PSIDTS=sidts-CjE...

# 代理配置 (可选)
GEMINI_PROXY=http://127.0.0.1:7890

# 刷新配置 (可选)
GEMINI_AUTO_REFRESH=true

# 语言设置 (可选)
# LANGUAGE=zh-CN
```

---

## 🔄 Cookie 刷新机制

### 自动刷新

当 `GEMINI_AUTO_REFRESH=true`（默认）时：
- 每 9 分钟自动刷新 Cookie
- 提高连接稳定性
- 推荐开启

### 手动刷新

如果需要手动刷新，可以使用工具：

```
gemini_reset
```

---

## ⚠️ 安全注意事项

1. **不要** 将 `.env` 文件提交到 Git
2. **不要** 在公开场合分享 Cookie
3. **定期** 更新 Cookie（如果失效）
4. **使用** 独立的 Google 账户用于研究

---

## 🐛 配置问题排查

### 问题：认证失败

**检查：**
- `GEMINI_PSID 是否正确
- Cookie 是否过期
- Cookie 值是否完整复制

### 问题：代理不工作

**检查：**
- 代理地址格式正确
- 代理服务正在运行
- 网络连接正常

### 问题：Claude 无法连接

**检查：**
- Python 路径是否正确
- 项目路径是否正确
- 依赖是否已安装
