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

### 默认功能面（`gemini-mcp-function`）

`gemini-mcp-server` 未设置 `GEMINI_TOOLS` 时也使用此功能面：

- 协助：`gemini_ask`、`gemini_understand_image`、`gemini_understand`、`gemini_research`
- Gemini Task：`gemini_run_task`
- 会话：`gemini_start_chat`、`gemini_send_message`、`gemini_list_sessions`、`gemini_reset_session`
- 媒体：`gemini_generate_image`、`gemini_edit_image`、`gemini_generate_video`、`gemini_generate_music`
- 长任务：`gemini_get_operation_status`、`gemini_get_operation_result`
- 研究报告：`gemini_list_research_report_actions`、`gemini_create_from_research_report`

四个媒体专用工具始终后台启动，保存返回的 `operation_id` 并使用状态/结果工具取回同一任务。报告的 `audio_overview` 是旁白稿，`custom_app` 是应用规格，不冒充已生成音频或已部署应用。

`gemini_ask` 合并普通问答和联网问题，也可直接在提示中指定“去用某个 App”，默认使用 Flash，不提供联网或工具启用开关。返回观察到的 `sources`、`grounding_state`、来源数量和时间；普通文本中的链接不冒充联网证据。旧 `gemini_search` 在五工具 assist 兼容入口保留。App/Task 的实测范围与协议边界见 [Connected Apps](connected-apps.md)。

功能面 `gemini_ask` 另接受 `notebook_ids`、`timeout_seconds=600` 和 `operation_id`。提供 Notebook ID 时启动后台普通聊天，返回 `execution_mode="chat"` 与持久操作句柄；不带 Notebook 时保持直接问答。仅此后台分支接受调用方提供的 `operation_id`。完整回答先保存并校验本地产物，再按默认策略清理自有线程。

`gemini_run_task(prompt, model="flash", thinking_level="standard", notebook_ids=None, timeout_seconds=600, retain_chat=false, delete_after_seconds=None, operation_id=None)` 明确启动后台 Task，返回 `execution_mode="task"`。使用 `gemini_get_operation_status/result` 读取同一操作，不能通过重发提示查询进度。该工具可按用户提示执行应用修改，注解为 destructive；Task 完成不替代应用侧修改的独立核验。见 [Notebook 与 Task 工作流](notebook-task-workflows.md)。

功能面的 `gemini_generate_video` 也默认使用 `model="flash"`；显式指定的模型会原样保留。历史授权测试中 Pro 返回了经验证的视频文件，Flash 返回过普通文本拒绝；不自动切换到 Pro，视频可用性仍需按实际结果判断。

### 显式账号面（`gemini-mcp-account`）

`gemini_history`、`gemini_manage_chat`、`gemini_delete_chat`、`gemini_notebooks`、`gemini_manage_notebook`、`gemini_skills`、`gemini_move_chat_to_notebook`、`gemini_tasks`、`gemini_task_browser`、`gemini_account_inventory`、`gemini_list_scheduled_actions`、`gemini_get_scheduled_action`、`gemini_create_scheduled_action`、`gemini_delete_scheduled_action`、`gemini_manage_gems`、`gemini_cleanup`、`gemini_preferences`。

原生 `gemini_skills` 支持 list/get/create/update/enable/delete，指令正文读取须 `include_instructions=true`；`gemini_manage_notebook` 支持 create/get/list_sources/add_source/delete_source/delete，来源目前限纯文本。Notebook 的删除包含其中对话；列表分页、读回和覆盖边界见[原生账号资源](native-account-resources.md)。

账号面的历史查询默认 `response_format="compact"`，返回 ID、标题、置顶状态和分页信息；普通 read 每轮默认最多 800 字符。`gemini_manage_chat` 用准确 ID 执行 rename/pin/unpin，修改前后读取元数据核验；只发送成功但未读回验证时，`changed=null`。详见[紧凑对话管理](web-chat-management.md)。

`gemini_cleanup` 仅清理本项目登记的 MCP 自有聊天，不按标题匹配。Cookie、Doctor 和本地 Prompts 不属于两个业务面。账号数据和删除操作按用户明确意图使用。

### 显式兼容配置 (`GEMINI_TOOLS=core`)

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

## 结构化结果元数据（P0.3）

聊天、会话和客户端重置工具会继续返回原有 `TextContent.text`，同时在第一条内容的
`_meta.domain_result` 中提供稳定、机器可读的结果。旧客户端可继续读取正文；新客户端
不应再根据 emoji 或自然语言判断成功与失败。

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "The requested session does not exist.",
    "retryable": false,
    "suggested_action": "Create a session and use the returned ID.",
    "diagnostic_id": null
  },
  "warnings": [],
  "meta": {
    "request_id": "req_<uuid>",
    "operation_state": "failed",
    "observed_at": "2026-08-02T00:00:00+00:00",
    "requested_backend": null,
    "effective_backend": null,
    "verification_status": "local_state_absent",
    "diagnostic_id": null,
    "details": {}
  }
}
```

- `ok`：业务操作是否成功。
- `data`：工具相关的公开数据；不会包含客户端、Cookie、上游 session 对象或锁。
- `error.code`：稳定错误码。首批分类包括 `INVALID_ARGUMENT`、`AUTH_REQUIRED`、
  `AUTH_EXPIRED`、`SESSION_NOT_FOUND`、`CAPABILITY_UNAVAILABLE`、`UPSTREAM_REJECTED`、
  `UPSTREAM_CHANGED`、`NETWORK_ERROR`、`RATE_LIMITED`、`TIMED_OUT`、`CANCELLED`、
  `ARTIFACT_NOT_RETURNED`、`ARTIFACT_SAVE_FAILED`、`VERIFICATION_FAILED` 和 `INTERNAL_ERROR`。
- `error.retryable` / `error.suggested_action`：调用方是否应重试，以及建议的下一步。
- `warnings`：成功或部分成功时的非致命告警。
- `meta.operation_state`：`accepted`、`queued`、`running`、`completed`、`partial`、
  `timed_out`、`cancelled`、`failed` 或 `unavailable`。
- `meta.observed_at`：UTC ISO-8601 观测时间；backend 与 verification 字段记录请求、实际后端和验证状态。
- `meta.request_id`：每个结构化结果都有；`meta.diagnostic_id` 只在需要关联服务端异常日志时生成。

当前覆盖 primary 的 `gemini_chat*`、`gemini_start_chat`、`gemini_send_message*`、
`gemini_list_sessions`、`gemini_reset_session`、`gemini_reset`、媒体、文件/URL 分析和 research
report 本地产物；history 的 list/search/read/export/delete 也在 primary 与 compact 之间共享同一
`DomainResult`。`gemini_scan_chat_history_sources` 和其他未迁移管理工具仍以现有文本契约为准。

从 P0.4 起，primary 与 compact 的聊天、创建会话和会话发送都调用同一个应用服务。
工具名、参数和原有正文保持兼容；两个入口的差别（例如 compact 不向上游传 `gem` / `temporary`）
由适配器配置明确表达，不再维护两份聊天业务实现。

### 统一 artifact 元数据（P1.1）

媒体、文件/URL 和 research report 工具在保留原有正文的同时，把统一 artifact 数据放在
`_meta.domain_result.data`：

```json
{
  "state": "local",
  "artifacts": [
    {
      "id": "artifact_<stable-hash>",
      "kind": "image",
      "state": "local",
      "uri": "https://.../generated.png",
      "local_path": "/absolute/path/generated.png",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "width": 1024,
      "height": 1024,
      "duration_seconds": null,
      "requested_backend": "pro",
      "request_model": "gemini-3-flash",
      "effective_backend": "Nano Banana 2",
      "observed_backend": null,
      "verification": {
        "status": "verified",
        "methods": ["file_exists", "size_checked", "size_nonzero", "image_dimensions"]
      }
    }
  ],
  "input_artifacts": [],
  "requested_model": "pro",
  "request_model": "gemini-3-flash",
  "effective_backend": "Nano Banana 2",
  "observed_backend": null,
  "source_chat_id": "c_...",
  "media_type": "image"
}
```

- `state=remote`：响应含可用 URI，但尚未验证远端内容；artifact 的 verification 是 `unverified`。
- `state=local`：本地文件存在且非零；记录大小和可推断的 MIME，尺寸和时长在探针可用时填写。
- `state=queued`：上游明确返回 pending/processing/queued 等状态，操作结果仍为 `ok=true`、
  `operation_state=queued`。
- `state=empty`：请求完成但没有可用产物，返回 `ARTIFACT_NOT_RETURNED`，不把普通文本当作媒体成功。
- `state=failed`：保存或验证失败；完全失败使用 `ARTIFACT_SAVE_FAILED`/`VERIFICATION_FAILED`，
  远端 URI 仍可用但本地保存失败时使用 `operation_state=partial` 和 `ARTIFACT_SAVE_PARTIAL` 告警。

`artifacts` 是输出，`input_artifacts` 是本地文件、URL 或参考图。相同类型和 URI 的 artifact ID
在 primary 与 compact 表面一致。`response_format="json"` 的 research report 正文仍保持合法 JSON；
统一契约只放在 `TextContent._meta`，不会拼进 JSON 字符串。

---

## 对话工具

### gemini_chat

单次对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: str - `flash` / `argon` / `pro`，兼容别名或运行时模型名 (默认: `flash`)；`flash-lite` 仅在运行时确认当前账号基础 Flash 额度耗尽时可用，且不可选思考强度
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `learning_mode`: str - 可选；`interactive_quiz`/`quiz`、`flashcards`、`practice_test`、`study_guide`/`exam_prep`
- `image_paths`: list[str] - 可选图片路径
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 是否使用 Temporary chat
- `retain_chat`: bool - 是否保留远端聊天
- `delete_after_seconds`: int - 可选远端聊天清理时间

### gemini_chat_stream

兼容名称保持不变：工具会收集 Gemini Web 的上游流，归一化 delta、累计文本或混合片段，
再把正文作为一个 MCP 结果返回；它不声明 MCP 客户端会收到增量内容。

**参数：**
- `message`: str - 要发送的消息
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `learning_mode`: str - 可选，含义同 `gemini_chat`
- `image_paths`: list[str] - 可选图片路径
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 是否使用 Temporary chat

**结构化结果：** `_meta.domain_result.data.stream.delivery="collected"`，并提供
`chunk_semantics`、`chunk_count`、`emitted_piece_count`、`duplicate_chunk_count` 和
`text_length`。累计或重复片段不会让最终正文重复。

### gemini_start_chat

创建多轮会话。

**参数：**
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - 创建会话后默认沿用的 `standard` / `extended`
- `learning_mode`: str - 可选，创建会话后默认沿用的学习模式
- `gem_id`: str - 可选 Gem ID
- `temporary`: bool - 后续会话消息默认沿用的 Temporary chat 状态

**返回：** `sess_<uuid>` 形式的不透明本地会话 ID，用于后续消息。primary 与 compact 入口共享同一份会话状态；调用方不应自行构造或按顺序猜测 ID。

### gemini_send_message

会话消息。

未知 `session_id` 会明确返回 `SESSION_NOT_FOUND`，不会退化为单次对话或创建新会话。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径
- `learning_mode`: str - 可选，覆盖会话默认学习模式
- `temporary`: bool - 可选，覆盖会话默认 Temporary chat 状态
- `recover_only`: bool - 默认 false；true 时不提供 message 或图片，只核验同一活跃会话上一次未确认的回合

上一次回复未确认时，新的提示不会派发。使用原会话的 `recover_only=true` 读取原请求／回复，不能重新发送提示代替恢复；首次请求尚无远端标识时可能仍无法恢复。会话恢复依赖原服务器进程，不等于持久后台操作句柄。此参数不在 compact session 或 streaming 入口中提供。

### gemini_send_message_stream

收集现有会话的 Gemini Web 上游流并一次性返回 MCP 结果；归一化和结构化 `stream`
元数据与 `gemini_chat_stream` 相同。

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

只删除指定会话；未知 ID 返回 `SESSION_NOT_FOUND`，不会影响其他会话。会话未设置 `retain_chat=true` 时，还会尝试立即删除对应远端聊天。

---

## 媒体工具

### gemini_generate_media

通用媒体生成。默认 `background=true`：立即返回 `operation_id`，用 `gemini_get_operation_status` / `gemini_get_operation_result` 查询同一任务和取回 Artifact；`background=false` 保留等待结果的路径。

**参数：**
- `prompt`: str - 生成描述
- `media_type`: "image" | "video" | "music"
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `image_path`: str - 可选参考图片
- `background`: bool - 默认 `true`；`false` 等待本次结果
- `operation_id`: str - 可选调用方生成的操作句柄

**真实网页行为：**
- `image`: Flash-Lite 使用 `Nano Banana 2 Lite`；Flash / Pro 使用 `Nano Banana 2`
- `music`: 返回 Lyria 产物；当前 MCP 响应没有提供可验证的具体版本
- `video`: 使用专用视频模式；兼容的 `gemini_generate_media` 和 compact `create` 可显式传 `model="pro"` 使用本次实测路线，默认值保持不变。需要实际视频 Artifact 才算成功，单独缩略图不能证明视频生成完成
- `image + model=pro` 不会直接切换首轮图像后端；Pro redo 是网页生成后的二次操作

**artifact 行为：** 后台任务默认保存到操作数据库同级的 `artifacts/<operation_id>/` 持久目录，也可指定 `output_dir`；显式同步路径仍默认使用 `generated_media/`。实际写入的文件会检查存在性、
非零大小、MIME，以及可用的尺寸/时长。排队、空响应和保存失败有独立结构化状态。
`prompt` 不可为空；`filename` 只能是单个文件名，若目标文件已存在会另取不冲突的名字。
后台任务在生成、排队或结果无法核实时保护远端会话；本地产物验证并保存或明确拒绝后按原策略清理。`retain_chat=true` 可主动保留会话。重复读取结果会重新检查本地文件，缺失或被修改的文件不再标为已验证。

### gemini_generate_music

音乐生成（便捷工具）。默认后台启动，使用返回的 `operation_id` 查询状态和取回产物；`background=false` 等待结果。

**参数：**
- `prompt`: str - 音乐描述
- `model`: str - MCP 别名或运行时模型名 (默认: `flash`)
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)

媒体工具使用对应的 Gemini Web 生成模式触发图像、视频和音乐能力。
账号可用性、上游排队和响应形状仍由 Gemini Web 决定。

---

## 文件与 URL

### gemini_upload_file

上传本地文件并分析。代码文件也走这个本地文件路径；它不等同于
Gemini Web 的 Google Drive 选择器。

本地来源记录在 `input_artifacts`，并验证绝对路径、MIME 和大小；Gemini 响应中的图片记录为
`artifacts`。请求别名、实际模型与观测后端也随结果返回。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)

### gemini_analyze_url

让 Gemini 分析网页或视频 URL。

输入 URL 作为 `kind=webpage,state=remote` 的 `input_artifacts` 返回；这只证明调用方提供了该 URI，
不表示 MCP 已自行抓取并验证页面内容。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)

---

## Deep Research

研究默认 `retain_chat=false`。后台观察原请求，报告明确就绪且正文非空后，先保存和校验完整 Markdown，再释放自动清理保护；无需等待调用者取结果。显式 `retain_chat=true` 才保留远端线程。默认删除后，可通过原操作句柄重哈希并读取本地报告。

### gemini_get_operation_status / gemini_get_operation_result

以显式 `operation_id` 查询或取回后台研究、图片、视频、音乐、Task 或 Notebook 普通聊天。媒体任务完成后结果包含经验证的 `artifacts`；研究任务完成后返回报告。Task／Notebook Chat 的 worker 在原回复完成后自动保存并校验完整回答；Task 还须核验完整原生文件清单及支持的文件卡附件，再清理自有线程。附件失败或旧缓存缺少清单时返回部分结果，保留可用回答，不伪造空清单。结果读取可以在清理后或进程重启后校验已保存文件，不依赖调用者先取结果。状态只读元数据，不生成或读取完整回答。
两个工具由主服务的 `research` / `core` 组提供，并共用本地 SQLite 元数据，因此可跨服务重启恢复。
报告结果优先读取原账号、原 CID／RID／candidate 对应的已保存产物；没有缓存时只读取该句柄对应的会话，不重新启动研究。只有 ready 且正文非空的研究文档才算完成。正文预览限制 4000 字符，完整报告在已验证的本地 Artifact 中；缓存缺失、被改动或旧记录已清理但未保存文件时返回 partial。status 仅检查元数据／进度，不下载或清理报告。
报告和 Cookie 不写入任务数据库，句柄记录保留七天。只有最新研究轮次的明确运行证据才报告 `running`；没有此证据或报告时不推断运行或完成。
为处理首次调用返回前断连，应在调用 `gemini_research` 前生成 `op_` 加 32 位随机小写十六进制字符，
作为 `operation_id` 传入；重复该句柄不会重发原查询，也忽略新传入的查询与保留设置。若计划结果未确认且能读回原计划，可使用保存的原模型和思考等级原子认领并确认一次；已认领的确认不会重复发送，其余情况返回已有状态。
默认服务端生成句柄仍可使用，但首次响应丢失时调用方可能无法定位该记录。
若计划请求在观察并保存上游会话 ID 前中断，句柄只能防止重复启动，不能恢复远端计划。
防重仅在同一数据库保留该记录期间有效；七天到期清理后不要重用旧句柄。
读取只检查有界的最近会话窗口；报告不在窗口中或读回无法确认时不会宣称完成。 已发出的计划请求若出现无计划或恢复超时，但已观察到 chat ID，结果为 `partial`、状态未确认；保留其清理保护，使用同一 operation handle 后续查询，避免重复启动。
当前不支持取消；遇到超时或未确认状态，继续使用同一 operation handle 查询，不要重发生成请求。

### gemini_deep_research

默认后台创建研究计划并立即返回 `operation_id`；用操作状态/结果工具查询同一任务。`background=false` 保留等待路径。

**关键参数：**
- `model`: str - MCP 别名或运行时模型名
- `thinking_level`: str - `standard` / `extended` (默认: `standard`)
- `timeout_seconds`: int - 等待最终报告的时间上限（默认: `600`）
- `poll_interval_seconds`: int - 轮询间隔，最小 3 秒
- `background`: bool - 默认 `true`；`false` 使用同步等待路径
- `wait_for_completion`: bool - `true` 明确等待最终报告，覆盖默认后台模式；传入 `operation_id` 时应使用后台查询流程

**结构化状态：** 第一条正文的 `_meta.domain_result` 会明确给出 `queued`、`running`、
`completed` 或 `timed_out`。`data.upstream_operation_id` 和 `data.upstream_chat_id` 在上游
已提供时会保留；因此超时不等于上游任务失败，`continuation_possible=true` 时可稍后用
chat ID 读取报告。只返回计划且未观察运行证据时不会推断为已运行或完成。

### gemini_list_research_report_actions

读取已完成 Deep Research 聊天中的沉浸式报告，并列出 MCP 侧支持的“从报告创建”动作。
读取 SDK 明确就绪且非空的研究文档及结构化来源；普通聊天正文或运行中的空文档不能作为报告。

**关键参数：**
- `chat_id`: str - 仍可访问的 Deep Research 聊天 ID；与 operation_id 二选一
- `operation_id`: str - 原后台操作句柄；只读取其已验证本地报告，无远端认证、生成或清理
- `response_format`: `"markdown"` | `"json"`

### gemini_create_from_research_report

从已完成 Deep Research 报告创建本地产物。2026-06-21 网页实测的原生“创建”菜单项为
`webpage`、`infographic`、`quiz`、`flashcards`、`audio_overview`，并带有一个
自定义应用描述入口 `custom_app`。当前 MCP 工具生成本地文件：网页和信息图为 HTML，测验、
抽认卡为 Markdown，`audio_overview` 为 Markdown 旁白稿，`custom_app` 为 Markdown 应用规格。
它们由本地渲染器创建，后两项分别是用于制作音频与应用的文档。

写入完成后会重新检查文件存在、非零大小和 MIME；Markdown 模式附带可读 artifact 块，JSON 模式
保持原 payload 不变并通过 `_meta.domain_result` 返回同一份类型化 artifact。写入异常使用
`ARTIFACT_SAVE_FAILED` 和 diagnostic ID，不会把原始文件系统错误放入稳定错误消息。

**关键参数：**
- `chat_id`: str - 仍可访问的 Deep Research 聊天 ID；与 operation_id 二选一
- `operation_id`: str - 原后台操作句柄；报告已清理远端线程时使用本地缓存
- `artifact_type`: str - 要创建的产物类型
- `output_dir`: str - 本地输出目录，默认 `generated_media/research_artifacts`

例如先使用 `gemini_get_operation_result(operation_id=<原句柄>)` 检查完整报告已保存，再调用 `gemini_create_from_research_report(operation_id=<同一句柄>, artifact_type="webpage")`。目标参数同时提供或都缺失时，在初始化前返回 `INVALID_ARGUMENT`；本地报告未保存或校验失败时不回退到新生成。

---

## 账户和 Gems

管理类工具的 RPC ID、source path 和 payload builder 统一来自
`src/infrastructure/rpc_contracts.py`；handler 不再内嵌私有 RPC 常量。history、account、Notebook、
scheduled、Gem、manifest 和 doctor 分别由 `src/services/` 中的模块承载，primary 与 compact
只保留参数/文本适配。上游 body parser 会显式区分 `success`、`empty`、`rejected` 和
`changed_shape`，避免把空响应或网页结构变化误报为正常结果。history 的
list/search/read/export/delete 已迁入同一个 service；其他管理域仍按 bounded slice 逐步迁移，
不能把 adapter 文本相似当成语义共享。

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

`action=list/search/read/export` 的第一条内容带 `_meta.domain_result`。`scan` 目前仍是 primary
专用的深度元数据聚合结果，尚未迁入该 typed history service。

### gemini_list_chats

分页列出 Gemini Web 历史聊天元数据。

**参数：**
- `limit`: int - 单页数量，最大 50
- `offset`: int - 分页偏移
- `response_format`: "markdown" | "json"

结构化结果的 `count` / `offset` / `limit` 描述本次扫描的来源页，`match_count` 描述该页命中数。
当前分页在过滤前应用，不是对全局匹配结果分页；`scan_turns=true` 也只读取当前来源页。

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

### gemini_delete_chat

请求删除一个明确选定的 Gemini Web 历史对话。该操作不可逆，必须有显式用户确认。

**参数：**
- `chat_id`: str - 要删除的聊天 ID

调用返回后会在客户端支持底层只读 RPC 时，完整分页刷新 canonical recent/pinned 历史元数据并检查目标 ID。
`read_chat()` 返回 `None` 也可能表示读取失败或内容仍在生成，不能单独作为删除证据：

- `verification.status=verified_absent` / `deleted=true`：回读确认聊天不存在；
- `verification.status=not_available` / `deleted=null`：删除请求已返回，但客户端无法完成新鲜回读或分页达到安全上限；
- `verification.status=still_present`：聊天仍可读取，结果以 `VERIFICATION_FAILED` 失败关闭；
- `verification.status=read_back_error`：回读出错，结果以 `VERIFICATION_FAILED` 失败关闭。

不要把 `operation_state=accepted` 或兼容正文中的“已请求”改写成“已删除”。

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

Gemini 自动生成的聊天标题可能不保留 prompt 中的 marker。创建测试聊天时应立即保存返回的完整远端
chat ID，并优先用 `gemini_delete_chat(chat_id=...)` 删除到 `verified_absent`。默认 metadata-only marker
cleanup 只匹配标题/ID，可能对这类聊天返回 0；`scan_turns=true` 会读取最近聊天正文，只能在用户明确授权
正文扫描时作为兜底。

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

NotebookLM／Gemini Notebook 的只读入口。账号面默认 `source="notebooklm"`，返回可供 `gemini_ask` 或 `gemini_run_task` 的 `notebook_ids` 使用的准确 ID、名称、来源数量及分页；该目录不读取来源正文。`source="gemini"` 保留同一产品的项目 list/chats 视图，兼容 profile 的默认值仍为此视图。

两种视图可有重叠，不能凭资源前缀自行转换。移动聊天前在项目视图中精确解析目标，只有来源选择器记录不足以证明旧 move 合同可用于它。

**参数：**

- `action`: "list" | "chats"
- `source`: "notebooklm" | "gemini" - `notebooklm` 当前只支持 list
- `query`: str - `notebooklm` 目录的名称过滤
- `notebook_id`: str - `action=chats` 时可用
- `notebook_title`: str - 可选，按标题查找笔记本
- `limit`: int - 单页数量，最大 100
- `offset`: int - 分页偏移
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_list_notebooks

兼容工具：列出 Notebook 在 Gemini 的项目视图，与 `gemini_notebooks(source="gemini")` 相同。它与默认 Notebook 来源目录属于同一集成产品，使用的协议投影不同。

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

把已有 Gemini Web 对话移动到已在当前账号项目视图中解析的 Notebook。该工具会修改远端聊天元数据，
但不删除聊天；移动后会读取目标笔记本最近对话列表进行校验。
JSON 输出中的 `accepted=true` 仅表示 RPC 返回了可解析的确认；只有在目标笔记本
读回该聊天时才返回 `ok=true`、`verification_status="verified"`。

**参数：**
- `chat_id`: str - 要移动的 Gemini Web 聊天 ID
- `notebook_id`: str - 目标笔记本 ID
- `notebook_title`: str - 可选，按标题查找目标笔记本
- `locale`: str - 本地化语言，默认 `zh-CN`
- `response_format`: "markdown" | "json"

### gemini_tasks

账号面提供 `action="list"|"get"|"delete"|"progress"` 和 Task 计划管理操作。`list/get` 只读紧凑元数据，不读取完整回答；`delete` 要求明确删除意图与准确 `c_` ID，并独立核验不存在。聚合工具的整体注解为 destructive。已发送但未核验的删除不可盲目重复。

- `chat_id`: str - get/delete 的准确 Task 聊天 ID
- `limit`: int - 列表页大小，默认 20
- `page_token`: str - 继续读取返回的下一页
- `max_chars`: int - get 描述的字符窗口，默认 800

发起 Task 使用功能面的 `gemini_run_task`，完整回答通过其 `operation_id` 读取。Task 计划通过 `list_schedules`、`get_schedule`、`create_schedule`、`update_schedule`、`pause_schedule`、`resume_schedule`、`delete_schedule` 管理；下面的聊天定时工具不管理 Task 计划。进度仅返回有限活动名称、工具名和计数，不推断步骤成功。浏览器查看和输入见下文。

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
如果 mutation 响应没有可解析 body，`verification_status="rpc_unconfirmed"`；调用方不能把它当作
已删除。
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

列出、创建、更新或删除用户自建 Gems。列表使用严格的 custom 目录，不混入 SDK 预置 Gems，也不返回指令正文。

create/update/delete 都会再次读取 Gem 列表。返回文本包含 `读回校验`；可能值包括 `verified`、
`verified_deleted`、`read_back_not_observed`、`read_back_mismatch`、`still_present` 和
`read_back_error`。mutation 方法返回不再单独作为“已验证成功”的证据。custom 响应缺失、被拒绝或结构变化时，不能以 SDK 仍返回预置项为由声称列表为空或目标已删除。写入使用同账号单次派发；不确定结果只读回核验，不由 SDK 自动重发。

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

macOS Keychain 读取由 `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` 限制，默认 15 秒。超时会返回
`BROWSER_COOKIE_ACCESS_TIMEOUT`，不会无限阻塞工具，也不会在错误中包含 Cookie 值。

当定时任务 create 返回 ID 但 list 的 registry 为空时，先调用这个工具，检查
`chrome_selected_profile`、`chrome_selected_profile_directory`、`scheduled_registry_count`
和 `account_available`，再用
`gemini_get_cookie_from_browser(profile="...")` 刷新当前运行时 Cookie。

### gemini_get_cookie_from_browser

从浏览器获取 Cookie。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")
- `profile`: str - Chrome profile 名称，可从 `gemini_list_browser_cookie_profiles` 获取

该路径使用同一 macOS Keychain 超时。失败只表示当前进程未加载浏览器 Cookie；不要据此宣称账号无效。

---

## 管理工具

### gemini_reset

重置客户端。

**参数：** 无

---

## 低 token Skill 入口

`src.skill_server` 面向 skills 兼容客户端提供短工具名；默认注册前四项，`GEMINI_SKILL_MODE=full` 才注册其余四项：

| Tool | Purpose |
|------|---------|
| `chat` | 对话，支持图片和 session |
| `create` | 生成图片、视频或音乐 |
| `edit` | 基于参考图片编辑 |
| `session` | 创建、发送、列出、重置本地多轮会话 |
| `history` | 远端 Gemini Web 历史对话 list/search/read/export/delete 和测试产物清理 |
| `account` | 账号状态、工具清单、可用模型、功能探测、公开链接、用量、Library 能力和定时操作只读列表 |
| `scheduled` | 定时操作 list/get/create/delete，create 仅支持每日固定小时 |
| `prompts` | 本地提示词库 |

compact `create` / `edit` 默认将产物保存到本地；可传 `output_dir` 和单个文件名 `filename`。视频预览图不能替代视频文件。账号状态若标记 `cached_unverified`，只能表示 SDK 缓存，不能证明当前认证状态。

compact `session` 支持 `create` / `send` / `list` / `reset`（或 `reset_one`）/ `reset_all`。`reset` 与 `reset_one` 都必须提供 `session_id`，且只删除该会话；只有显式 `reset_all` 才会清空全部会话并重置客户端。旧的 `action="reset"` 保留为单会话别名，不再把缺少 ID 解释为全量重置。

compact 的 history/account/scheduled 直接导入共享 service 和 RPC parser；加载
`src.skill_server` 不再初始化 4k 行的 `src.tools.manage` 兼容适配器。

---

## 专注 assistance 入口

`src.surfaces.assist`（`gemini-mcp-assist` / `gemini_assist_mcp`）面向 assistance 意图提供恰好五个工具，不暴露 history、Cookie、Scheduled、Gem、Prompt 或 cleanup 工具：

| Tool | Purpose |
|------|---------|
| `gemini_ask` | 自然语言提问、联网问题和可用 App 请求，默认 Flash，可附加上下文并保留观测来源 |
| `gemini_search` | 带观测来源的当前网页搜索；`grounding_state` 如实报告 grounded / answer_only / unavailable / failed |
| `gemini_understand_image` | 理解单张本地图片或 http(s) 图片 URI |
| `gemini_understand` | 文本、图片、文件、URL 的类型化混合输入理解（最多 16 个输入，逐输入记录结果） |
| `gemini_research` | 异步启动一次 Deep Research 并返回保留的 operation handle 与上游 ID |

assist 的 `gemini_research(action="start")` 默认返回后台 operation handle；以 `action="status"` 或 `action="result"` 配合同一 `operation_id` 查询，查询时可省略 `query`。compact 使用 `session(action="operation_status" | "operation_result", session_id=operation_id)`；保持原有四工具目录。

`gemini_search` 与 `gemini_understand` 返回信息而不是 Artifact；Deep Research 报告通过保存的句柄取回，默认在报告提取后清理远端聊天，只有显式保留才支持再次远端读取。对应 Runtime Skill 为 `gemini-assist`。

搜索结果的来源 `role` 为 `citation` 或 `source_attribution` 时，有上游引用或经严格正文片段与 offset 匹配的关联证据；`retrieved_only` 表示观察到来源，但尚未确认它支持回答，因此不会使结果变为 `grounded`。这些标记不保证来源内容或回答本身正确。

---

## 🛡️ 智能错误处理

智能错误处理会在遇到问题时自动提供解决方案。

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

Task confirmation cards return `input_required`; an explicit result read returns their `input_id` and bounded prompt. Reply using `gemini_run_task` with the original `operation_id`, that `input_id`, selected `input_option_id` and exact user-authorized prompt from `input_options`. Each input token is dispatched once; original model and retention remain in force. Cards are never saved as final answer artifacts or automatically approved.

App connection setup is separate: `needs_attention`, `setup_required` and `setup_kind` identify a native setup requirement. Open the original Task to connect or decline; text confirmation cannot grant OAuth. A previously observed card in metadata is not fresh verification of its current state.

### gemini_task_browser

账号工具默认读取已知 Task 浏览器的截图，并返回经校验的 PNG Artifact；不超过 2 MiB 且摘要匹配时附原生图片。`response_id` 与 `candidate_id` 要同时提供，省略时读取最新回合。明确输入须提供 `browser_input`（`kind=click|scroll|key|type`）、原截图两个 ID、`expected_frame_sha256`、至少 20 字符的唯一 `action_id`；坐标对应当前帧。只接受 Task 状态 3 或 5 且发送前复核未变。同 ID 不重发；不自动批准权限、登录或新 App 授权。状态检查不证明独占暂停，帧也不证明网站操作成功。需要 `[browser-control]` 或 `[all]` 依赖。
