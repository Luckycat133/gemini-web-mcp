# Gemini Web MCP Server (v3.0)

> ⚠️ 免责声明: 本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。

基于 Gemini Web 网页版逆向工程的 MCP 服务器，支持 Claude Desktop、VS Code 等 AI 应用。

---

## ✨ 主要功能 (v3.0 更新)

### 🤖 模型支持 (2026.5 更新)
- **fast** → gemini-3-flash (快速，免费)
- **thinking** → gemini-3-flash-thinking (推理链，免费)
- **pro** → gemini-3.1-pro (最强，AI Plus)

### 🎨 媒体生成
- **图像**: Nano Banana 2 (所有模型)
- **视频**: Veo 3.1 (最长60秒，所有模型)
- **音乐**:
  - fast → Lyria 3 Clip (30秒片段)
  - thinking/pro → Lyria 3 Pro (完整歌曲)

### 📚 Deep Research
- 深度研究功能 (需要 AI Plus)
- 来源引用和详细分析

### 💬 对话功能
- 单次对话 (支持流式输出)
- 多轮会话 (支持流式输出)
- 图片输入支持

### 🖼️ 图像编辑 (v3.0 新增)
- 提示词驱动的图像编辑
- 图像变体生成
- 支持参考图像

### 📝 预设提示词库 (v3.0 新增)
- 提示词 CRUD 管理
- 分类管理
- 快速访问常用提示词

### 📁 文件与 URL 分析
- 上传文件 (图片、PDF、文档等)
- URL 分析 (YouTube、网页等)

### 🔧 管理功能
- 历史对话管理
- Gem (自定义助手) CRUD
- 会话管理
- Cookie 自动刷新 (v3.0 新增)
- 会话持久化 (v3.0 新增)

---

## 🚀 快速开始

### 1. 获取 Cookie

#### 方法 1: 手动获取
1. 打开 Chrome，访问 [gemini.google.com](https://gemini.google.com) 并登录
2. F12 → Application → Cookies → https://gemini.google.com
3. 复制 `__Secure-1PSID` 的值 (必填)
4. 复制 `__Secure-1PSIDTS` 的值 (可选，但推荐)

#### 方法 2: 自动从浏览器获取 (v3.0 新增)
```bash
pip install browser-cookie3
```
然后使用 MCP 工具 `gemini_get_cookie_from_browser(browser="chrome")` 自动获取

### 2. 配置 Claude Desktop

编辑配置文件:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/server.py"],
      "env": {
        "GEMINI_PSID": "your-__Secure-1PSID-value-here",
        "GEMINI_PSIDTS": "your-__Secure-1PSIDTS-value-here"
      }
    }
  }
}
```

### 3. 安装依赖

```bash
pip install gemini-webapi>=1.20.0 mcp fastmcp
```

或者使用 uv:
```bash
uv pip install gemini-webapi>=1.20.0 mcp fastmcp
```

可选: 安装浏览器 Cookie 自动获取功能
```bash
pip install browser-cookie3
```

### 4. 重启 Claude Desktop

完成！现在可以在 Claude 中使用 Gemini 了。

---

## 📦 环境变量

| 变量名 | 必填 | 说明 | 默认值 |
|--------|------|------|--------|
| GEMINI_PSID | ✅ | Cookie __Secure-1PSID | - |
| GEMINI_PSIDTS | ❌ | Cookie __Secure-1PSIDTS | 自动提取 |
| GEMINI_PROXY | ❌ | 代理地址 | - |
| GEMINI_AUTO_REFRESH | ❌ | 自动刷新 Cookie | true |
| GEMINI_SESSIONS_DIR | ❌ | 会话保存目录 | ./.gemini_sessions |

---

## 🛠️ 可用工具

### 对话工具
- `gemini_chat`: 单次对话 (支持图片输入)
- `gemini_chat_stream`: 单次流式对话 (v3.0 新增)
- `gemini_start_chat`: 创建多轮会话
- `gemini_send_message`: 会话消息
- `gemini_send_message_stream`: 会话流式消息 (v3.0 新增)
- `gemini_list_sessions`: 列会话
- `gemini_reset_session`: 重置会话

### 研究工具
- `gemini_deep_research`: Deep Research (需 AI Plus)

### 媒体工具
- `gemini_generate_media`: 图像/视频/音乐生成
- `gemini_generate_music`: 音乐生成 (便捷工具)

### 图像编辑工具 (v3.0 新增)
- `gemini_edit_image`: 使用提示词编辑图像
- `gemini_variations`: 生成图像变体

### 文件工具
- `gemini_upload_file`: 上传并分析文件
- `gemini_analyze_url`: 分析网址

### 预设提示词库 (v3.0 新增)
- `gemini_manage_prompts`: 提示词管理 (CRUD)

### Cookie 管理 (v3.0 新增)
- `gemini_get_cookie_status`: 查看 Cookie 状态
- `gemini_get_cookie_from_browser`: 从浏览器自动获取 Cookie

### 管理工具
- `gemini_list_chats`: 历史对话
- `gemini_manage_gems`: Gem 管理
- `gemini_list_models`: 模型列表
- `gemini_list_features`: 功能列表
- `gemini_health_check`: 健康检查
- `gemini_reset`: 重置客户端

---

## 🎯 v3.0 新功能详解

### 1. 会话持久化
- 会话自动保存到本地 JSON 文件
- 服务器重启后自动恢复会话
- 可配置保存目录 (GEMINI_SESSIONS_DIR)
- 自动清理过期会话

### 2. Cookie 自动刷新
- 后台 Cookie 状态监控
- 浏览器 Cookie 自动获取
- Cookie 使用时长统计
- 24小时刷新提醒

### 3. 流式对话
- 流式输出支持
- 更流畅的用户体验
- 完全兼容现有工具

### 4. 图像编辑
- 提示词驱动的编辑
- 图像变体生成
- 参考图像支持

### 5. 预设提示词库
- 提示词分类管理
- 快速访问常用提示词
- 支持创建、读取、更新、删除操作

---

## 📁 项目结构

```
gemini-mcp-server/
├── pyproject.toml          # 项目配置
├── README.md               # 使用文档
├── .env.example            # 环境变量示例
├── src/
│   ├── __init__.py
│   ├── server.py           # MCP 服务器主入口
│   ├── client_wrapper.py   # Gemini 客户端封装 (含会话持久化)
│   ├── cookie_manager.py   # Cookie 管理模块 (v3.0 新增)
│   ├── constants.py        # 模型常量、配置
│   └── tools/              # 工具集
│       ├── __init__.py
│       ├── chat.py         # 对话工具 (含流式支持)
│       ├── research.py     # Deep Research
│       ├── media.py        # 媒体生成
│       ├── file.py         # 文件工具
│       ├── manage.py       # 管理工具
│       ├── image.py        # 图像编辑工具 (v3.0 新增)
│       └── prompts.py      # 预设提示词库 (v3.0 新增)
├── tests/                  # 测试 (可选)
├── .gemini_sessions/       # 会话数据目录 (自动创建)
└── prompts.json            # 预设提示词存储 (自动创建)
```

---

## 🔬 开发与调试

使用 MCP Inspector 调试:

```bash
pip install "mcp[cli]"
mcp dev src/server.py
```

或使用 TypeScript Inspector:

```bash
npx -y @modelcontextprotocol/inspector
```

---

## ⚠️ 限制与注意事项

- AI Plus 功能需要订阅 (Deep Research、Pro 模型)
- 免费账户有每日配额限制
- Cookie 需要定期更新
- 部分功能可能有地区限制
- 建议使用独立的 Google 账户进行研究

---

## 📖 参考项目

- [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) - 核心逆向工程库

---

## 📄 许可证

AGPL-3.0
