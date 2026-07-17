# Changelog

Gemini MCP Server 版本更新历史记录。

---

## v2.2.0 (2026-07-17)

### Skill 最佳实践对齐
- 对齐 agentskills.io 规范：`mcp-builder` 的 `reference/` 目录重命名为 `references/`（规约规定的复数形式），SKILL.md 内 9 处链接同步更新
- `gemini-web-mcp` SKILL.md 移除不可移植的硬编码路径 `/Users/jack/...`，改用规约标准的 `skills-ref validate` 校验命令
- `python-mcp-server-generator` 的 `description` 补充"何时使用"触发词（对齐规约反面示例要求）；新增 `license` 与 `compatibility` 字段
- `gemini-web-mcp` 新增 `compatibility` 字段（Python 3.10+ / .venv / Chrome cookies / 启动命令）
- 新增 `gemini-web-mcp/references/tool_surface.md`：按安全分层（破坏性/读取私密文本/只读发现/聊天媒体/历史元数据）紧凑记录工具表面，SKILL.md 按需引用，符合渐进式披露
- `.agents` 与 `.codex` 两份 skill 副本保持同步

### 依赖与配置
- `gemini-webapi` 依赖下限从 `>=1.20.0` 升至 `>=2.0.0`（v2.1.x 期间静默升级，此条补登 changelog）：代码深度使用 `types.RPCData`、`constants.GRPC`、`constants.Model`、`constants.Endpoint`、`constants.AccountStatus`、`types.video.GeneratedMedia`、`types.ModelOutput`、`utils.extract_json_from_response`、`utils.get_nested_value` 等 2.x 才稳定的 API
- 同步修正 `src/server.py` 自报版本号 `v2.0` → `v2.2.0`（docstring / FastMCP instructions / 启动日志 3 处），与 `pyproject.toml` 保持一致
- 修正 `AGENTS.md` 模块清单：删除已移除的 `auth.py`，补全 `remote_chat_cleanup_manager.py`、`thinking_client.py`、`error_handler.py` 及 `tools/` 下的 `annotations.py`、`manifest_data.py`、`utils.py`
- 修正 `README.md` / `README.zh-CN.md` / `docs/launch-kit.md` 中所有 `v2.1.2` wheel URL → `v2.2.0`；修正 README 徽章 `tests-70` → `tests-118`；清理 `README.md` 残留的 `/Users/jack/...` 硬编码路径，改用 `skills-ref validate`

### 代码质量
- 删除死代码 `src/auth.py`（5 个公共函数全仓零引用）
- 删除未使用的 `load_images` 函数及未用导入
- 修复 `tools/__init__.py:register_tools` 公共 API 类型标注（`mcp: FastMCP`、`list[str] | None`、`-> None`）
- 清理 `error_handler.py` 未使用导入，`Dict` → `dict` 现代化类型
- 修复 `cookie_manager.py` 3 处静默吞导入错误（`except Exception: return {}`）→ 具体异常 + `logger.warning`
- 修复 `cookie_manager.py` 2 处 `client.close()` 的 `except Exception: pass` 静默吞错 → `logger.debug` 记录关闭异常
- `manage.py` 新增 `_json_response()` helper，替换 23 处重复的 `json.dumps(payload, ensure_ascii=False, indent=2)` 模式
- `skill_server.py` 抽取 `_error_text(e, tool_name)` helper，替换 11 处重复的 `logger.error + return [TextContent(text=f"Error: {e}")]` 模板
- 修复 `ClientManager.initialize` 的 TOCTOU 竞态：`if not self._initialized` 检查移入 `_init_lock`，防止并发协程重复调用 `client.init()`

### 重构
- `skill_server.py` 的 `account` god function（157 行 / 11 action）拆分为 11 个独立 async handler + 2 个分发表（auth-free / client-based），dispatcher 仅 12 行，保留原语义
- `skill_server.py` 的 `scheduled` god function（4 action：list/get/create/delete）拆为 4 个独立 async handler + dispatcher，dispatcher 仅保留 try/except + action 分发
- `skill_server.py` 的 `session` god function（4 action：create/send/list/reset）拆为 4 个独立 handler；`list`/`reset` 不需 client，下放为 sync handler，client 初始化只保留在 `create`/`send` 内
- `research.py` 的 `gemini_deep_research`（204 行）拆为 35 行主函数 + 4 个辅助函数：`_run_native_deep_research`（client 原生 plan/start/wait 路径）、`_run_fallback_deep_research`（`generate_content(deep_research=True)` 回退路径）、`_deep_research_timeout_error`、`_deep_research_generic_error`
- `tools/prompts.py` 和 `skill_server.py` 的 `_prompt_manager` 单例加锁（`_prompt_manager_lock`），防止并发 MCP 工具调用创建多实例并覆盖 JSON 文件

### 测试
- 新增 `test_skill_server_session_lifecycle_and_dispatch`：覆盖 session 4 个 action + invalid action 短路；用 FakeSession/FakeClient 验证 single-session reset 不触发 client reset、reset_all 触发
- 新增 `test_skill_server_session_invalid_image_path_short_circuits`：验证无效 image_path 在 client 初始化前失败
- 新增 `tests/test_error_and_session.py`（38 个测试）：`error_handler.py` 全模块覆盖（7 个 ERROR_CODES 分支 + handle_error 字符串匹配的边界误判 + format_error_response + GeminiError + wrap_tool_error）；`session_manager.py` 全模块覆盖（store/get/remove/pop/list/clear + `_clean_expired_sessions` 过期逻辑 + get/pop 触发清理）；`extract_remote_chat_id` 两份实现的漂移守护（5 个场景）
- 新增 `tests/test_skill_server_prompts_cookie.py`（8 个测试）：skill_server 的 `prompts`（4 action + invalid + 缺参数早退）和 `cookie`（3 action + invalid + profiles 列表 + 空 profiles）此前零功能测试
- 新增 `tests/test_cookie_manager.py`（25 个测试）：`CookieManager` 核心生命周期行为覆盖（`__init__` + `_load_initial_cookie` + `_load_extra_cookies_from_env`、`update_cookie` + `on_cookie_update` 回调链含异常吞并、`get_cookie_status` VALID/EXPIRED/UNKNOWN 状态机、`needs_refresh`、`refresh_cookie` 无 browser / browser 成功 / browser 失败三条路径、`to_env_vars`、`start_monitor`/`stop_monitor` 启停幂等 + 短间隔循环不崩、`CookieData` 默认值、`CookieStatus` 枚举值稳定、4 线程并发 `update_cookie` 安全性）—— 此前仅浏览器候选探测覆盖，回调链/状态机/刷新路径零行为测试
- 新增 `tests/test_client_manager.py`（23 个测试）：`validate_config` / `get_configured_proxy` / `get_default_chat_retention_seconds` 纯函数边界覆盖（缺 PSID 抛错、本地不可达 proxy 早退、无效 retention 回退、0/负数边界）；`prepare_browser_cookie_cache` 6 条路径覆盖（force=False 早退 / source 非 browser_ 早退 / source=browser_ 创建+设 env / force=True 跳过检查 / GEMINI_COOKIE_PATH 不一致早退 / 清空 stale cache 文件）；`ClientManager` 生命周期覆盖（get_client 创建一次 / reset 清空后重建 / initialize 已初始化短路 / 并发 initialize 不重复调用 init，验证 `_init_lock` TOCTOU 修复）
- 新增 `tests/test_chat_session_lifecycle.py`（6 个测试）：`gemini_reset_session`（destructiveHint=True）4 条 delete_remote_chat 决策路径覆盖（session 不存在 / retain_chat=False 触发删除 / retain_chat=True 跳过 / session 无 cid 时 delete None）；`gemini_list_sessions` 空列表与非空列表渲染 —— 此前仅有注解形状测试
- 修复 `tools/research.py` `_walk_nested_json` 和 `tools/manage.py` `_summarize_probe_response` 的 2 处静默吞错（`except Exception: return` → 加 `logger.debug` 记录路径/rpcid 便于排障）
- 修复 `src/` 全部 ruff 错误（9 → 0）：删除未使用 import、移除无占位符 f-string 前缀、为 `client_wrapper.py` 的 facade re-export 加 `# noqa: F401`、为 `client_manager.py` try/except 后的 import 加 `# noqa: E402`
- 测试套件 70 → 172 passed

### 性能优化
- 上提 `gemini_webapi.utils` 导入到模块级，消除 `_extract_rpc_bodies`/`_summarize_probe_response` 在分页循环内的函数级 import
- `research.py` 3 处循环内 `re.match` → 模块级 `re.compile`
- `_merge_conversation_source_items` 的 `sources_by_id` 从 `list`（O(k) 成员测试）→ `set`（O(1)），输出时 `sorted()` 物化
- `WEB_UI_CAPABILITIES` 深拷贝从 `json.loads(json.dumps())` → `copy.deepcopy`
- `_account_features`（8 probe）与 `_account_usage`（2 probe）串行 RPC → `asyncio.gather` 并发，保持输出顺序，N×RTT → 1×RTT

### 仓库结构
- 抽取 676 行纯数据到新模块 `src/tools/manifest_data.py`（`WEB_UI_CAPABILITIES` / `WEB_FEATURE_PROBES` / `TOOL_MANIFEST`），`manage.py` 从 4591 → 3924 行，通过 re-export 保持向后兼容
- `.gitignore` 新增 `*.egg-info/`、`build/`、`dist/` 规则

### 文档与分发
- 将默认 `README.md` 改为英文公开首页，并新增 `README.zh-CN.md` 保留完整中文入口
- 新增 `docs/assets/gemini-web-mcp-banner.svg`，改善 GitHub 仓库首屏视觉呈现
- 更新打包清单，确保源码包包含中文 README 和 README banner 资产
- 更新 `docs/architecture.md` 项目结构，补充 `manifest_data.py`、`skill_server.py` 等模块

## v2.1.2 (2026-07-04)

### 发布与分发
- 新增 `scripts/package_release.py`，一键构建 wheel、sdist 和 standalone Codex skill zip
- 新增 `MANIFEST.in`，确保源码包包含 docs、evaluations 和公开 `.agents/skills/gemini-web-mcp`
- 新增 `docs/launch-kit.md`，提供安装链接、分发清单和中英文社交媒体发布文案
- 更新 README 顶部版本、Release/Skill/License badges 和公开分发说明

## v2.1.1 (2026-07-04)

### 发布与分发
- 新增公开 repo skill 路径 `.agents/skills/gemini-web-mcp`，让 Codex 和 skill 聚合站可直接从 GitHub 发现/安装
- 补充 README 中的 GitHub skill 安装命令、手动安装步骤，并明确 skill 与 MCP server runtime 的分层关系
- 扩展 skill packaging 测试，校验 `.codex/skills` 本地副本和 `.agents/skills` 公开副本保持一致

## v2.1.0 (2026-07-04)

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
- 新增 `gemini_scan_chat_history_sources`，按前端观测到的多个历史 RPC 过滤器、
  原生 notebook 对话和 Remy goal conversation 引用深度枚举聊天元数据，便于验证历史覆盖范围
- 新增 `gemini_history` 作为只读历史聚合入口，把 list/scan/search/read/export 合并到一个 agent-facing 工具
- 新增 `gemini_cleanup_test_artifacts`，默认 dry-run 查找并可选删除显式 marker 匹配的测试聊天和测试定时任务
- 新增 `gemini_get_tool_manifest`，为 agent 暴露工具安全、隐私、分页、可用分组、当前启用状态和推荐工作流元数据
- primary MCP 工具增加 MCP `ToolAnnotations`，标记只读、远端修改、本地修改和 destructive 操作
- 新增 `gemini_probe_web_features`，用浏览器实测到的只读 RPC 探测 Library、公开链接、用量、个性化、记忆导入等新版 Web 入口
- 新增 `gemini_get_web_capabilities`，返回 Pro 网页模型、思考等级、工具菜单、设置入口和 MCP 覆盖清单
- 新增 `gemini_list_public_links`、`gemini_get_usage_limits`、`gemini_list_library_capabilities`，把可稳定解析的新版 Web 入口从 probe 升级为只读工具
- 新增 `gemini_list_notebooks`、`gemini_list_notebook_chats`、`gemini_move_chat_to_notebook`，支持 Gemini Web 原生笔记本列表、笔记本内最近对话读取，以及把已有聊天移动到目标笔记本并校验
- 新增 `gemini_notebooks` 作为原生 Notebook 只读聚合入口，供 `history-organize` 使用
- 新增 `gemini_list_scheduled_actions`，列出定时操作页面返回的 active/inactive 任务条目
- 新增 `gemini_get_scheduled_action`，用前端确认的 `kwDCne` / GetTask RPC 按 ID 只读校验定时操作
- 新增 `gemini_create_scheduled_action` / `gemini_delete_scheduled_action`，支持每日定时操作的创建和按 ID 删除
- 新增 `gemini_get_tool_mode_status`，只读读取 Canvas / 学习辅导等工具模式附近出现的 Web 内部状态枚举
- 新增 `gemini_account_inventory` 作为账号只读聚合入口，把 capabilities/status/features/links/usage/library/notebooks/scheduled/modes/models 收口到一个工具
- 新增 `gemini_list_research_report_actions` / `gemini_create_from_research_report`，为 Deep Research 完成后的网页实测“创建”菜单提供 MCP 侧等价入口，可生成网页、信息图、测验、抽认卡、音频概览脚本和自定义应用规格；当前未观测到稳定原生网页菜单 mutation RPC
- `gemini_list_chats` 增加 `offset`、`response_format` 和分页元数据
- `gemini_list_public_links`、`gemini_list_library_capabilities`、`gemini_list_scheduled_actions`
  和 `gemini_get_tool_mode_status` 增加统一分页元数据，便于 agent 分页读取账号内容
- 忽略不可达的本地 `GEMINI_PROXY`，避免旧代理端口导致客户端初始化失败
- 从 Chrome 读取 Cookie 时验证多个本地 profile，隔离 gemini_webapi cookie cache，
  并优先选择能读取 scheduled registry 的 profile
- 新增 `gemini_list_browser_cookie_profiles`，用于列出 Chrome profile 的非敏感账号
  诊断；`gemini_get_cookie_from_browser` 支持 `profile` 参数，便于手动对齐多账号上下文
- 新增 `gemini_doctor`，用于只读预检工具面、Cookie 状态、浏览器 profile 对齐和媒体校验依赖
- `gemini_list_browser_cookie_profiles` 增加 `chrome_selected_profile` 诊断；定时操作
  create/delete 增加 `verification_status`、by-id 可读性和 `task_state` 校验，区分 RPC 已接受、registry 已验证和 deleted tombstone
- 低 token `src.skill_server` 增加 `history` 和 `scheduled`，其中 `scheduled` 支持 list/get/create/delete；并扩展 `history` 支持 search/export、`account` 支持 manifest/features/links/usage/library
- 新增项目内 Codex skill：`.codex/skills/gemini-web-mcp`，用于指导 agent 安全使用 manifest、聊天记录和验证流程

### 工具面收缩
- 新默认工具组改为 `core`
- 新增意图型工具 profile：`model`/`chat` 仅调用模型，`history` 只读历史整理，
  `history-organize` 允许将历史对话移动到 native Notebook，`account-read` 只读盘点账号
  Web surface，`scheduled-read`/`scheduled-admin` 分离定时操作读写权限
- `history` 和 `account-read` 改为 facade-first：普通 agent 分别只看到 `gemini_history`
  和 `gemini_account_inventory`；旧颗粒工具继续保留在 `manage` / `all` 作为兼容维护面
- `all` 保留完整维护/验证工具面，但不再加载本地提示词工具
- `manage` 内部改为按 profile 分层注册，避免只想整理历史的 agent 同时拿到账号写操作、
  scheduled mutation 或 Gems 管理工具
- 移除 `gemini_list_features`，减少低价值枚举型工具
- 当前默认工具面为 `core` 加始终可用的 manifest/cookie helpers；`all` 额外提供
  `gemini_history`、`gemini_account_inventory`、`gemini_notebooks`、
  `gemini_inspect_account`、`gemini_cleanup_test_artifacts`、`gemini_list_chats`、`gemini_search_chats`、
  `gemini_scan_chat_history_sources`、`gemini_read_chat`、`gemini_export_chat`、`gemini_delete_chat`、
  `gemini_get_web_capabilities`、`gemini_probe_web_features`、`gemini_list_public_links`、
  `gemini_get_usage_limits`、`gemini_list_library_capabilities`、
  `gemini_list_notebooks`、`gemini_list_notebook_chats`、`gemini_move_chat_to_notebook`、
  `gemini_list_scheduled_actions`、`gemini_get_scheduled_action`、`gemini_create_scheduled_action`、
  `gemini_delete_scheduled_action`、`gemini_get_tool_mode_status`、
  `gemini_list_models`、`gemini_manage_gems`
- `gemini_manage_prompts` 保留为 `prompts` 可选分组，不属于默认工具面

### 文档与验证
- 补充 Gemini Web live UI 覆盖说明和媒体路由说明
- 新增 `evaluations/gemini_web_mcp_contract.xml`，提供 17 个只读、稳定答案的 MCP contract evaluation
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
