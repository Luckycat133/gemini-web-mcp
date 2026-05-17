# 快速开始指南

本指南将帮助您在 5 分钟内开始使用 Gemini MCP Server v2.0。

---

## 📋 前置条件

- Python 3.10+
- Claude Desktop 或支持 MCP 的应用
- Google 账户（免费或 AI Plus）

---

## 🚀 步骤 1：获取 Cookie

1. 打开 Chrome 浏览器
2. 访问 [gemini.google.com](https://gemini.google.com)
3. 登录您的 Google 账户
4. 按 F12 打开开发者工具
5. 选择 "Application" → "Cookies" → "https://gemini.google.com"
6. 复制以下两个 Cookie 的值：
   - `__Secure-1PSID` (必填)
   - `__Secure-1PSIDTS` (可选，但推荐)

详细说明请查看 [Cookie 获取指南](./cookie-setup.md)。

---

## 📦 步骤 2：安装项目

```bash
# 克隆或下载项目
cd gemini-mcp-server

# 安装依赖
pip install "gemini-webapi>=1.20.0" mcp fastmcp

# 或使用 uv（推荐）
uv pip install "gemini-webapi>=1.20.0" mcp fastmcp
```

---

## ⚙️ 步骤 3：配置环境变量

### 方法 A：使用 Claude Desktop 配置

编辑您的 Claude Desktop 配置文件：

**macOS**:
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Windows**:
```
%APPDATA%\Claude\claude_desktop_config.json
```

**Linux**:
```
~/.config/Claude/claude_desktop_config.json
```

添加以下内容：

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/server.py"],
      "env": {
        "GEMINI_PSID": "your___Secure-1PSID_value",
        "GEMINI_PSIDTS": "your___Secure-1PSIDTS_value"
      }
    }
  }
}
```

### 方法 B：使用环境变量文件

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入您的 Cookie 值：

```env
GEMINI_PSID=your___Secure-1PSID_value
GEMINI_PSIDTS=your___Secure-1PSIDTS_value
GEMINI_PROXY=  # 可选：代理地址
GEMINI_AUTO_REFRESH=true
```

---

## 🔄 步骤 4：重启 Claude Desktop

1. 完全关闭 Claude Desktop
2. 重新启动
3. 在 Claude 中您应该会看到 Gemini MCP Server 可用

---

## 🎉 步骤 5：开始使用！

### 基础对话

尝试发送以下消息：

```
使用 gemini 快速回答：什么是量子计算？
```

### 图像生成

```
请用 Gemini 生成一张猫咪的卡通图片
```

### 多轮对话

```
gemini_start_chat
```

然后继续发送消息。

---

## 💡 下一步

- 查看 [工具使用手册](./tools.md) 了解所有可用工具
- 阅读 [模型选择指南](./models.md) 选择合适的模型
- 查看 [媒体生成教程](./media-generation.md) 尝试图像、视频和音乐生成

---

## ⚠️ 常见问题

**问题：Claude 无法连接到 MCP 服务器**

解决：
1. 检查路径是否正确
2. 确认环境变量是否正确设置
3. 查看 Claude 的错误日志

**问题：Gemini 响应错误**

解决：
1. 检查 Cookie 是否过期
2. 确认 Cookie 格式正确
3. 查看网络连接

更多问题请查看 [FAQ](./faq.md)。
