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
- `gemini_list_research_report_actions`
- `gemini_create_from_research_report`
- `gemini_get_tool_manifest`
- `gemini_doctor`
- `gemini_get_cookie_status`
- `gemini_list_browser_cookie_profiles`
- `gemini_get_cookie_from_browser`
- `gemini_reset`

### 可选启用

`GEMINI_TOOLS=all` 额外增加：

- `gemini_history`
- `gemini_account_inventory`
- `gemini_notebooks`
- `gemini_cleanup_test_artifacts`
- `gemini_list_chats`
- `gemini_scan_chat_history_sources`
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
- `gemini_list_notebooks`
- `gemini_list_notebook_chats`
- `gemini_move_chat_to_notebook`
- `gemini_list_scheduled_actions`
- `gemini_get_scheduled_action`
- `gemini_create_scheduled_action`
- `gemini_delete_scheduled_action`
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

### gemini_list_research_report_actions

读取已完成 Deep Research 聊天中的沉浸式报告，并列出 MCP 侧支持的“从报告创建”动作。
当前 raw `READ_CHAT` payload 能稳定暴露报告、来源和沉浸式报告 ID，但未观测到网页下拉菜单的稳定 mutation RPC。

**关键参数：**
- `chat_id`: str - Deep Research 所在的 Gemini Web chat ID
- `response_format`: `"markdown"` | `"json"`

### gemini_create_from_research_report

从已完成 Deep Research 报告创建本地产物。2026-06-21 网页实测的原生“创建”菜单项为
`webpage`、`infographic`、`quiz`、`flashcards`、`audio_overview`，并带有一个
自定义应用描述入口 `custom_app`。当前 MCP 工具生成本地等价产物，不直接调用未稳定观测到的
Gemini 私有 mutation RPC。

**关键参数：**
- `chat_id`: str - Deep Research 所在的 Gemini Web chat ID
- `artifact_type`: str - 要创建的产物类型
- `output_dir`: str - 本地输出目录，默认 `generated_media/research_artifacts`

---

## 账户和 Gems

### gemini_history

Gemini Web 历史对话只读聚合入口。推荐给 `GEMINI_TOOLS=history` 和
`GEMINI_TOOLS=history-organize` 使用，避免让普通 agent 同时看到过多颗粒工具。

**参数：**
- `action`: "list" | "scan" | "search" | "read" | "export"
- `chat_id`: str - `read` / `export` 时需要
- `query`: str - `search` 时需要
- `limit`: int - 单页数量
- `offset`: int - 分页偏移
- `scan_turns`: bool - `search` 时是否读取正文匹配，默认 false
- `response_format`: "markdown" | "json"

`action=scan` 会枚举已观测历史来源和 notebook 对话；`action=read/export`
会返回私人聊天文本，应只在用户明确指定目标后调用。

### gemini_list_chats

分页列出 Gemini Web 历史聊天元数据。

**参数：**
- `limit`: int - 单页数量，最大 50
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

### gemini_scan_chat_history_sources

深度枚举 Gemini Web 历史对话元数据来源。该工具会合并已观测的
`ListConversations` 过滤器、原生 notebook 内对话列表，以及 Remy goals 中携带的
`conversationId` 引用；不会读取 turn 正文，也不会移动或删除聊天。

**参数：**
- `limit`: int - 单页数量，最大 500
- `offset`: int - 合并结果分页偏移
- `max_items_per_source`: int - 每个来源最多抓取数量，最大 10000
- `page_size`: int - RPC page size，最大 100
- `max_pages_per_source`: int - 每个来源最多请求页数，最大 200
- `include_notebook_chats`: bool - 是否合并原生 notebook 内对话，默认 true
- `include_remy_goals`: bool - 是否合并 Remy goal conversation 引用，默认 true
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

### gemini_cleanup_test_artifacts

查找并可选删除匹配显式 marker 的测试聊天和测试定时任务。默认 `dry_run=true`，
先返回将被删除的 ID；只有传入 `dry_run=false` 才执行删除。

**参数：**
- `markers`: str - 逗号分隔的 marker，默认 `codex-,Cleanup Verification Marker`
- `target`: "all" | "chats" | "scheduled"
- `dry_run`: bool - 是否只预览，默认 true
- `max_chats`: int - 最多扫描最近多少个聊天，最大 100
- `scan_turns`: bool - 是否读取正文查找 marker，默认 false
- `response_format`: "markdown" | "json"

### gemini_get_web_capabilities

返回 2026-06-18 在 Pro 账号网页中实测到的模型、思考等级、上传/工具菜单、
设置菜单、已覆盖 MCP 工具和可探测 RPC 清单。

**参数：**
- `response_format`: "markdown" | "json"

这个工具是只读能力清单，不会访问私人聊天内容。实时 RPC 可达性请配合
`gemini_probe_web_features` 使用。

### gemini_account_inventory

账号和 Gemini Web surface 的只读聚合入口。推荐给 `GEMINI_TOOLS=account-read`
使用，避免把 links、usage、library、scheduled 等盘点工具全部暴露给普通 agent。

**参数：**
- `surface`: "capabilities" | "status" | "features" | "links" | "usage" | "library" | "notebooks" | "notebook_chats" | "scheduled" | "modes" | "models"
- `feature_surface`: str - `surface=features` 时的探测范围
- `usage_scope`: "quota" | "model_state" | "all"
- `scheduled_scope`: "active" | "inactive" | "all"
- `notebook_action`: "list" | "chats"
- `notebook_id` / `notebook_title`: notebook 对话读取时使用
- `limit`: int - 单页数量
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

### gemini_notebooks

Gemini Web 原生笔记本只读聚合入口。推荐给 `history-organize` 使用，和
`gemini_move_chat_to_notebook` 搭配完成“先找笔记本、再移动、再校验”的流程。

**参数：**
- `action`: "list" | "chats"
- `notebook_id`: str - `action=chats` 时可用
- `notebook_title`: str - 可选，按标题查找笔记本
- `limit`: int - 单页数量，最大 100
- `offset`: int - 分页偏移
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_list_notebooks

列出 Gemini Web 原生笔记本。返回的是 Gemini 侧边栏/移动对话弹窗中的 native Notebooks，
不是外部 NotebookLM。

**参数：**
- `limit`: int - 单页数量，最大 100
- `offset`: int - 分页偏移
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_list_notebook_chats

列出某个 Gemini 原生笔记本内的最近对话元数据。

**参数：**
- `notebook_id`: str - `gemini_list_notebooks` 返回的笔记本 ID
- `notebook_title`: str - 可选，按标题查找笔记本
- `limit`: int - 单页数量，最大 100
- `offset`: int - 分页偏移
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_move_chat_to_notebook

把已有 Gemini Web 对话移动到 Gemini 原生笔记本。该工具会修改远端聊天元数据，
但不删除聊天；移动后会读取目标笔记本最近对话列表进行校验。

**参数：**
- `chat_id`: str - 要移动的 Gemini Web 聊天 ID
- `notebook_id`: str - 目标笔记本 ID
- `notebook_title`: str - 可选，按标题查找目标笔记本
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_list_scheduled_actions

只读列出 Gemini Web “定时操作”页面返回的任务条目。

**参数：**
- `scope`: "active" | "inactive" | "all"
- `limit`: int - 每类最多返回数量，最大 100
- `offset`: int - 每类分页偏移
- `response_format`: "markdown" | "json"
- `max_chars_per_field`: int - 单个文本字段截断长度

定时任务标题和时间可能属于账号私人内容，应只在用户需要查看定时任务时调用。
JSON 输出包含 `diagnostic`，当当前 cookie/session 返回空 registry 时会给出
账号上下文提示，方便区分“确实没有任务”和“浏览器多账号上下文不一致”。
从 Chrome 刷新 Cookie 时，服务器会隔离 gemini_webapi 的本地 cookie cache，并在
可用时优先选择能读到 scheduled registry 的 Chrome profile。

### gemini_get_scheduled_action

按 ID 只读获取单个 Gemini Web “定时操作”。适合在创建返回 ID 后做二次校验，或在
用户提供已知任务 ID 时查看详情。

**参数：**
- `action_id`: str - 定时操作 ID
- `response_format`: "markdown" | "json"
- `max_chars_per_field`: int - 单个文本字段截断长度

JSON 输出包含 `diagnostic.matched_task`、`item.task_state` 和 `item.is_deleted`。如果当前
cookie/session 无法按 ID 读取任务，工具会返回 `ok=false` 和账号/profile 上下文提示。

### gemini_create_scheduled_action

创建 Gemini Web “定时操作”。当前只开放已实测稳定的每日计划：每天在指定小时触发。

**参数：**
- `title`: str - 定时操作名称
- `instructions`: str - 定时执行时发送给 Gemini 的指令
- `hour`: int - 0 到 23，默认 9
- `timezone_name`: str - 默认 `Asia/Shanghai`
- `locale`: str - 默认 `zh-CN`
- `response_format`: "markdown" | "json"

这个工具会修改 Gemini Web 账号状态。只有在用户明确要求创建定时任务时调用。
JSON 输出包含 `visible_in_registry`、`readable_by_id_after_create` 和
`verification_status`。如果创建 RPC 返回了 ID，但随后列表校验没有看到该 ID，调用方应再用
`gemini_get_scheduled_action` 按 ID 校验，并提示用户检查当前 Gemini cookie/session 是否与浏览器
可见账号一致。

### gemini_delete_scheduled_action

按 ID 删除 Gemini Web “定时操作”。删除定时操作不会删除它已经产生的历史对话。

**参数：**
- `action_id`: str - `gemini_list_scheduled_actions` 或创建结果返回的定时操作 ID
- `response_format`: "markdown" | "json"

JSON 输出包含 `verification_status`、`visible_after_delete`、`readable_by_id_after_delete`
和 `deleted_by_id_after_delete`。Gemini 的 `GetTask` 在删除后可能仍返回 tombstone 对象；
只有按 ID 读到 `task_state=deleted` 时，工具才把删除标记为已校验。
这个工具是 destructive 远端操作。只删除用户明确指定或当前验证流程刚创建的任务。

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
- `scope`: "all" | "core" | "history" | "account" | "notebooks" | "scheduled" | "media" | "files" | "research" | "gems" | "cookie" | "prompts"
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

### gemini_doctor

只读预检 Gemini Web MCP 运行状态，不输出 Cookie 原值。默认只做本地和静态检查；
`validate_browser=true` 时会验证浏览器 profile 的账号状态和 scheduled registry 计数。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")
- `validate_browser`: bool - 是否执行账号/profile 远端验证 (默认: false)
- `response_format`: "markdown" | "json"

### gemini_get_cookie_status

查看 Cookie 状态。

**参数：** 无

### gemini_list_browser_cookie_profiles

列出本地浏览器 Cookie profile 诊断信息，不返回任何 Cookie 原值。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")
- `validate`: bool - 是否初始化 Gemini 客户端验证账号与 scheduled registry (默认: true)
- `response_format`: "markdown" | "json"

当定时任务 create 返回 ID 但 list 的 registry 为空时，先调用这个工具，检查
`chrome_selected_profile`、`chrome_selected_profile_directory`、`scheduled_registry_count`
和 `account_available`，再用
`gemini_get_cookie_from_browser(profile="...")` 刷新当前运行时 Cookie。

### gemini_get_cookie_from_browser

从浏览器获取 Cookie。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")
- `profile`: str - Chrome profile 名称，可从 `gemini_list_browser_cookie_profiles` 获取

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
| `history` | 远端 Gemini Web 历史对话 list/search/read/export/delete 和测试产物清理 |
| `cleanup` | dry-run 或删除匹配显式 marker 的测试聊天/定时任务 |
| `account` | 账号状态、工具清单、可用模型、功能探测、公开链接、用量、Library 能力和定时操作只读列表 |
| `scheduled` | 定时操作 list/get/create/delete，create 仅支持每日固定小时 |
| `prompts` | 本地提示词库 |
| `cookie` | Cookie 状态、浏览器 profile 诊断和浏览器获取 |
| `doctor` | 只读预检工具组、Cookie 状态、浏览器 profile 对齐和媒体校验依赖，不输出 Cookie 值 |

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
| 只调用 Gemini 模型 | `model` |
| 只读整理或导出历史对话 | `history` |
| 将历史对话整理进 native Notebook | `history-organize` |
| 只读盘点账号 Web surface | `account-read` |
| 通用内容工作流 | `core` |
| 明确授权后管理定时操作 | `scheduled-admin` |
| 完整维护/验证工具面 | `all` |
| 还需要本地提示词库 | `core,prompts` |

### 工具组配置

根据需要通过 `GEMINI_TOOLS` 环境变量配置加载的工具：

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
