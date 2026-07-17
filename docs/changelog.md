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
- 修复 4 个文件的 mypy 类型错误（57 → 51）：`cookie_manager.py` 的 `psidts` 可空回退、`thinking_client.py` 的 `int(learning_config[...])` 加 `# type: ignore[call-overload]` 并重构 if/return 流让 mypy 正确收窄、`client_wrapper.py` 的 `list_sessions` 过滤 None、`tools/prompts.py` 重命名循环变量 `prompt` → `item` 避免与同函数内 `Optional[dict]` 赋值的类型冲突
- 消除 `src/` 全部 mypy 错误（67 → 0，22 个源文件 clean）：`constants.py` 用 `TypedDict`（`ModelConfig` / `LearningModeConfig`）替代裸 `dict` 字面量，使 `resolve_model_name` 返回 `str` 而非 `object`，消除 4 个级联错误；`tools/manage.py` 的 `_tool` 装饰器引入 `TypeVar("_F", bound=Callable[..., Any])` 并改为通过副作用注册（`mcp.tool(...)(func)` 丢弃返回值、始终 `return func`），保留被装饰函数的声明返回类型，一次消除 18 个 dispatcher 模式 `no-any-return`；`_clamp_int` 加 `number: int` 标注 + `# type: ignore[call-overload]`；`_sanitize_account_status` / `_format_chat_export_markdown` / `gemini_move_chat_to_notebook` 用临时变量替代 `X if isinstance(X, dict) else {}` 双调用模式（mypy 不跨调用收窄）；`_read_chat_turns` 的 `turns_raw` + `isinstance` 收窄；`_move_chat_to_notebook_payload` 的 `conversation: list[Any]`；`_web_capabilities_payload` / `gemini_list_notebook_chats` 的 `payload: dict[str, Any]`；`gemini_search_chats` 标注 `matches`/`fields`/`snippets` 并重命名 `fields` → `fields_str`（避免 `list[str]` 与 `str` 同名冲突）；`gemini_get_usage_limits` 标注 `results`/`entries`；`_fetch_conversation_metadata_sources` 调用方标注 `pinned_diag`/`recent_diag`；`tools/media.py` 的 `gemini_generate_music` 转发返回值加 `cast(list[TextContent], ...)`；`skill_server.py` 的 history export 分支重命名 `chat` → `history`（避免与 search 分支 `_chat_to_dict` 返回的 `dict` 类型冲突）；`pyproject.toml` 新增 `[[tool.mypy.overrides]]` 对 `gemini_webapi.*` 和 `browser_cookie3` 设置 `ignore_missing_imports = true`（第三方包未发布 PEP 561 stubs）
- 新增 `tests/test_cleanup_test_artifacts.py`（34 个测试）：`gemini_cleanup_test_artifacts`（destructiveHint=True）此前仅有注解形状测试，**dry_run=False 会真实删除远端聊天与定时任务却零行为覆盖**——本文件补充 `_split_cleanup_markers`（空串/空白过滤/多值/保留大小写）、`_marker_hits`（大小写不敏感/None/多 marker）、`_format_cleanup_markdown`（空 payload/chats 三态 deleted-matched-error/scheduled verification_status 优先/errors 段/dry_run 提示）、`_cleanup_test_artifacts_payload`（chats dry_run 命中 id/title、dry_run=False 成功删除/删除抛异常/缺 delete_chat 能力、scan_turns 命中 turn/抛异常不中断、缺 list_chats 能力、target=chats 跳过 scheduled、target=scheduled 跳过 chats、scheduled dry_run/dry_run=False 删除/dry_run=False RPC 抛异常、空 markers 回退 codex-、max_chats 夹紧到 [1,100] 与切片窗口、缺 _batch_execute 能力）；工具层注册 + DESTRUCTIVE_REMOTE 注解 + call_tool markdown/json 双格式
- 清理 3 个测试文件中前几轮引入的未使用 import（`test_cleanup_test_artifacts.py` 的 `pytest`、`test_chat_session_lifecycle.py` 的 `pytest`、`test_client_manager.py` 的 `pathlib.Path`）
- 清理 tests/ 历史遗留 ruff 错误（4 → 0）：`test_error_and_session.py` 删未用的 `SessionData`、`test_imports.py` 删重复的 `src.tools.media` import（typo）、`test_core.py`/`test_imports.py` 的 side-effect import 加 `# noqa: F401`
- 新增 `tests/test_server_cookie_tools.py`（13 个测试）：`src/server.py` 的 `gemini_get_cookie_status`（Manager 不可用 / 可用+已设置 / 可用+未设置+需刷新）、`gemini_list_browser_cookie_profiles`（空 profiles / 含 error 条目 / 正常多字段渲染 / account_available=None 渲染 unknown / response_format=json / 抛异常 handle_error 兜底）、`gemini_get_cookie_from_browser`（成功无 profile / 成功带 profile / 失败 / 抛异常 handle_error 兜底）—— 此前仅有注解形状测试
- 新增 `tests/test_doctor_helpers.py`（26 个测试）：`gemini_doctor` 此前仅有注解形状测试，`_doctor_check` / `_doctor_overall_status` / `_format_doctor_markdown` / `_doctor_payload` 四个 helper 零直接覆盖——本文件补充 `_doctor_check`（None 值过滤 / 空 details）、`_doctor_overall_status`（空/全 ok/全 skip/混合/warn 优先/error 优先 6 种组合）、`_format_doctor_markdown`（browser=disabled / error profile / account=None / 空 recommendations / detail 白名单 4 key）、`_doctor_payload`（cookie_status 3 分支 / browser_profiles 3 分支 / alignment ok / ffprobe warn + recommendations / generated_media warn / validate_browser 推荐 / overall_status warn & ok / cookie 值不泄露）
- 新增 `tests/test_tool_helpers.py`（69 个测试）：补齐 `utils` / `constants` / `media` / `file` 四模块纯 helper 的零覆盖空洞——`validate_local_file_path`（空/路径遍历/不存在/非文件/扩展名/size/happy/无扩展名 8 分支）、`validate_image_paths`（空/逐个校验/fail-fast/非图片扩展名）、`validate_optional_image_path`（None/单张/无效）、`extract_remote_chat_id`（cid/metadata/无匹配）、`parse_response`（text/override/image/video/music 模型分流/remote_chat_id）、`get_stream_text_piece`（text_delta 优先/回退/缺失/falsy 不回退）、`resolve_model_name` / `normalize_model_alias` / `describe_model_name` / `supported_learning_modes`（查表函数全覆盖）、`resolve_media_request`（image 固定 Nano Banana 2 / music 非 pro=Lyria 3 / pro+standard=Lyria 3 / pro+extended=Lyria 3 Pro / 未知类型 passthrough）、`_safe_media_filename`（正常/特殊字符/48 字符截断/末尾剥离/空回退）、`_media_timeout`（显式/image=180/其他=600）、`_set_client_timeouts`+`_restore_client_timeouts`（无属性/max 算法/不降低/watchdog 下限 120/写回/None 跳过/往返）、`_prepend_backend_note`（空 note/空 parsed/正常拼接）、`_media_from_music_card`（mp3/mp4/无 URL/空 title 回退）、`_validate_url`（空/无 scheme/无 netloc/合法/异常）、`_validate_file_path` 转发壳
- 扩展 `tests/test_chat_session_lifecycle.py`（+19 个测试，6→25）：补齐 `gemini_send_message` 的参数回退逻辑——此前 `temporary` / `learning_mode` / `retain_chat` / `delete_after_seconds` 四参的 "None 时从 session_data 回退、传入则覆盖" 行为零分支覆盖（仅 happy path 间接走过）。新增：session 不存在早退 / image_paths 无效在 session 检查前早退 / temporary 三态（None 回退 / 显式覆盖 / session 缺失回退 False）/ learning_mode 三态（None 回退 / 显式覆盖 / 双 None 不写入 kwargs）/ retain_chat 三态 / delete_after_seconds 三态（含双 None 传 None）/ thinking_level 从 session 取+缺失回退 standard / schedule cleanup 用 session.cid / 返回 response.text / request_kwargs 含 prompt+files
- 新增 `tests/test_chat_tools.py`（14 个测试）：`gemini_chat` 与 `gemini_start_chat` 入口工具此前仅 happy path 间接覆盖，关键行为契约零断言——`gemini_chat` 补齐 image_paths 无效在 client init 前早退 / request_kwargs 全字段注入（prompt/files/model/thinking_level/gem/temporary）/ model alias 经 resolve_model_name 解析 / learning_mode 条件注入（None 省略、truthy 写入）/ cleanup_due_remote_chats 接收 client / schedule_remote_chat_cleanup_from_response 入参（response 同一对象 + retain/delete/source）/ parse_response 用 model 解析含 remote_chat_id；`gemini_start_chat` 补齐 client.start_chat 接收 model_name 与 gem / store_session 全入参（session_id 8 字符 / session / model 原始 alias / thinking_level / learning_mode / temporary / retain_chat / delete_after_seconds）/ 默认值（learning_mode=None / temporary=False / retain_chat=False / delete_after_seconds=None）/ 返回文本含 session_id 与 model_name / cleanup_due_remote_chats 接收 client / 不调 schedule cleanup（无 response）
- 扩展 `tests/test_chat_tools.py`（+13 个测试，14→27）：补齐 `gemini_chat_stream` 与 `gemini_send_message_stream` 流式工具的空流分支与累加逻辑——此前两个流式工具仅 happy path 间接覆盖，`final_response is None` 回退分支零覆盖。`gemini_chat_stream` 补齐 image_paths 无效早退 / 空流返回空文本且跳过 cleanup（`if final_response:` 守卫）/ 多 chunk text_delta 累加 / schedule cleanup 用最后一个 response / request_kwargs 全字段注入 / learning_mode 条件省略；`gemini_send_message_stream` 补齐 session 不存在早退 / image_paths 无效在 session 检查前早退 / 多 chunk 累加 / **空流仍调 cleanup 传 None（文档化与 chat_stream 的不一致：send_message_stream 无 `if final_response:` 守卫，总是调用）** / schedule cleanup 用最后 response / temporary 回退 session / learning_mode 双 None 省略
- 新增 `tests/test_file_tools.py`（21 个测试）：`gemini_upload_file` 与 `gemini_analyze_url` 此前仅有 1 个间接用例（同时验证路径遍历与 URL 格式无效早退），关键行为契约零断言——`gemini_upload_file` 补齐 无效路径在 client init 前早退 / generate_content 位置参数 prompt + files=[safe_path] + model/thinking_level/timeout=60 / analysis_prompt 默认值与自定义值 / 返回前缀 "✅ Successfully analyzed {filename}" / response.images 拼接（📷 Images + 编号 + title + url）/ remote_chat_id 拼接 / schedule cleanup 入参 / cleanup_due_remote_chats 接收 client / asyncio.TimeoutError 分支（"文件分析超时"）/ 通用 Exception 分支（"❌ Error: {e}"）/ 异常分支跳过 schedule cleanup；`gemini_analyze_url` 补齐 无效 URL 早退 / prompt 默认值（"Please analyze the content at this URL: {url}"）/ prompt 自定义拼接（用户提示 + URL + "Use the URL above..."）/ generate_content 无 files 参数 / 返回无 ✅ 前缀（与 upload_file 不同）/ response.images 拼接 / remote_chat_id 拼接 / schedule cleanup source / asyncio.TimeoutError 分支（"URL 分析超时"）/ 通用 Exception 分支
- 新增 `tests/test_media_tools.py`（36 个测试）：`gemini_generate_media` 与 `gemini_generate_music` 此前仅 5 个 happy/edge 间接用例，关键集成契约零断言——`gemini_generate_media` 补齐 image_path 无效在 client init 前早退 / generate_content 全字段注入（prompt 模板 / files / model / thinking_level / timeout）/ 有效 image_path 转 files / cleanup_due_remote_chats 接收 client / timeout 默认值（image=180 / music=600 / video=600）/ 显式 timeout 覆盖 / 零与负 timeout 回退默认 / client.timeout 临时提升并 restore（含异常与 TimeoutError 分支 finally）/ 后端路由（image 恒用 flash / image 返回 Nano Banana 2 + Pro redo note / music+flash=Lyria 3 / music+pro+standard=Lyria 3 / music+pro+extended=Lyria 3 Pro / video=Gemini Web default）/ asyncio.TimeoutError 分支（含后端标签 + "可增大 timeout_seconds"）/ 通用 Exception 分支（含后端标签 + "通用 generate_content"）/ 异常分支跳过 schedule cleanup / **空响应仍调 schedule cleanup（文档化与 chat_stream 的 `if final_response:` 守卫不一致）** / remote_chat_id 拼接 / response.media 用 effective_alias 渲染 Lyria 3 Pro 标签 / schedule cleanup source 按媒体类型分流（"gemini_generate_media:{media_type}"）/ schedule cleanup 入参（response 同一对象 + retain/delete）；music 回收路径补齐 response.media 为空时调 _fetch_music_media_from_chat 恢复 / 回收异常吞咽不崩溃 / 非 music 跳过回收；`gemini_generate_music` 补齐 转发后用 music prompt 模板 / **默认 thinking_level=extended → Lyria 3 Pro（与 generate_media 默认 standard → Lyria 3 的关键差异）** / **source 仍为 "gemini_generate_media:music" 非 "gemini_generate_music"（文档化 cleanup 归因不一致）**
- 新增 `tests/test_research_tools.py`（33 个测试）：`gemini_deep_research` 此前在 test_tool_workflows.py 有 9 个间接用例（native happy path / fallback / chat-history 轮询 / immersive report 提取），但关键集成契约零断言——入口补齐 cleanup_due_remote_chats 接收 client / 默认 thinking_level=extended（与 gemini_chat 默认 standard 不同）/ 默认 timeout_seconds=600；fallback 路径补齐 generate_content 收到 deep_research=True / model=解析后的 model_name / timeout=原始 timeout_seconds（不走 _phase_timeout 的 max(30,...) 底线，与 native 路径不同）/ prompt 含 "Requested MCP model alias" 与 "Transport model selection" / schedule source="gemini_deep_research:fallback"（与 native 的 "gemini_deep_research" 区分）/ schedule 接收 response 同一对象 / retain_chat 与 delete_after_seconds 转发（含默认 False/None）/ 返回文本前缀 "# 📚 Deep Research 计划: {query}" / 含 "- 请求模型:" 与 "- 实际研究传输:" 行 / 含 "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API" 警告 / 含 response.text 内容；native 路径补齐 has_native_api 判定（client 有 3 个方法）/ start_chat 接收 research_model（默认 Model.UNSPECIFIED）/ create_deep_research_plan 收到含模型元数据的 query / start_deep_research 接收 plan / plan.research_id 存在时调 wait_for_deep_research(plan, poll_interval=, timeout=) / poll_interval 的 max(3, ...) clamp / schedule cid 从 plan.cid 回退（chat.cid 被 _start_fresh_research_chat 清空为 ""）/ schedule source="gemini_deep_research" / retain/delete 转发 / done=True 时返回 "# 📚 Deep Research 报告:" + "完成: 是" + "## 报告" + result 文本 / 含 Research ID 与标题 / 含 model_note；thinking_scope 补齐 非 default transport（非标准 alias 如 "gemini-3-pro"）时调用 / default transport（标准 alias → Model.UNSPECIFIED）时跳过；错误处理补齐 asyncio.TimeoutError → "❌ Deep Research 超时（{N}秒）" + "AI Plus 订阅" / RuntimeError → "❌ Deep Research 失败: {str(e)}" + "该功能在您所在的区域是否可用" / 异常分支跳过 schedule cleanup / native 路径 wait 抛 TimeoutError 与 RuntimeError 也被外层捕获
- 新增 `tests/test_prompts_tools.py`（44 个测试）：`tools/prompts.py` 覆盖率 48% → 100%（136 stmts, 0 miss），此前仅 test_tool_workflows.py 间接覆盖 create + list happy path，关键行为契约零断言——`PromptManager._load_prompts` 补齐 文件不存在跳过 / JSON 解析异常吞咽并记录 ERROR 日志 / 正常加载既有 prompts 字典；`_save_prompts` 补齐 写入到不存在目录抛 FileNotFoundError 被吞咽并记录日志；`create_prompt` 补齐 返回 uuid4 字符串 / 持久化到文件 / 字段齐全（id/name/content/category/description/created_at/updated_at）/ 默认 category='通用' 与 description=''；`get_prompt` 补齐 命中 / 未命中返回 None；`list_prompts` 补齐 空 / 无分类过滤按 created_at 降序 / 按分类过滤仍降序；`list_categories` 补齐 空 / 多分类去重排序；`update_prompt` 补齐 未找到返回 False / 仅 name 部分更新（其他字段不变）/ 全量更新并刷新 updated_at / **显式空字符串更新（验证 `is not None` 检查非 falsy 检查，允许清空字段）**；`delete_prompt` 补齐 未找到返回 False / 命中删除并持久化；`get_prompt_manager` 补齐 单例创建一次（**发现 DEFAULT_PROMPTS_FILE 在类定义时绑定为 `__init__` 默认参数，monkeypatch 模块级常量无效，改用子类硬编码 tmp_path**）/ 8 线程并发返回同一实例（验证 `_prompt_manager_lock`）；`gemini_manage_prompts` 6 个 action 全覆盖——list（空/分类过滤空/非空含 category 头与 description 行/无 description 省略行）、list_categories（空/非空含每类条目数）、get（缺 prompt_id/未找到/全字段详情/缺 description 键触发 `.get('description','无描述')` 默认值）、create（缺 name/缺 content/默认 category/explicit category）、update（缺 prompt_id/未找到/成功）、delete（缺 prompt_id/未找到/成功）、**invalid action 经 MCP 抛 ToolError（FastMCP pydantic Literal 校验在 dispatch 前）+ 直接调 tool.fn 绕过校验触发 line 251 '❌ 无效的 action。' 兜底（生产经由 MCP 不可达的防御性 fallback）**、异常兜底（manager.list_prompts 抛 RuntimeError → '❌ 失败: {e}'）
- 测试套件 70 → 494 passed

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
