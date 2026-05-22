# 工具使用手册

完整的 MCP 工具使用说明。

---

## 📋 工具分类

- [对话工具](#对话工具)
- [媒体工具](#媒体工具)
- [文件与 URL](#文件与-url)
- [Deep Research](#deep-research)
- [账户和 Gems](#账户和-gems)
- [Cookie 管理](#cookie-管理)
- [管理工具](#管理工具)

---

## 对话工具

### gemini_chat

单次对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: str - `flash-lite` / `flash` / `pro`，兼容别名，或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `image_paths`: list[str] - 可选图片路径
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 是否使用 Temporary chat
- `retain_chat`: bool - 是否保留远端聊天
- `delete_after_seconds`: int - 可选远端聊天清理时间

### gemini_chat_stream

单次流式对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `image_paths`: list[str] - 可选图片路径
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 是否使用 Temporary chat

### gemini_start_chat

创建多轮会话。

**参数：**
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - 创建会话后默认沿用的 `standard` / `extended`
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 后续会话消息默认沿用的 Temporary chat 状态

**返回：** 会话 ID，用于后续消息

### gemini_send_message

会话消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径
- `temporary`: bool - 可选，覆盖会话默认 Temporary chat 状态

### gemini_send_message_stream

会话流式消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径
- `temporary`: bool - 可选，覆盖会话默认 Temporary chat 状态

### gemini_list_sessions

列出会话。

**参数：** 无

### gemini_reset_session

重置会话。

**参数：**
- `session_id`: str - 会话 ID

---

## 媒体工具

### gemini_generate_media

通用媒体生成。

**参数：**
- `prompt`: str - 生成描述
- `media_type`: "image" | "video" | "music"
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `image_path`: str - 可选参考图片

**真实网页行为：**
- `image`: 首轮生成始终走 `Nano Banana 2`
- `music`: `flash` 系列走 `Lyria 3`，`pro` 走 `Lyria 3 Pro`
- `image + model=pro` 不会直接切换首轮图像后端；Pro redo 是网页生成后的二次操作

### gemini_generate_music

音乐生成（便捷工具）。

**参数：**
- `prompt`: str - 音乐描述
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `extended`)

媒体工具通过 Gemini Web 通用生成接口触发图像、视频和音乐能力。
账号可用性、上游排队和响应形状仍由 Gemini Web 决定。

---

## 文件与 URL

### gemini_upload_file

上传本地文件并分析。代码文件也走这个本地文件路径；它不等同于
Gemini Web 的 Google Drive 选择器。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)

### gemini_analyze_url

让 Gemini 分析网页或视频 URL。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)

---

## Deep Research

### gemini_deep_research

创建研究计划、启动研究，并在当前客户端能力允许时轮询最终结果。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `extended`)

---

## 账户和 Gems

### gemini_list_chats

列出当前客户端缓存的历史聊天。

### gemini_list_models

列出 MCP 模型别名和认证账户运行时模型注册表。

### gemini_manage_gems

列出、创建、更新或删除 Gems。

---

## Cookie 管理

### gemini_get_cookie_status

查看 Cookie 状态。

**参数：** 无

### gemini_get_cookie_from_browser

从浏览器获取 Cookie。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")

---

## 管理工具

### gemini_reset

重置客户端。

**参数：** 无

---

## 🛡️ 智能错误处理 (v2.0 新增)

v2.0 新增智能错误处理，遇到问题时会自动提供解决方案。

### 错误响应格式

```
✅ 错误: 未设置 GEMINI_PSID 环境变量
💡 解决方案: 请设置环境变量 export GEMINI_PSID=xxx 或使用 gemini_get_cookie_from_browser
🔧 可使用工具: gemini_get_cookie_from_browser
```

### 支持的错误类型

| 错误类型 | 解决方案 | 建议工具 |
|---------|---------|---------|
| 无 Cookie | 设置 PSID 或从浏览器获取 | gemini_get_cookie_from_browser |
| Cookie 过期 | 更新 Cookie | gemini_get_cookie_from_browser |
| 会话不存在 | 创建会话 | gemini_start_chat |
| 模型不可用 | 切换模型 | - |
| 网络错误 | 检查网络/代理 | - |
| 限流 | 稍后重试 | - |
| 图片加载失败 | 检查路径/安装 pillow | - |

### AI 自主解决

AI 可以根据错误信息自动调用相应工具解决问题，无需人工干预。

例如，遇到无 Cookie 错误时，AI 可以自动：
1. 识别问题（NO_COOKIE）
2. 调用 `gemini_get_cookie_from_browser`
3. 重新执行原任务

---

## 💡 使用提示

### 工具组建议

| 场景 | 推荐工具组 |
|------|---------|
| 默认接入大多数 AI 客户端 | `core` |
| 需要历史对话 / Gems / 运行时模型 | `core,manage` |
| 还需要本地提示词库 | `core,prompts` |

### 工具组配置

根据需要通过 `GEMINI_TOOLS` 环境变量配置加载的工具：

```bash
# 推荐默认工具面
GEMINI_TOOLS=core

# 增加账户内容管理
GEMINI_TOOLS=core,manage

# 全部功能
GEMINI_TOOLS=all
```
