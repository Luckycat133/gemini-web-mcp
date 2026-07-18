# 实机测试清单

本文件列出**无法被单元测试覆盖、必须用真实 Gemini 账号 + Cookie 实机验证**的内容。单元测试用 FakeClient / monkeypatch 验证的是"参数传递、annotation、dispatcher 路由、payload 形状"——而本清单关注的是**远端真实行为是否和文档/MCP 标注一致**。

## 何时跑实机测试

| 触发场景 | 必跑范围 |
|---|---|
| 升级 `gemini-webapi` 依赖 | 全量 P0 + P1 |
| Gemini Web 前端改版（模型名、菜单、RPC） | 受影响模块 + `gemini_get_web_capabilities` |
| 修改 `client_wrapper.py` / `cookie_manager.py` | P0 鉴权 + Cookie profile |
| 修改 `tools/research.py` | P0 Deep Research 全流程 |
| 修改 `tools/media.py` | P0 媒体生成 |
| 修改 `tools/manage.py` 里的 RPC 形状 | 受影响的 P1 RPC 工具 |
| 发布新版本前 | 全量 P0 + 抽查 P1 |
| 日常 PR | 单元测试通过即可，不强制实机 |

## 前置准备

1. **专用测试账号**——不要用主账号，逆向访问有风控风险（见 [AGENTS.md](../AGENTS.md) "Security & Configuration Tips"）
2. **AI Plus 订阅**——Deep Research、部分 Pro 模型需要
3. **Chrome 已登录测试账号**——供 `gemini_get_cookie_from_browser` 抓取
4. **环境变量**：

   ```bash
   export GEMINI_PSID="__Secure-1PSID 的值"
   export GEMINI_PSIDTS="__Secure-1PSIDTS 的值"
   export GEMINI_TOOLS=all   # 实机测试通常需要全工具面
   ```

5. **启动方式**：直接前台跑，方便看日志

   ```bash
   GEMINI_TOOLS=all python -m src.server
   # 或用 MCP Inspector 交互调用
   mcp dev src/server.py
   ```

6. **测试 marker 约定**：所有测试产生的聊天/定时任务用 `codex-test-` 或 `manual-test-` 前缀，便于 `gemini_cleanup_test_artifacts` 清理

---

## P0 — 核心路径（必跑）

这些是项目的核心价值，任何一次都至少跑一遍。

### 1. 鉴权与客户端初始化

| # | 步骤 | 预期 |
|---|---|---|
| 1.1 | 调 `gemini_doctor` | `cookie_status.has_cookie=true`、`tool_surface` 列出期望分组、`media_dependencies` 无缺失 |
| 1.2 | 调 `gemini_get_cookie_status` | `status="ok"`、`source` 指向环境变量或浏览器 |
| 1.3 | 调 `gemini_list_browser_cookie_profiles` | 列出 Chrome profile，`Default` 与 `Profile 1` 等；`has_psid` 至少一个为 true |
| 1.4 | 删掉 `GEMINI_PSID`，重启，调 `gemini_doctor` | `cookie_status.status="missing"`，但工具不崩 |
| 1.5 | 提供过期 `GEMINI_PSID`，调 `gemini_chat` | 返回明确的认证错误文本，不返回空字符串 |

**风险点**：`__Secure-1PSIDTS` 过期比 `__Secure-1PSID` 快，两者必须同步更新。

### 2. 单轮对话（`gemini_chat` / `gemini_chat_stream`）

| # | 步骤 | 预期 |
|---|---|---|
| 2.1 | `gemini_chat(message="回复 OK 两个字", model="flash")` | 文本含 "OK"，无报错 |
| 2.2 | `model="pro"` 重复 2.1 | 返回正常；如果账号没 Pro 权限，错误文本要明确说"模型不可用"，不能空 |
| 2.3 | `model="flash-lite"` 重复 | 同上 |
| 2.4 | `thinking_level="extended"` 重复 | 返回正常，文本长度通常比 standard 长 |
| 2.5 | `image_paths=["/abs/path/cat.jpg"]` | Gemini 能识别图片内容并描述 |
| 2.6 | `gemini_chat_stream(...)` 同样参数 | 流式返回多个 text delta，最终拼起来和 `gemini_chat` 接近 |
| 2.7 | `temporary=true` | 返回正常；调用 `gemini_list_chats` 不应看到这条 |
| 2.8 | `gem_id="<某个 Gem ID>"` | 用指定 Gem 的人格回答 |

**关键校验**：返回里 `Remote chat ID: c_xxx` 必须能解析（用于 cleanup）。`test_parse_response_exposes_remote_chat_id_for_cleanup` 只验了格式，实机要验 ID 真的能在 `gemini_delete_chat` 里删掉。

### 3. 多轮会话（`gemini_start_chat` / `gemini_send_message` / 流式版本）

| # | 步骤 | 预期 |
|---|---|---|
| 3.1 | `gemini_start_chat(model="flash")` → 拿 `session_id`（形如 `sess_1`） | 返回 `Session created: sess_N` |
| 3.2 | `gemini_send_message(session_id, "我叫张三")` | 正常回复 |
| 3.3 | `gemini_send_message(session_id, "我叫什么名字？")` | 回复含"张三"，证明上下文保留 |
| 3.4 | `gemini_send_message_stream(...)` 同上 | 流式正常 |
| 3.5 | `gemini_list_sessions` | 列出 `sess_1`，含 model 信息 |
| 3.6 | `gemini_reset_session(session_id)` 后再 `gemini_send_message` | 第二轮不记得"张三" |
| 3.7 | `session_id="sess_invalid"` | 明确错误，不崩 |

**关键校验**：session 是**本地概念**，`sess_N` 不等于 Gemini 的 `cid`。`gemini_reset_session` 只清本地状态，不动远端。要重置底层连接用 `gemini_reset`。

### 4. 媒体生成（`gemini_generate_media` / `gemini_generate_music`）

| # | 步骤 | 预期 |
|---|---|---|
| 4.1 | `gemini_generate_media(prompt="一只橘猫", media_type="image")` | 返回图片，文件落到 `generated_media/`，`Remote chat ID` 可解析 |
| 4.2 | `media_type="image", model="pro"` | 仍然走 Nano Banana 2（**这是网页规则，不是 bug**） |
| 4.3 | `gemini_generate_media(prompt="30秒海浪视频", media_type="video")` | 返回视频，最长 60s（Veo 3.1） |
| 4.4 | `gemini_generate_music(prompt="轻快钢琴", model="flash")` | 走 Lyria 3，返回音频文件 |
| 4.5 | `gemini_generate_music(prompt="交响乐", model="pro")` | 走 Lyria 3 Pro |
| 4.6 | `image_path="/abs/ref.jpg"` 作为参考图 | 不崩；如果上游不支持参考图，错误要明确 |

**关键校验**：
- 图像后端固定 Nano Banana 2，`pro` 不会换首轮——和 [live-ui-coverage.md](./live-ui-coverage.md) "Media Routing Notes" 一致
- 音乐按 `flash`/`pro` 分流 Lyria 3 / Lyria 3 Pro
- 文件实际落到磁盘，`generated_media/` 目录被 `.gitignore`
- 失败时返回**清晰的上游错误文本**，不能是空字符串（单元测试 `test_media_tool_returns_clear_upstream_failure` 验了形状，实机要验内容真实可读）

### 5. Deep Research（`gemini_deep_research`）

**前置**：账号必须有 AI Plus 订阅。

| # | 步骤 | 预期 |
|---|---|---|
| 5.1 | `gemini_deep_research(query="2026 年大模型发展趋势", model="flash", timeout_seconds=600)` | 走 `_run_native_deep_research` 路径，返回报告，含 `# Deep Research` 标题、`请求模型` 字段、引用来源 `[cite: N]` |
| 5.2 | 用 `model="pro"` 重复 | 走 native 路径；如果 `gemini-webapi` 不支持，回落到 `_run_fallback_deep_research`，返回里会写 "当前 gemini-webapi 客户端没有暴露完整研究轮询 API" |
| 5.3 | 故意设 `timeout_seconds=30` | 返回 `❌ Deep Research 超时（30秒）`，**不能把 start message 当成最终报告**返回（`test_deep_research_timeout_does_not_present_start_message_as_report` 验了形状） |
| 5.4 | 完成后调 `gemini_list_research_report_actions(chat_id=<上一步 cid>)` | 列出 webpage / infographic / quiz / flashcards / audio_overview / custom_app 等动作 |
| 5.5 | `gemini_create_from_research_report(chat_id, artifact_type="webpage", output_dir=/tmp/dr-test)` | 在 output_dir 生成 HTML，HTML 含报告正文和来源链接，`rel="noopener noreferrer"` |

**关键校验**：
- `query` 里要带 `Requested MCP model alias: xxx`（便于事后审计）
- 完成后 `gemini_history(action="read", chat_id=<cid>)` 能读到同一份报告
- `retain_chat=false` 时，cleanup 后台任务会删掉远端 chat；`retain_chat=true` 时保留

### 6. 文件上传与 URL 分析

| # | 步骤 | 预期 |
|---|---|---|
| 6.1 | `gemini_upload_file(file_path="/abs/cat.jpg", message="描述这张图")` | Gemini 识别图片并描述 |
| 6.2 | `file_path` 指向 PDF | 正常解析 |
| 6.3 | `file_path` 指向不存在的文件 | **在 client 初始化前就报错**（`test_file_validation_runs_before_client_initialization` 验了路由，实机要验顺序） |
| 6.4 | `gemini_analyze_url(url="https://example.com", message="总结这个网页")` | 返回网页内容摘要 |
| 6.5 | `url` 是 YouTube 链接 | 返回视频内容分析 |

---

## P1 — 账号与历史管理（`GEMINI_TOOLS=all`）

### 7. 模型发现

| # | 步骤 | 预期 |
|---|---|---|
| 7.1 | `gemini_list_models` | 返回当前账号**实际可见**的模型，不是硬编码列表（`test_model_listing_prefers_runtime_registry` 验了优先级） |
| 7.2 | 切换到没开通 Pro 的账号 | `gemini_list_models` 不应包含 Pro 模型 |

### 8. 历史对话（read/search/scan/export/delete）

**前置**：先在 Gemini Web 手动造几条聊天，其中至少一条标题含 marker `manual-test-history`。

| # | 步骤 | 预期 |
|---|---|---|
| 8.1 | `gemini_history(action="list", limit=10)` | 列出最近 10 条，分页元数据完整 |
| 8.2 | `gemini_history(action="search", query="manual-test-history")` | 默认只匹配标题，**不读正文**（隐私）；找到 marker 聊天 |
| 8.3 | `gemini_history(action="search", query="正文里的某句话", scan_turns=true)` | 这次读正文，返回截断片段 |
| 8.4 | `gemini_history(action="scan")` | 合并 `MaZiqc`、notebook、`GS7W1` 来源，不读 turn 正文 |
| 8.5 | `gemini_history(action="read", chat_id=<某条>)` | 返回完整 turn 列表（私密文本，要确认用户意图） |
| 8.6 | `gemini_history(action="export", chat_id=<某条>, response_format="json")` | 返回 JSON，结构完整 |
| 8.7 | `gemini_delete_chat(chat_id=<测试 marker 聊天>)` | 删除成功；再 `gemini_history(action="search", query="manual-test-history")` 应返回 `match_count=0` |
| 8.8 | 删除后调 `gemini_search_chats(query=marker)` | `match_count=0`（参照 [live-ui-coverage.md](./live-ui-coverage.md) 2026-06-20 E2E smoke test 的契约） |

**关键校验**：
- `search` 默认 `scan_turns=false` 是隐私默认（单元测试 `test_chat_search_defaults_to_metadata_without_reading_turns` 验了形状）
- `delete_chat` 是 destructive，必须有 `destructiveHint=True`（已验）
- 删除是**不可逆**的，只能用测试 marker 聊天

### 9. Notebook

| # | 步骤 | 预期 |
|---|---|---|
| 9.1 | `gemini_notebooks(action="list")` | 列出原生 Notebook |
| 9.2 | `gemini_notebooks(action="chats", notebook_id=<某 id>)` | 列出该 notebook 下的对话 |
| 9.3 | `gemini_move_chat_to_notebook(chat_id=<测试聊天>, notebook_id=<目标>)` | 移动成功，返回里带 verification；再 `notebooks(action="chats")` 应能找到该 chat |
| 9.4 | 不存在的 `notebook_id` | 明确错误 |

### 10. 账号盘点（`gemini_account_inventory`）

逐个 surface 跑一遍，验证返回不为空且结构完整：

| # | surface | 预期 |
|---|---|---|
| 10.1 | `capabilities` | 返回 `gemini_get_web_capabilities` 的内容：模型、思考等级、菜单、设置入口 |
| 10.2 | `status` | `gemini_inspect_account` 内容，无原始 RPC preview |
| 10.3 | `features` | `gemini_probe_web_features` 内容，**不返回 raw response body**（`test_web_feature_probe_uses_observed_rpc_shapes_without_raw_response` 验了形状） |
| 10.4 | `links` | `gemini_list_public_links` 内容 |
| 10.5 | `usage` | `gemini_get_usage_limits` 内容，含 quota / model_state |
| 10.6 | `library` | `gemini_list_library_capabilities` 内容 |
| 10.7 | `notebooks` + `notebook_chats` | 同 #9 |
| 10.8 | `scheduled` | `gemini_list_scheduled_actions` 内容 |
| 10.9 | `modes` | `gemini_get_tool_mode_status` 内容 |
| 10.10 | `models` | 同 #7 |

### 11. 定时操作（create/get/list/delete）

**这是最容易出"RPC 接受但 registry 不落库"问题的地方**，必须实机验证。

| # | 步骤 | 预期 |
|---|---|---|
| 11.1 | `gemini_create_scheduled_action(prompt="manual-test-scheduled 每日问候", schedule="daily")` | 返回 task id，`task_state="created"`，`verification_status` 非 unknown |
| 11.2 | `gemini_get_scheduled_action(task_id=<上一步>)` | 立即可读，`task_state="created"`（参照 [live-ui-coverage.md](./live-ui-coverage.md) 2026-06-20 smoke test） |
| 11.3 | `gemini_list_scheduled_actions()` | 新建的 task 应出现（**注意**：某些 cookie/profile 上下文下 registry 一直为空，这是已知现象，要记下来作为诊断而不是 bug） |
| 11.4 | `gemini_delete_scheduled_action(task_id)` | `task_state="deleted"`，`verification_status="deleted_state_by_id"`，`deleted_by_id_after_delete=true` |
| 11.5 | 删除后再 `gemini_get_scheduled_action(task_id)` | 返回 tombstone 状态（`Rg=6` / `Deleted`），不是 404 |

**关键校验**：
- `verification_status` 必须区分 "RPC 已接受" / "registry 已验证" / "deleted tombstone"
- 如果 create 返回 id 但 `list` 看不到，要在返回里**显式诊断**，不能假装成功

### 12. 清理工具（`gemini_cleanup_test_artifacts`）

| # | 步骤 | 预期 |
|---|---|---|
| 12.1 | 先造几条 `codex-test-` 前缀的聊天和定时任务 | 远端确实存在 |
| 12.2 | `gemini_cleanup_test_artifacts(markers="codex-test-", dry_run=true)` | 列出将被删除的 ID，**不实际删** |
| 12.3 | `dry_run=false` 重复 | 实际删除；再 `dry_run=true` 应返回空列表 |

---

## P2 — 边界与回归（按需）

### 13. Cookie 多 profile

| # | 步骤 | 预期 |
|---|---|---|
| 13.1 | 多账号 Chrome，`gemini_list_browser_cookie_profiles` | 列出所有 profile，`has_psid` 字段区分 |
| 13.2 | `gemini_get_cookie_from_browser(profile="Profile 1")` 后 `gemini_doctor` | Cookie 切换成功，scheduled registry 可见性可能变化 |
| 13.3 | 指定不存在的 profile | 明确错误 |

### 14. 代理与网络

| # | 步骤 | 预期 |
|---|---|---|
| 14.1 | `GEMINI_PROXY=http://127.0.0.1:1`（不可达） | **被忽略**，正常初始化（`test_client_wrapper_ignores_stale_local_proxy` 验了形状，实机要验真的不连代理） |
| 14.2 | `GEMINI_PROXY=http://valid-proxy:port` | 走代理 |
| 14.3 | `GEMINI_AUTO_REFRESH=false` + 过期 token | 卡住或报错；改回 `true` 后台刷新 |

### 15. 远端聊天清理

| # | 步骤 | 预期 |
|---|---|---|
| 15.1 | `gemini_chat(..., retain_chat=false, delete_after_seconds=5)` | 5 秒后远端 chat 被删 |
| 15.2 | `retain_chat=true` | 保留，`gemini_history(action="read")` 能读到 |
| 15.3 | 手动触发 cleanup（看日志） | 过期的 chat 被删，未过期的不动 |

### 16. Stream 协议

| # | 步骤 | 预期 |
|---|---|---|
| 16.1 | `gemini_chat_stream` 看日志 | 走 StreamGenerate，payload 含 text delta |
| 16.2 | `thinking_level="extended"` 时 stream | payload 含 thinking scope 扩展字段（`test_thinking_level_transport_extends_stream_generate_payload` 验了形状） |
| 16.3 | `learning_mode="flashcards"` 时 stream | payload 含 `X9b` / `GOa` 字段（`test_web_request_transport_injects_learning_companion_fields` 验了形状） |

### 17. Prompts 管理（`GEMINI_TOOLS=prompts`）

| # | 步骤 | 预期 |
|---|---|---|
| 17.1 | `gemini_manage_prompts(action="create", name="测试", content="...", category="test")` | 创建成功，返回完整 ID（不是截断的） |
| 17.2 | `action="list"` | 列出，含完整 ID（`test_prompt_list_exposes_full_id_for_cleanup` 验了形状） |
| 17.3 | `action="delete", id=<上一步>` | 删除成功 |
| 17.4 | 确认 `prompts.json` 被 `.gitignore` | 不进 Git |

---

## P3 — Skill 与分发（发布前）

### 18. Skill 校验

| # | 步骤 | 预期 |
|---|---|---|
| 18.1 | `skills-ref validate .agents/skills/gemini-web-mcp` | 通过 |
| 18.2 | `skills-ref validate .agents/skills/mcp-builder` | 通过 |
| 18.3 | `skills-ref validate .agents/skills/python-mcp-server-generator` | 通过 |
| 18.4 | `.codex/skills/gemini-web-mcp` 与 `.agents/skills/gemini-web-mcp` 内容一致 | `test_public_repo_skill_matches_local_project_skill` 通过 |

### 19. 打包

| # | 步骤 | 预期 |
|---|---|---|
| 19.1 | `python -m build` | 生成 wheel + sdist |
| 19.2 | `python scripts/package_release.py` | 生成 Codex skill zip |
| 19.3 | 解压 wheel，检查含 `README.zh-CN.md` 和 `docs/assets/gemini-web-mcp-banner.svg` | 都在 |
| 19.4 | `pip install dist/*.whl` 到干净 venv，`python -m src.server` 能起 | 启动正常 |

### 20. Evaluation

| # | 步骤 | 预期 |
|---|---|---|
| 20.1 | `pytest tests/test_evaluations.py -v` | 2 个测试通过，17 个 QA 答案与 manifest 一致 |
| 20.2 | 人工读 [evaluations/gemini_web_mcp_contract.xml](../evaluations/gemini_web_mcp_contract.xml) | 17 个 qa_pair，问题/答案与当前工具面一致 |

---

## 测试结果记录模板

每次实机测试建议在 PR 或 release notes 里贴这个：

```markdown
## 实机测试结果

**账号**：[测试账号类型：免费 / AI Plus]
**日期**：YYYY-MM-DD
**gemini-webapi 版本**：x.y.z
**GEMINI_TOOLS**：all

### P0
- [ ] 1. 鉴权（5 项）
- [ ] 2. 单轮对话（8 项）
- [ ] 3. 多轮会话（7 项）
- [ ] 4. 媒体生成（6 项）
- [ ] 5. Deep Research（5 项，需 AI Plus）
- [ ] 6. 文件上传与 URL（5 项）

### P1
- [ ] 7. 模型发现（2 项）
- [ ] 8. 历史对话（8 项）
- [ ] 9. Notebook（4 项）
- [ ] 10. 账号盘点（10 项）
- [ ] 11. 定时操作（5 项）
- [ ] 12. 清理工具（3 项）

### P2 / P3（按需）
- [ ] 13-20 略

### 发现的问题
1. ...
2. ...

### 已知现象（不是 bug）
1. 某些 cookie/profile 上下文下 scheduled registry 一直为空（见 [live-ui-coverage.md](./live-ui-coverage.md) 2026-06-20 记录）
2. ...
```

---

## 单元测试 vs 实机测试 边界

明确什么不用实机测（已被单元测试覆盖）：

| 已被单元测试覆盖 | 不要重复实机测 |
|---|---|
| 工具注册名、唯一性 | ✓ |
| MCP annotations（`readOnlyHint` / `destructiveHint` / `openWorldHint`） | ✓ |
| `account` / `scheduled` / `session` dispatcher 路由 | ✓ |
| 参数校验在 client 初始化前发生 | ✓ |
| `parse_response` 提取 `Remote chat ID` 的格式 | ✓ |
| Deep Research native vs fallback 路径选择 | ✓ |
| 媒体后端路由（flash→Lyria 3，pro→Lyria 3 Pro）的**形状** | ✓ |
| `learning_mode` 注入 `X9b`/`GOa` 字段的**形状** | ✓ |
| cleanup helper 的 retain_chat / delete_after 逻辑 | ✓ |
| cookie manager 的 profile 选择逻辑 | ✓ |
| prompts ID 完整性 | ✓ |

实机测试关注的是：**远端真的接受这些 payload 吗？返回的真的是文档说的格式吗？错误真的可读吗？cleanup 真的删了吗？**

---

## 相关文档

- [docs/live-ui-coverage.md](./live-ui-coverage.md) — 现网 UI 实测记录，是本清单的 RPC 证据来源
- [docs/tools.md](./tools.md) — 工具参数完整说明
- [docs/troubleshooting.md](./troubleshooting.md) — 实机测试中遇到问题的排查
- [docs/faq.md](./faq.md) — 概念性问题
- [evaluations/gemini_web_mcp_contract.xml](../evaluations/gemini_web_mcp_contract.xml) — 只读 QA 评估
- [AGENTS.md](../AGENTS.md) — 仓库安全与构建规范
