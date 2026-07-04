# Cookie 获取指南

详细介绍如何获取 Gemini Web 的认证 Cookie。

---

## 📋 Cookie 类型

| Cookie 认证需要以下 Cookie：

| Cookie 名称 | 必填 | 说明 |
|------------|------|------|
| `__Secure-1PSID` | ✅ | 主要认证 Cookie |
| `__Secure-1PSIDTS` | 推荐 | 额外稳定性 Cookie |
| `__Secure-3PSID` | 可选 | 另一种 3PSID 变体 |

---

## 🚀 如何获取 Cookie

### Chrome 浏览器（推荐）

#### macOS / Chrome 浏览器获取：

1. **打开 Gemini** 浏览器（Chrome）
2. **访问** [gemini.google.com](https://gemini.google.com)
3. **登录** 您的 Google 账户

4. **打开开发者工具**，按：
   - macOS: `Cmd + Opt + I
   - Windows/Linux: `F12` 或 `Ctrl + Shift + I

5. **选择 Application 标签**
   - 顶部标签栏中找到 Application
   - 点击 "Application"

6. **找到 Cookies**
   - 左侧菜单找到 "Cookies"
   - 展开 "Cookies"
   - 选择 "https://gemini.google.com"

7. **找到所需 Cookie

8. **复制**值

---

#### 快速找到这两个：

- `__Secure-1PSID`（✅必需✅必需的的值值
   - 值通常以 `g.a000` 开始
   - 它很长，请完整复制
  
- `__Secure-1PSIDTS`（推荐，值

---

### Firefox 浏览器

在 Firefox 类似：

1. 访问 gemini.google.com，登录
2. 打开开发者工具 (F12)
3. 存储 (Storage) → Cookies
4. 找到并复制

---

## 📋 Cookie格式是什么样子？

### `__Secure-1PSID`

通常长这样：

```
g.a000...your-cookie-value...
```

### `__Secure-1PSIDTS`

通常：

```
sidts-CjEB...your-cookie-value...
```

⚠️ 重要：完整复制 Cookie！

---

## ⚙️ 如何复制说明

⚠️ ⚠️ ⚠️

**重要提示：

1. **完整复制**：值，不要截断
2. **不要有空格或尾随空格
3. **保持原样，不要修改
4. **不要遗漏最后面部分

---

## 💻 配置到环境变量

### Claude Desktop 配置

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/server.py"],
      "env": {
        "GEMINI_PSID": "g.a000...",
        "GEMINI_PSIDTS": "sidts-CjEB..."
      }
    }
  }
}
```

### .env 文件配置

```env
GEMINI_PSID=<your-psid-cookie-value>
GEMINI_PSIDTS=<your-psidts-cookie-value>
GEMINI_PROXY=http://127.0.0.1:7890
GEMINI_AUTO_REFRESH=true
```

---

## ❓ Cookie 过期了怎么办？

### 重新获取 Cookie！Cookie 会过期，如果出现认证错误，按照步骤重新获取。

### Cookie 过期表现

### 如何验证？

1. 重新访问 Gemini
2. 检查是否需要重新登录
3. 重新获取新的 Cookie
4. 更新配置
5. 重启 Claude Desktop

---

## 🚨 Cookie？

### 环境变量更新后：

1. 更新后：

1. 关闭 Claude Desktop

2. 重新打开

重启后新的环境变量会生效。
