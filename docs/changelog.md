# Changelog

Gemini MCP Server 版本更新历史记录。

---

## Unreleased

### Web UI 对齐
- 对齐 2026-05-22 观察到的 Gemini Web 模型面：`3.1 Flash-Lite`、`3.5 Flash`、`3.1 Pro`
- 复核 2026-06-18 Pro 账号网页面：工具菜单包含上传、Drive、导入代码、图片、视频、Canvas、Deep Research、音乐、学习辅导、个性化/Labs；设置菜单包含活动记录、记忆导入、用量限额、定时操作、公开链接等入口
- 将 `standard` / `extended` 固化为独立 `thinking_level` 选择
- 新增 `learning_mode`，对齐 2026-06-19 Web 前端学习辅导 Input Companion：
  互动测验、抽认卡、模拟测试、备考/学习指南会写入对应 `X9b` / `GOa` 请求字段
- 在媒体工具中显式写入网页实际后端规则：
  图像首轮固定为 Nano Banana 2，音乐按 `flash` / `pro` 分流到 Lyria 3 / Lyria 3 Pro

### 账号和聊天管理
- 新增 `gemini_inspect_account`，检查当前账号 Web RPC/能力状态并隐藏原始 RPC 预览
- 新增 `gemini_read_chat`，按 chat ID 读取历史对话 turns
- 新增 `gemini_search_chats`，分页搜索历史对话标题/ID，并可显式扫描当前页正文片段
- 新增 `gemini_export_chat`，将单个历史对话导出为 Markdown 或 JSON
- 新增 `gemini_delete_chat`，删除指定远端历史对话
- 新增 `gemini_get_tool_manifest`，为 agent 暴露工具安全、隐私、分页、可用分组、当前启用状态和推荐工作流元数据
- primary MCP 工具增加 MCP `ToolAnnotations`，标记只读、远端修改、本地修改和 destructive 操作
- 新增 `gemini_probe_web_features`，用浏览器实测到的只读 RPC 探测 Library、公开链接、用量、个性化、记忆导入等新版 Web 入口
- 新增 `gemini_get_web_capabilities`，返回 Pro 网页模型、思考等级、工具菜单、设置入口和 MCP 覆盖清单
- 新增 `gemini_list_public_links`、`gemini_get_usage_limits`、`gemini_list_library_capabilities`，把可稳定解析的新版 Web 入口从 probe 升级为只读工具
- 新增 `gemini_list_scheduled_actions`，列出定时操作页面返回的 active/inactive 任务条目
- 新增 `gemini_get_scheduled_action`，用前端确认的 `kwDCne` / GetTask RPC 按 ID 只读校验定时操作
- 新增 `gemini_create_scheduled_action` / `gemini_delete_scheduled_action`，支持每日定时操作的创建和按 ID 删除
- 新增 `gemini_get_tool_mode_status`，只读读取 Canvas / 学习辅导等工具模式附近出现的 Web 内部状态枚举
- 新增 `gemini_list_research_report_actions` / `gemini_create_from_research_report`，为 Deep Research 完成后的网页实测“创建”菜单提供 MCP 侧等价入口，可生成网页、信息图、测验、抽认卡、音频概览脚本和自定义应用规格；当前未观测到稳定原生网页菜单 mutation RPC
- `gemini_list_chats` 增加 `offset`、`response_format` 和分页元数据
- `gemini_list_public_links`、`gemini_list_library_capabilities`、`gemini_list_scheduled_actions`
  和 `gemini_get_tool_mode_status` 增加统一分页元数据，便于 agent 分页读取账号内容
- 忽略不可达的本地 `GEMINI_PROXY`，避免旧代理端口导致客户端初始化失败
- 从 Chrome 读取 Cookie 时验证多个本地 profile，隔离 gemini_webapi cookie cache，
  并优先选择能读取 scheduled registry 的 profile
- 新增 `gemini_list_browser_cookie_profiles`，用于列出 Chrome profile 的非敏感账号
  诊断；`gemini_get_cookie_from_browser` 支持 `profile` 参数，便于手动对齐多账号上下文
- `gemini_list_browser_cookie_profiles` 增加 `chrome_selected_profile` 诊断；定时操作
  create/delete 增加 `verification_status`、by-id 可读性和 `task_state` 校验，区分 RPC 已接受、registry 已验证和 deleted tombstone
- 低 token `src.skill_server` 增加 `history` 和 `scheduled`，其中 `scheduled` 支持 list/get/create/delete；并扩展 `history` 支持 search/export、`account` 支持 manifest/features/links/usage/library
- 新增项目内 Codex skill：`.codex/skills/gemini-web-mcp`，用于指导 agent 安全使用 manifest、聊天记录和验证流程

### 工具面收缩
- 新默认工具组改为 `core`
- `all` 现在聚焦高价值 AI 工作流，不再默认加载本地提示词工具
- 移除 `gemini_list_features`，减少低价值枚举型工具
- 当前默认工具面为 `core` 加始终可用的 manifest/cookie helpers；`all` 额外提供
  `gemini_inspect_account`、`gemini_list_chats`、`gemini_search_chats`、
  `gemini_read_chat`、`gemini_export_chat`、`gemini_delete_chat`、
  `gemini_get_web_capabilities`、`gemini_probe_web_features`、`gemini_list_public_links`、
  `gemini_get_usage_limits`、`gemini_list_library_capabilities`、
  `gemini_list_scheduled_actions`、`gemini_get_scheduled_action`、`gemini_create_scheduled_action`、
  `gemini_delete_scheduled_action`、`gemini_get_tool_mode_status`、
  `gemini_list_models`、`gemini_manage_gems`
- `gemini_manage_prompts` 保留为 `prompts` 可选分组，不属于默认工具面

### 文档与验证
- 补充 Gemini Web live UI 覆盖说明和媒体路由说明
- 新增 `evaluations/gemini_web_mcp_contract.xml`，提供 11 个只读、稳定答案的 MCP contract evaluation
- 扩展测试以校验媒体后端分流、学习模式请求注入、工具 annotations、evaluation XML、Codex skill 和默认工具面

---

## v2.0.0 (2026-05-05)

### ✨ 主要更新

#### 全新架构
- 完整重新设计的架构
- 清晰的模块分离（server, client_wrapper, constants）
- 工具模块化（chat, research, media, file, manage）

#### 支持最新模型
- `fast` → gemini-3-flash
- `thinking` → gemini-3-flash-thinking
- `pro` → gemini-3.1-pro

#### 媒体生成增强
- **图像**：Nano Banana 2（所有模型）
- **视频**：Veo 3.1（最长 60 秒，所有模型）
- **音乐**：Lyria 3 Clip（30秒）和 Lyria 3 Pro（完整）

#### Deep Research 支持
- 新增 `gemini_deep_research` 工具
- 深度研究与报告生成

#### 完整工具系统（历史记录）

以下为 v2.0.0 发布时的历史工具面；当前支持情况以
`docs/tools.md` 和 `docs/api-reference.md` 为准。

**对话工具：**
- `gemini_chat` - 单次对话（支持图片输入）
- `gemini_start_chat` - 创建会话
- `gemini_send_message` - 发送会话消息
- `gemini_list_sessions` - 活跃会话列表
- `gemini_reset_session` - 重置会话

**媒体工具：**
- `gemini_generate_media` - 通用媒体生成
- `gemini_generate_music` - 便捷音乐生成

**文件工具：**
- `gemini_upload_file` - 文件上传
- `gemini_analyze_url` - URL 分析

**管理工具：**
- `gemini_list_chats` - 历史聊天
- `gemini_manage_gems` - Gem 管理（CRUD）
- `gemini_list_models` - 模型列表
- `gemini_list_features` - 功能列表
- `gemini_health_check` - 健康检查
- `gemini_reset` - 重置客户端

#### 完整文档系统
- docs/README.md - 文档中心
- docs/quickstart.md - 快速开始
- docs/tools.md - 工具使用
- docs/models.md - 模型选择
- docs/configuration.md - 配置说明
- docs/faq.md - 常见问题
- docs/architecture.md - 技术架构
- docs/changelog.md - 更新历史

### 📦 技术改进
- 客户端封装（client_wrapper.py）
- 常量系统（constants.py）
- 工具注册架构
- 会话管理系统
- 更好的错误处理

### ⚙️ 配置更新
- 环境变量 `GEMINI_PSID`（必需）
- `GEMINI_PSIDTS`（推荐）
- `GEMINI_PROXY`（可选）
- `GEMINI_AUTO_REFRESH`（默认 true）

### 📚 依赖
- gemini-webapi >= 1.20.0
- mcp (FastMCP)
- Python >= 3.10

---

## v0.1.0 (初始版本)

### 最初版本
- 基础认证机制
- 简单对话功能
- 项目框架
