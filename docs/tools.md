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

## 当前支持的工具

下面这份清单对应当前仓库真实注册结果，而不是历史文档残留。

### 默认启用 (`GEMINI_TOOLS=core`)

- `gemini_chat`
- `gemini_chat_stream`
- `gemini_start_chat`
- `gemini_send_message`
- `gemini_send_message_stream`
- `gemini_list_sessions`
- `gemini_reset_session`
- `gemini_generate_media`
- `gemini_generate_music`
- `gemini_upload_file`
- `gemini_analyze_url`
- `gemini_deep_research`
- `gemini_get_tool_manifest`
- `gemini_get_cookie_status`
- `gemini_get_cookie_from_browser`
- `gemini_reset`

### 可选启用

`GEMINI_TOOLS=all` 额外增加：

- `gemini_list_chats`
- `gemini_search_chats`
- `gemini_read_chat`
- `gemini_export_chat`
- `gemini_delete_chat`
- `gemini_inspect_account`
- `gemini_get_web_capabilities`
- `gemini_probe_web_features`
- `gemini_list_public_links`
- `gemini_get_usage_limits`
- `gemini_list_library_capabilities`
- `gemini_list_scheduled_actions`
- `gemini_get_tool_mode_status`
- `gemini_list_models`
- `gemini_manage_gems`

`GEMINI_TOOLS=prompts` 单独提供：

- `gemini_manage_prompts`

### 已移除 / 已合并

- `gemini_list_features` 已移除
- 旧的独立图片工具已并入 `gemini_generate_media`

---

## 对话工具

### gemini_chat

单次对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: str - `flash-lite` / `flash` / `pro`，兼容别名，或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `learning_mode`: str - 可选；`interactive_quiz`/`quiz`、`flashcards`、`practice_test`、`study_guide`/`exam_prep`
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
- `learning_mode`: str - 可选，含义同 `gemini_chat`
- `image_paths`: list[str] - 可选图片路径
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 是否使用 Temporary chat

### gemini_start_chat

创建多轮会话。

**参数：**
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - 创建会话后默认沿用的 `standard` / `extended`
- `learning_mode`: str - 可选，创建会话后默认沿用的学习模式
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 后续会话消息默认沿用的 Temporary chat 状态

**返回：** 会话 ID，用于后续消息

### gemini_send_message

会话消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径
- `learning_mode`: str - 可选，覆盖会话默认学习模式
- `temporary`: bool - 可选，覆盖会话默认 Temporary chat 状态

### gemini_send_message_stream

会话流式消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径
- `learning_mode`: str - 可选，覆盖会话默认学习模式
- `temporary`: bool - 可选，覆盖会话默认 Temporary chat 状态

**学习模式：**

`learning_mode` 对齐 2026-06-19 Gemini Web `学习辅导` 输入 companion：

- `interactive_quiz` / `quiz`: 互动式测验
- `flashcards`: 互动式抽认卡
- `practice_test`: 模拟测试
- `study_guide` / `exam_prep`: 备考/学习指南

这些模式会写入 Web 前端使用的 `X9b` / `GOa` 请求字段，并按网页行为给 prompt 加上对应学习前缀。它不能和 Deep Research 等独立工作流混用。

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

分页列出 Gemini Web 历史聊天元数据。

**参数：**
- `limit`: int - 单页数量，最大 50
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

### gemini_read_chat

读取指定 Gemini Web 历史对话内容。这个工具会返回私人聊天文本，应只在用户明确需要时调用。

**参数：**
- `chat_id`: str - `gemini_list_chats` 返回的聊天 ID
- `limit`: int - 最多返回的 turn 数量，最大 100
- `response_format`: "markdown" | "json"
- `max_chars_per_turn`: int - 单条 turn 最大字符数

### gemini_search_chats

分页搜索 Gemini Web 历史对话。默认只匹配标题和 ID，不读取聊天正文；只有传入
`scan_turns=true` 时才会读取当前页聊天内容并返回截断后的匹配片段。

**参数：**
- `query`: str - 搜索关键词
- `limit`: int - 本次最多扫描的聊天数量，最大 50
- `offset`: int - 分页偏移
- `scan_turns`: bool - 是否读取正文匹配，默认 false
- `turns_per_chat`: int - 正文搜索时每个聊天最多读取的 turn 数，最大 50
- `max_chars_per_turn`: int - 匹配片段最大字符数
- `response_format`: "markdown" | "json"

### gemini_export_chat

将单个 Gemini Web 历史对话导出为 Markdown 或 JSON。这个工具会返回私人聊天文本，
应只在用户明确需要导出某个 `chat_id` 时调用。

**参数：**
- `chat_id`: str - `gemini_list_chats` 或 `gemini_search_chats` 返回的聊天 ID
- `response_format`: "markdown" | "json"
- `limit`: int - 最多导出的 turn 数，最大 200
- `max_chars_per_turn`: int - 单条 turn 最大字符数，最大 20000
- `include_metadata`: bool - 是否附带标题、时间等元数据

### gemini_get_web_capabilities

返回 2026-06-18 在 Pro 账号网页中实测到的模型、思考等级、上传/工具菜单、
设置菜单、已覆盖 MCP 工具和可探测 RPC 清单。

**参数：**
- `response_format`: "markdown" | "json"

这个工具是只读能力清单，不会访问私人聊天内容。实时 RPC 可达性请配合
`gemini_probe_web_features` 使用。

### gemini_list_scheduled_actions

只读列出 Gemini Web “定时操作”页面返回的任务条目。

**参数：**
- `scope`: "active" | "inactive" | "all"
- `limit`: int - 每类最多返回数量，最大 100
- `offset`: int - 每类分页偏移
- `response_format`: "markdown" | "json"
- `max_chars_per_field`: int - 单个文本字段截断长度

这个工具不会创建、修改或删除定时任务。定时任务标题和时间可能属于账号私人内容，
应只在用户需要查看定时任务时调用。

### gemini_get_tool_mode_status

读取 Gemini Web 工具/模式状态枚举。该 RPC 在 Canvas / 学习辅导等工具模式
切换时会出现，返回 `mode_id`、`available`、`quota_value`、`used_value`、
`state` 等字段。

**参数：**
- `limit`: int - 最大返回数量
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

`mode_id` 是 Gemini Web 内部数字枚举，可能随网页版本漂移；这个工具只做
只读状态读取，不创建或发送任何工具模式请求。

### gemini_delete_chat

删除指定 Gemini Web 历史对话。

**参数：**
- `chat_id`: str - 要删除的聊天 ID

### gemini_inspect_account

检查当前账号可用能力和 Web RPC 状态，并隐藏原始 RPC 预览。

**参数：**
- `response_format`: "markdown" | "json"

### gemini_get_tool_manifest

返回面向 agent 的工具清单，包含每个工具的能力说明、隐私等级、是否 destructive、
是否支持分页、可用分组、当前 `GEMINI_TOOLS` 下是否已启用，以及推荐工作流。
这个工具是静态只读入口，不访问账号内容。

**参数：**
- `scope`: "all" | "core" | "history" | "account" | "media" | "files" | "research" | "gems" | "cookie" | "prompts"
- `response_format`: "markdown" | "json"

### gemini_probe_web_features

探测 2026-06-18 Pro 网页面观察到的新版 Gemini Web 入口背后的只读 RPC 是否可达。
该工具只返回 HTTP/RPC 状态、`reject_code` 和入口元数据，不返回原始响应正文或账号内容。

**支持范围：**
- `library`: Library 页面相关 RPC
- `sharing`: 公开链接页面相关 RPC
- `usage`: 用量限额相关 RPC
- `personalization`: 个性化设置相关 RPC
- `import`: 记忆导入入口相关 RPC
- `all`: 以上全部

**参数：**
- `surface`: "all" | "library" | "sharing" | "usage" | "personalization" | "import"
- `response_format`: "markdown" | "json"

### gemini_list_public_links

列出 Gemini Web “你的公开链接”页面返回的公开链接条目。

**参数：**
- `limit`: int - 最大返回数量
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

### gemini_get_usage_limits

读取 Gemini Web “用量限额”页面返回的限额和模型状态结构。字段名仍按观测结构保守命名。

**参数：**
- `scope`: "quota" | "model_state" | "all"
- `response_format`: "markdown" | "json"

### gemini_list_library_capabilities

列出 Gemini Web Library 页面暴露的本地化能力/模板条目。当前不是 Library 资产列表。

**参数：**
- `limit`: int - 最大返回数量
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

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

## 低 token Skill 入口

`src.skill_server` 面向 skills 兼容客户端提供短工具名：

| Tool | Purpose |
|------|---------|
| `chat` | 对话，支持图片和 session |
| `create` | 生成图片、视频或音乐 |
| `edit` | 基于参考图片编辑 |
| `session` | 创建、发送、列出、重置本地多轮会话 |
| `history` | 远端 Gemini Web 历史对话 list/search/read/export/delete |
| `account` | 账号状态、工具清单、可用模型、功能探测、公开链接、用量和 Library 能力 |
| `prompts` | 本地提示词库 |
| `cookie` | Cookie 状态和浏览器获取 |

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
