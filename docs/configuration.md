# 环境变量配置

本指南详细介绍所有可用的环境变量配置。

---

## 📋 环境变量列表

| 变量名 | 必填 | 说明 | 默认值 |
|--------|------|------|--------|
| GEMINI_PSID | ✅ | Cookie __Secure-1PSID | - |
| GEMINI_PSIDTS | ❌ | Cookie __Secure-1PSIDTS | - |
| GEMINI_PROXY | ❌ | 代理地址 | - |
| GEMINI_AUTO_REFRESH | ❌ | 自动刷新 Cookie | true |
| GEMINI_TOOLS | ❌ | 加载的工具组 | basic |

---

## 🔧 工具组配置 (v2.0 新增)

v2.0 支持分层加载，可以根据使用场景选择加载不同的工具组，降低 Token 消耗。

### 可用工具组

| 工具组 | 包含功能 | 用途 | Token 消耗 |
|--------|---------|------|-----------|
| `basic` | 对话功能 | 基础对话 | 低 |
| `media` | 媒体生成 + 图像编辑 | 创作场景 | 中 |
| `advanced` | 提示词管理 | 高级用户 | 中 |
| `all` | 全部功能 | 完整体验 | 高 |

### 配置示例

```bash
# 仅加载基础对话功能（最小 Token 消耗）
GEMINI_TOOLS=basic

# 基础 + 媒体功能
GEMINI_TOOLS=basic,media

# 全部功能
GEMINI_TOOLS=all

# 自定义组合
GEMINI_TOOLS=basic,media,advanced
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
        "GEMINI_TOOLS": "basic,media"
      }
    }
  }
}
```

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
GEMINI_TOOLS=basic,media
```

---

## 🔍 验证配置

使用健康检查工具验证配置：

```
gemini_health_check
```

如果配置正确，应该看到 "✅ Gemini 连接正常"。

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
