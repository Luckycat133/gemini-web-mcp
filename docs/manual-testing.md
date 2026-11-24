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
| 修改 `services/chat.py` 或任一聊天适配器 | P0 单轮对话 + 多轮会话，并各抽查一次 primary/compact |
| 修改 `infrastructure/rpc_contracts.py`、`rpc_parsers.py` 或管理 service | 受影响的 P1 RPC 工具，并抽查 primary/compact 一致性 |
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

6. **测试 marker 与 ID 约定**：所有测试产生的聊天/定时任务用 `codex-test-` 或 `manual-test-` 前缀，同时立即记录工具返回的完整远端 ID。Gemini 自动标题可能不保留 prompt marker，因此 ID 是主清理路径，marker 只用于 bounded fallback

---

当前默认入口各有六个工具，先从功能面的 `gemini_ask`、`gemini_run_task`、`gemini_create_media` 和操作状态／结果验证正常路径，再按需检查 `function-full`、`account-full` 与旧 profile。下面的颗粒工具名称保留为兼容性回归项目。多参考图与源产物续编需覆盖上传顺序、实际合成画面、后续编辑的连续性以及原句柄恢复；代码离线通过不替代画面验收。[当前参数](api-reference.md#business-surfaces)

2026-11-22 的定向本地媒体测试已获用户明确授权使用 `default` 账号，与标准专用账号 canary 分开记录。2026-11-21 的[19格矩阵](live-model-matrix-2026-11-21.md)保持 13 项通过的历史结果；新的 Hydrogen／Neon／Carbon 音乐与视频六格、最终 wheel 和共享入口验收另行记录，未完成前不填通过数。

## P0 — 核心路径（必跑）

这些是项目的核心价值，任何一次都至少跑一遍。

### 1. 鉴权与客户端初始化

| # | 步骤 | 预期 |
|---|---|---|
| 1.1 | 调 `gemini_doctor` | `cookie_status.has_cookie=true`、`tool_surface` 列出期望分组、`media_dependencies` 无缺失 |
| 1.2 | 调 `gemini_get_cookie_status` | `status="ok"`、`source` 指向环境变量或浏览器 |
| 1.3 | 调 `gemini_list_browser_cookie_profiles` | 列出 Chrome profile，`Default` 与 `Profile 1` 等；`has_psid` 至少一个为 true |
| 1.3a | 在 macOS 拒绝/不响应 Keychain 授权并重试 profile list | 在 `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` 内返回 `BROWSER_COOKIE_ACCESS_TIMEOUT`；进程不挂起、响应无 Cookie 值 |
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
| 2.6 | `gemini_chat_stream(...)` 同样参数 | 一次性返回归一化正文；`domain_result.data.stream.delivery=collected`，累计/重复 chunk 不会重复文本 |
| 2.7 | `temporary=true` | 返回正常；调用 `gemini_list_chats` 不应看到这条 |
| 2.8 | `gem_id="<某个 Gem ID>"` | 用指定 Gem 的人格回答 |
| 2.9 | primary `gemini_chat` 与 compact `chat` 使用同一普通文本/模型调用 | 两者正文均正常，`_meta.domain_result` 的 model/backend/verification 语义一致；允许 request ID 不同 |

**关键校验**：返回里 `Remote chat ID: c_xxx` 必须能解析并立即记录（用于 cleanup）。`test_parse_response_exposes_remote_chat_id_for_cleanup` 只验了格式，实机要验 ID 真的能在 `gemini_delete_chat` 里删掉并得到 `verified_absent`；不要假设 Gemini 自动标题会保留 prompt marker。

### 3. 多轮会话（`gemini_start_chat` / `gemini_send_message` / 流式版本）

| # | 步骤 | 预期 |
|---|---|---|
| 3.1 | `gemini_start_chat(model="flash")` → 拿 `session_id`（形如 `sess_<32位十六进制 UUID>`） | 返回成功文本和不透明的本地会话 ID |
| 3.2 | `gemini_send_message(session_id, "我叫张三")` | 正常回复 |
| 3.3 | `gemini_send_message(session_id, "我叫什么名字？")` | 回复含"张三"，证明上下文保留 |
| 3.4 | `gemini_send_message_stream(...)` 同上 | 一次性返回归一化正文和 `stream.delivery=collected` |
| 3.5 | `gemini_list_sessions` | 列出 3.1 返回的 ID，含 model 信息 |
| 3.6 | `gemini_reset_session(session_id)` 后再 `gemini_send_message` | 明确返回 `SESSION_NOT_FOUND`，不会悄悄创建新会话 |
| 3.7 | 保留一个有效会话，再用 `session_id="sess_invalid"` 发送或重置 | 返回 `SESSION_NOT_FOUND`，有效会话仍可继续使用 |
| 3.8 | primary 的 `gemini_start_chat` 创建后，从 compact `session(action="list"/"send")` 使用同一 ID；再反向测试 | 两个入口看到并操作同一份状态 |
| 3.9 | compact `session(action="reset")` 不传 ID，再调用 `session(action="reset_all")` | 前者返回 `INVALID_ARGUMENT` 且不清状态；后者才清空全部并重置客户端 |
| 3.10 | 用 MCP Inspector 查看 3.1–3.9 的第一条 `TextContent._meta.domain_result` | 有 `ok/data/error/warnings/meta`；正文仍与旧版本兼容 |
| 3.11 | primary 和 compact 分别访问同一个不存在的 ID | 两边 `error.code=SESSION_NOT_FOUND`、`retryable=false`、`operation_state=failed`，不根据正文或 emoji 判断 |
| 3.12 | 提供无效图片路径，再模拟一次上游超时 | 分别得到 `INVALID_ARGUMENT/failed` 与 `TIMED_OUT/timed_out`；超时的 `retryable=true` |
| 3.13 | 触发一个可控的上游异常并查看 stderr | 返回 `diag_<uuid>`，日志中同一行可找到 request/diagnostic ID；不要记录或回传 Cookie |

**关键校验**：session 是**本地概念**，`sess_<uuid>` 不等于 Gemini 的 `cid`。primary 与 compact 共用同一生命周期服务。单会话 reset 只删除指定本地状态；默认 `retain_chat=false` 时还会尝试立即删除对应远端聊天，`retain_chat=true` 时保留远端聊天。要清空全部本地会话并重置底层连接，primary 用 `gemini_reset`，compact 必须显式用 `action="reset_all"`。结构化断言读取 `_meta.domain_result`，不得以 emoji 或自然语言作为程序分支条件。

### 4. 媒体生成（`gemini_generate_media` / `gemini_generate_music`）

| # | 步骤 | 预期 |
|---|---|---|
| 4.1 | `gemini_generate_media(prompt="一只橘猫", media_type="image")` | 返回图片，文件落到 `generated_media/`，`Remote chat ID` 可解析 |
| 4.2 | `media_type="image", model="flash-lite"` 且无基础 Flash 额度耗尽证据 | 生成前拒绝，不能为了验证故意耗尽账号额度；静态标签不证明图片后端 |
| 4.3 | 图片显式选择 `neon`；另选已从注册表消失的旧 `pro` | 可用 Neon selector 原样派发；不可用 Pro 生成前拒绝，不替换为 Flash／Hydrogen。Pro redo 是网页二次操作 |
| 4.4 | `gemini_generate_media(..., media_type="video")` | 仅在结构化结果包含可验证视频 Artifact 时通过；2026-10-31 的专用视频模式已返回 MP4；JPEG 缩略图不能代替视频 |
| 4.5 | `gemini_generate_music(prompt="轻快钢琴", model="flash")` | 返回可验证音频或封面视频文件；无上游版本证据时 `observed_backend` 留空，不把 Lyria 标签当作实际版本 |
| 4.6 | `gemini_generate_music(prompt="交响乐", model="neon")`，账号目录与套餐允许时 | 精确 Neon selector 派发并验证实际产物；旧 Pro 缺席时在派发前拒绝，不从 `thinking_level` 推断后端或曲长 |
| 4.7 | `image_path="/abs/ref.jpg"` 作为参考图 | 不崩；如果上游不支持参考图，错误要明确 |
| 4.8 | 用 MCP Inspector 查看一次成功图片/音乐结果的 `_meta.domain_result.data` | `state=remote` 或 `local`；`artifacts[].id/uri/kind` 完整，primary 与 compact 对同一 URI 的 ID 一致 |
| 4.9 | 指定 `output_dir` 保存产物并检查对应 artifact | 文件真实存在且非零，`verification.status=verified`，MIME/bytes 与磁盘一致；图片有尺寸、音视频在 ffprobe 可用时有时长 |
| 4.10 | 观测一次上游 queued/empty，或用离线 fixture 重放 | queued 为 `ok=true/operation_state=queued`；empty 为 `ARTIFACT_NOT_RETURNED`，两者不混淆 |
| 4.11 | 使用不可写输出目录测试一个仍返回远端 URI 的请求 | 远端 artifact 保留，结果为 `partial` 并带 `ARTIFACT_SAVE_PARTIAL`；不得宣称本地保存成功 |
| 4.12 | 指定 `filename="image.png"`，上游实际返回 JPEG 字节 | 落盘后缀调整为 `.jpg`，结构化 `mime_type=image/jpeg`，与文件签名一致 |
| 4.13 | 指定已有文件名或 `filename="../escape.png"` | 已有文件不被覆盖；路径型文件名在请求前返回 `INVALID_ARGUMENT` |
| 4.14 | 媒体结果为 queued/empty 且含上游聊天 ID | 原句柄继续观察，不重复生成；原生媒体在身份、产物或独立清理证据不足时保留保护，失败不等于已清理 |
| 4.15 | 默认原生路线分别选择 Hydrogen／Neon／Carbon，各运行一次音乐和视频 | 六个不同组合独立记录选择器、单次发送、CID、精确 RID／candidate、产物和清理；不以某一格成功替代全部格子 |
| 4.16 | 原生浏览器注入 Cookie 后核查实际鉴权 | 对照 Chrome 元数据保留 JS 鉴权 Cookie 属性；PSID相等或页面打开不替代已登录历史与同 CID 读回。不要一律强制 HttpOnly |
| 4.17 | 填写提示后再选择模型，检查视频格式与禁用项 | 发送前核验已选模型、媒体入口和选项；格式触发器可显示「格式，视频」，未选禁用项不影响已选值判断；无法核验则不发送 |
| 4.18 | 原生音乐只有 MP4，或视频只有 JPEG 预览 | MP4须有实际音轨才可作音乐产物；不要求必有 MP3，也不将无音轨封面或 JPEG 当作完整媒体 |
| 4.19 | 在只持久化 CID 后断开，再读原句柄 | 保持 `native_media_submitted` 与清理保护；仅恢复同对话的回复，不重发。completed 重启须重验已有文件后再清理 |
| 4.20 | 检查原生回复、产物和清理证据链 | 精确 CID／RID／candidate 完成证据 → 保存并核验本地文件 → 释放操作保护 → 既有 canonical 精确删除／独立读回 |
| 4.21 | 保存前缺少 RID／candidate、恢复时文件损坏、改为保留或失去 cleanup lease | 未知身份不释放保护；结果恢复须重验大小／哈希，账号、保留与 schedule／lease 规则继续生效，不把 guest 缺少历史当已删除 |
| 4.22 | 新建连接读取共享 CLI，检查旧账本／去重／租约 | 以最终同一 wheel 验证源码一致性、工具目录和实际进程；旧 SHA／PBKDF绑定仅限同凭据，既有动作不能重复执行，不重建状态库 |
| 4.23 | 用户明确要短纯音乐时传 `music_duration="short", music_vocals="instrumental"` | 发前读回选项匹配；`None`保留实际网页默认，不能按prompt或首项猜默认。`standard`、`vocals`／`auto`也逐项核验；仅音乐接受，显式SDK路线带选项预提交拒绝 |
| 4.24 | 兼容主入口／compact 原生 `background=false` 分别模拟超时、未知、保存失败和完成 | 只提交一个受保护操作；超时返回原句柄与 partial／wait_elapsed，未知或保存失败不清理。完成兼容 local、显式文件名与 generated_media 默认目录；默认名称不含 prompt，账本无 prompt；SDK／API 同步合同不变 |
| 4.25 | 原生视频传一张 `image_path`，在 Send 前改变、移除或无法识别上传附件 | 上传预览 token、账号／模型／模式／格式均核验后才单次发送；附件缺失、变化或未知时不发送、不忽略参考图。DOM 上传不等于参考视频成功，端到端实测另记 |
| 4.26 | 原生 exact read 正在 await 时切换账号，再返回新 RID／candidate | 等待后先复核原账号／凭据再 observe／checkpoint；拒绝新回复身份与产物，最多保留原 CID，保持保护，不清理或重发 |

**关键校验**：
- 图片默认选择发现的 Hydrogen，显式可用选择保留；`observed_backend=null` 时不能从路由标签断言 Nano Banana 版本
- 音乐生成器只报告上游明确提供的家族／版本；带已验证音轨的 MP4 可作为音乐产物，不保证 MP3
- 检查视频专用模式的实际视频 Artifact；仅有缩略图或文本时保留失败状态，避免重复发起生成
- 文件实际落到磁盘，`generated_media/` 目录被 `.gitignore`
- 远端 URI 只标记 `unverified`；只有本地文件存在且非零才标记 `verified`
- `requested_model`、`request_model`、`effective_backend`、`observed_backend` 分开记录；没有观测证据时 `observed_backend=null`
- 失败时返回**清晰的上游错误文本**，不能是空字符串（单元测试 `test_media_tool_returns_clear_upstream_failure` 验了形状，实机要验内容真实可读）

2026-11-22 修复 HttpOnly 属性后，早前 Hydrogen／Carbon 原生音乐原句柄恢复保存了独立验证的 MP3／含音轨 MP4，并完成清理。Neon 默认设置一轮无音频，另有 Short＋Instrumental 浏览器播放器对照，不能据此声称 MCP 音乐全部通过。此前受保护的 Hydrogen 视频／Neon 音乐已由最终包按原句柄分别读回 `UPSTREAM_REJECTED`／`ARTIFACT_NOT_RETURNED`，清理均 completed；不计入新 cohort。原生媒体 Auto 对照不扩展 MCP 支持范围。此前 guest CID 未持久化不证明存储隔离，GraphQL 实验适配已移除。

19 项 cohort 的 V5 wheel 为 `ba8e0ab20c1bf3b42d1dc5dcdbfed733e3126191952a3e1bdbd9291d25e0a041`，该轮共享 CLI 指向 `~/.local/share/gemini-web-mcp/releases/ba8e0ab20c1bf3b4`。构建快照验证了 142 个源码／data 文件逐字节一致、47 个安装 distributions 中 46 个固定依赖零差异；4,628 项离线／2,316 项合同、Ruff、Mypy（151 个源文件）、清洁 wheel 六入口、current／legacy 协议、updater 和 profile 检查通过。19 条本地观察已结束，严格为 12 通过、1 保存产物链未验证、6 调用错误。三个 Task 初始清理失败后均已自动重试 completed，并独立确认三个 Goal／对话已缺席，零次修改。Neon 本地控制图编辑不算保存产物续编；无身份音乐超时的上游结果保持未知。逐项结果见 [11-22 报告](live-model-matrix-2026-11-22.md)。

后续[独立补测与最终交付](live-model-followup-2026-11-22.md)验证 Neon 真保存产物链、Carbon 单参考图同步视频／非静音音乐；最终 fcdf 将 Hydrogen 音乐原句柄分类为明确拒绝并完成清理，未重发生成。六个精确目标的只读 canonical 缺席检查均通过、零次修改；四个无身份视频保持保护，补测库 live worker 为 0。原八项、两个顺序独立尝试及同句柄读回按 round／attempt 分开，App 旧连接重载仍未验证。

前轮 fcdf 一致性与 4,699 离线／2,522 合同等记录见[安装说明](source-update-integration.md)；后续候选与当前未决 High 修复见[新诊断](live-media-diagnosis-2026-11-22.md)。不能把历史门禁、源码 NoSend 或 App 旧连接问题当作最新安装版全部通过。

### 5. Deep Research（`gemini_deep_research`）

**前置**：账号必须有 AI Plus 订阅。

| # | 步骤 | 预期 |
|---|---|---|
| 5.1 | `gemini_deep_research(query="2026 年大模型发展趋势", model="flash", timeout_seconds=600)` | 走 `_run_native_deep_research` 路径，返回报告，含 `# Deep Research` 标题、`请求模型` 字段、引用来源 `[cite: N]` |
| 5.2 | 用 `model="pro"` 重复 | 走 native 路径；如果 `gemini-webapi` 不支持，回落到 `_run_fallback_deep_research`，返回里会写 "当前 gemini-webapi 客户端没有暴露完整研究轮询 API" |
| 5.3 | 故意设 `timeout_seconds=30` | 返回 `❌ Deep Research 超时（30秒）`，**不能把 start message 当成最终报告**返回（`test_deep_research_timeout_does_not_present_start_message_as_report` 验了形状） |
| 5.4 | `wait_for_completion=false, retain_chat=true` | 不调用 wait；按观察返回 `accepted` 或 `running`，并保留已观察到的 research/chat ID 和 continuation 状态 |
| 5.5 | 完成后调 `gemini_list_research_report_actions(chat_id=<上一步 cid>)` | 列出 webpage / infographic / quiz / flashcards / audio_overview / custom_app 等动作 |
| 5.6 | `gemini_create_from_research_report(chat_id, artifact_type="webpage", output_dir=/tmp/dr-test)` | 在 output_dir 生成 HTML，HTML 含报告正文和来源链接，`rel="noopener noreferrer"` |
| 5.7 | 检查 5.6 的 `_meta.domain_result.data.artifacts[0]` | `state=local`、`kind=webpage`、path/bytes/MIME 与磁盘一致，backend 为 MCP local renderer / filesystem |

**关键校验**：
- `query` 里要带 `Requested MCP model alias: xxx`（便于事后审计）
- `domain_result.meta.operation_state` 与 `data.state` 一致；超时仍保留已观测到的上游 ID
- 显式 `retain_chat=true` 时可用 `gemini_history(action="read", chat_id=<cid>)` 核对报告；默认清理后不能再次远端读取
- `retain_chat=false` 时，cleanup 后台任务会删掉远端 chat；`retain_chat=true` 时保留

### 6. 文件上传与 URL 分析

| # | 步骤 | 预期 |
|---|---|---|
| 6.1 | `gemini_upload_file(file_path="/abs/cat.jpg", message="描述这张图")` | Gemini 识别图片并描述 |
| 6.2 | `file_path` 指向 PDF | 正常解析 |
| 6.3 | `file_path` 指向不存在的文件 | **在 client 初始化前就报错**（`test_file_validation_runs_before_client_initialization` 验了路由，实机要验顺序） |
| 6.4 | `gemini_analyze_url(url="https://example.com", message="总结这个网页")` | 返回网页内容摘要 |
| 6.5 | `url` 是 YouTube 链接 | 返回视频内容分析 |
| 6.6 | 检查 6.1/6.2 的 `_meta.domain_result.data.input_artifacts[0]` | 本地文件为 `local/verified`，绝对路径、MIME、bytes 与磁盘一致 |
| 6.7 | 检查 6.4 的 input artifact | URL 为 `kind=webpage,state=remote,verification=unverified`；响应图片（如有）位于 `artifacts` |
| 6.8 | 人为触发文件/URL 分析超时 | `error.code=TIMED_OUT`、`data.state=failed`，输入 artifact 身份仍保留 |

---

## P1 — 账号与历史管理（`GEMINI_TOOLS=all`）

### 7. 模型发现

| # | 步骤 | 预期 |
|---|---|---|
| 7.1 | `gemini_list_models` | 返回当前账号**实际可见**的模型，不是硬编码列表（`test_model_listing_prefers_runtime_registry` 验了优先级） |
| 7.2 | 切换到没开通 Pro 的账号 | `gemini_list_models` 不应包含 Pro 模型 |

### 8. 历史对话（read/search/scan/export/delete）

**前置**：先在 Gemini Web 手动造几条聊天，其中至少一条标题含 marker `manual-test-history`。
程序化生成的聊天还必须保存返回的 ID；2026-08-08 实机观察中，Gemini 自动标题没有保留 prompt marker。

| # | 步骤 | 预期 |
|---|---|---|
| 8.1 | `gemini_history(action="list", limit=10)` | 列出最近 10 条，分页元数据完整 |
| 8.2 | `gemini_history(action="search", query="manual-test-history")` | 默认只匹配标题，**不读正文**（隐私）；找到 marker 聊天 |
| 8.3 | `gemini_history(action="search", query="正文里的某句话", scan_turns=true)` | 这次读正文，返回截断片段 |
| 8.4 | `gemini_history(action="scan")` | 合并 `MaZiqc`、notebook、`GS7W1` 来源，不读 turn 正文 |
| 8.5 | `gemini_history(action="read", chat_id=<某条>)` | 返回完整 turn 列表（私密文本，要确认用户意图） |
| 8.6 | `gemini_history(action="export", chat_id=<某条>, response_format="json")` | 返回 JSON，结构完整 |
| 8.7 | `gemini_delete_chat(chat_id=<测试 marker 聊天>)` | 检查 `_meta.domain_result.data.verification`；只有 `verified_absent` / `deleted=true` 才可称删除成功，`not_available` 需继续搜索验证 |
| 8.8 | 删除后调 `gemini_search_chats(query=marker)` | `match_count=0`（参照 [live-ui-coverage.md](./live-ui-coverage.md) 2026-06-20 E2E smoke test 的契约） |
| 8.9 | 对同一 fixture 从 primary 与 compact 执行 list/search/read/export/delete | 两边 `domain_result.data` 语义一致；允许 request/observed 时间不同，兼容正文可不同 |

**关键校验**：
- `search` 默认 `scan_turns=false` 是隐私默认（单元测试 `test_chat_search_defaults_to_metadata_without_reading_turns` 验了形状）
- `delete_chat` 是 destructive，必须有 `destructiveHint=True`（已验）
- 删除是**不可逆**的，只能用测试 marker 聊天；`operation_state=accepted` 不等于已验证删除

### 9. Notebook

| # | 步骤 | 预期 |
|---|---|---|
| 9.1 | `gemini_notebooks(action="list")` | 列出原生 Notebook |
| 9.2 | `gemini_notebooks(action="chats", notebook_id=<某 id>)` | 列出该 notebook 下的对话 |
| 9.3 | `gemini_move_chat_to_notebook(chat_id=<测试聊天>, notebook_id=<目标>)` | 移动成功，`verification_status="verified"`；再 `notebooks(action="chats")` 应能找到该 chat |
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
| 11.1 | `gemini_create_scheduled_action(title="manual-test-scheduled", instructions="每日问候", hour=9)` | 返回 task id，`verification_status` 明确区分 registry/GetTask 读回结果 |
| 11.2 | `gemini_get_scheduled_action(task_id=<上一步>)` | 立即可读，`task_state="created"`（参照 [live-ui-coverage.md](./live-ui-coverage.md) 2026-06-20 smoke test） |
| 11.3 | `gemini_list_scheduled_actions()` | 新建的 task 应出现（**注意**：某些 cookie/profile 上下文下 registry 一直为空，这是已知现象，要记下来作为诊断而不是 bug） |
| 11.4 | `gemini_delete_scheduled_action(task_id)` | `task_state="deleted"`，`verification_status="deleted_state_by_id"`，`deleted_by_id_after_delete=true` |
| 11.5 | 删除后再 `gemini_get_scheduled_action(task_id)` | 返回 tombstone 状态（`Rg=6` / `Deleted`），不是 404 |
| 11.6 | primary 与 compact `scheduled(action="create"/"delete")` 各做一次 | 相同上游状态得到相同 `verification_status`；无 body 时为 `rpc_unconfirmed` |

**关键校验**：
- `verification_status` 必须区分 "RPC 已接受" / "registry 已验证" / "deleted tombstone"
- 如果 create 返回 id 但 `list` 看不到，要在返回里**显式诊断**，不能假装成功

### 11A. Gems mutation 读回

只使用专用测试 Gem，完成后删除。

| # | 步骤 | 预期 |
|---|---|---|
| 11A.1 | `gemini_manage_gems(action="create", name="manual-test-gem", ...)` | 返回 Gem ID，读回校验为 `verified`；若列表暂未出现则明确为 `read_back_not_observed` |
| 11A.2 | `action="update"` 修改名称/描述/指令 | 读回字段一致为 `verified`，字段不一致为 `read_back_mismatch` |
| 11A.3 | `action="delete"` | 列表中消失为 `verified_deleted`；仍存在为 `still_present` |

### 12. 清理工具（`gemini_cleanup_test_artifacts`）

| # | 步骤 | 预期 |
|---|---|---|
| 12.1 | 先造几条 `codex-test-` 前缀的聊天和定时任务，并记录每个返回 ID | 远端确实存在；ID 不依赖自动标题 |
| 12.2 | 对已知聊天 ID 调 `gemini_delete_chat` | 每条都返回 `verified_absent`；其他验证状态继续重试或诊断 |
| 12.3 | `gemini_cleanup_test_artifacts(markers="codex-test-", dry_run=true)` | 预览标题/ID marker leftovers，**不实际删**；自动标题未保留 marker 时允许 0 命中 |
| 12.4 | 对唯一 marker 显式 `dry_run=false`，再用 dry-run 和 metadata search 复查 | 删除剩余命中项；最终 marker 0 命中且无错误 |

不要为了兜底清理默认开启 `scan_turns=true`。它会读取最近聊天正文，只能在用户明确允许正文扫描且 ID
路径不足时使用。

---

## P2 — 边界与回归（按需）

### 13. Cookie 多 profile

| # | 步骤 | 预期 |
|---|---|---|
| 13.1 | 多账号 Chrome，`gemini_list_browser_cookie_profiles` | 列出所有 profile，`has_psid` 字段区分 |
| 13.2 | `gemini_get_cookie_from_browser(profile="Profile 1")` 后 `gemini_doctor` | Cookie 切换成功，scheduled registry 可见性可能变化 |
| 13.3 | 指定不存在的 profile | 明确错误 |
| 13.4 | macOS 将 `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS=0.1` 并不处理 Keychain 提示 | 快速返回脱敏 timeout；恢复正常超时值后后续调用仍能工作 |

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
| 18.3 | `.agents/skills` 中的仓库 skill 名称唯一，无 `.codex` 重复来源 | `test_project_skill_names_are_unique_across_discovery_roots` 通过 |

### 19. 打包

| # | 步骤 | 预期 |
|---|---|---|
| 19.1 | `python -m build` | 生成 wheel + sdist |
| 19.2 | `python scripts/package_release.py` | 生成 Codex skill zip |
| 19.3 | 解压 wheel，检查 `src/data/prompts_default.json` 和两个 `console_scripts` | 包内资源与主/精简入口都在 |
| 19.4 | wheel 安装到干净 venv，运行 `pip check`、`scripts/smoke_installed_wheel.py`、`scripts/smoke_profiles.py` 和 `scripts/smoke_mcp_protocol.py` | 两套入口可启动，代表 profile 工具名精确匹配，auto/discover 与 legacy/initialize 均能真实 list/call 并返回结构化结果，且未从源码树误导入 |

### 20. Evaluation

| # | 步骤 | 预期 |
|---|---|---|
| 20.1 | `pytest tests/test_evaluations.py -v` | 检查 QA 答案与当前 manifest 及证据约定一致 |
| 20.2 | 人工读 [evaluations/gemini_web_mcp_contract.xml](../evaluations/gemini_web_mcp_contract.xml) | 问题/答案与当前工具面及证据边界一致 |

### 21. 自动 Live Canary（专用账号、显式 opt-in）

`.github/workflows/live-canary.yml` 与日常 PR/release gate 分离，只运行 RPC registry 中的
21 个只读 capability probe。配置仓库变量、`gemini-live-canary` environment 和专用账号
secrets 后，可手动确认 `run_live`，也可启用每周 schedule。完整配置、脱敏字段和 issue
处理契约见 [live-canary.md](./live-canary.md)。

| # | 步骤 | 预期 |
|---|---|---|
| 21.1 | 不加 `--allow-live-account` 运行 `python scripts/run_live_canary.py --output /tmp/canary.json` | 退出非零、`status=not_run`，不初始化 Gemini client |
| 21.2 | 专用账号下执行 workflow | artifact 仅含 schema allowlist；无 Cookie、session ID、聊天正文、标题或 raw response |
| 21.3 | fixture 模拟 parser/envelope drift | 报告指出依赖版本、RPC/capability、parser stage；workflow 创建或更新固定标题 issue 后失败 |
| 21.4 | 后续 compatible run | workflow 评论恢复证据并关闭原 issue |

P2.2 实现阶段没有可用的专用账号，因此只完成离线 fixture/workflow 契约；不得把这次开发
记录为真实 Gemini Web 观测。

2026-08-08 后续定向实机结果见 [live-ui-coverage.md](./live-ui-coverage.md#2026-08-08-targeted-live-mcp-evidence)。
该结果是已授权的 bounded observation，不是专用账号全量 canary。

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
- [ ] 3. 多轮会话（9 项）
- [ ] 4. 媒体生成（10 项）
- [ ] 5. Deep Research（6 项，需 AI Plus）
- [ ] 6. 文件上传与 URL（8 项）

### P1
- [ ] 7. 模型发现（2 项）
- [ ] 8. 历史对话（8 项）
- [ ] 9. Notebook（4 项）
- [ ] 10. 账号盘点（10 项）
- [ ] 11. 定时操作（6 项）
- [ ] 11A. Gems mutation（3 项）
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
| 媒体请求模型、Artifact 状态与后端证据分离的**形状** | ✓ |
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

## 2026-10-31 授权本地功能观察

Primary 视频返回经验证的 10 秒 1280×720 MP4 和独立 JPEG 预览；primary 音乐返回经验证的 MP3/MP4；compact 图片编辑返回 JPEG。相关测试聊天自动删除。一次 Pro 研究通过同一持久句柄跨进程取回完整报告，核对 artifact 大小和哈希后自动删除并独立确认远端不存在。这些是授权本地定向测试，并非专用账号 canary；不能推断所有账号或后续调用稳定成功。后续 compact 视频已验证 10 秒 1280×720 MP4 及独立预览；compact 音乐已验证 MP3 音频、MP4 音视频及独立预览，两个测试对话自动删除并独立确认不存在。Flash-Lite 文本标记也已通过并完成清理。早期 Flash 与 Pro 搜索均为 `answer_only`。后续 Pro 受控请求观察到结构来源及其片段，片段在回答中与两组 offset 按 Unicode 码点及 UTF-16 均精确匹配；另一请求返回 `retrieved_only`，清理完成。最终修复后的真实 MCP Pro 搜索返回 `grounded`、1 条 `source_attribution`，片段与两组 offset 再次精确匹配，自动清理状态为 `completed`。匹配结果只证明该次响应的来源关联，不能推断每次请求均有来源。39 个只读盘点案例无 MCP/旧文本错误仅证明响应形状，不证明账号修改操作已验证；SDK 缓存状态标记为 `cached_unverified`。

另一次授权本地 Scheduled 合成条目测试完成创建、按 ID 读取和删除：创建后按 ID 可读但注册表为空，删除返回 `deleted_state_by_id` 且 `verified_deleted_state=true`。这只验证该合成条目的路径，不代表全部账号修改操作通过。

授权本地语义抽查：多轮 stream 保留随机标记，直接 read/export 和 history facade 均可读回；reset 后再次 reset 返回 `SESSION_NOT_FOUND`。附件中的随机标记被识别；不含颜色信息的随机文件名图片中左上蓝、右下黄均正确，mixed 输入的两个项目均被分析。URL 检查只验证响应提到 `example.com` 标题，不证明实时抓取。五个核心测试聊天和单独的图片测试聊天均经独立核验已不存在。

另一个唯一合成 Gem 完成创建、局部改名并验证指令/描述保留、指定 Gem 的固定标记对话、删除及新客户端读回确认不存在。只覆盖这个测试条目。

授权本地 Notebook 测试只移动了新建合成对话：移动 accepted 且回读 verified，同一 Notebook 随后确认存在；会话 reset 自动清理后，该 Notebook 再次读回确认条目不存在，独立 canonical 检查也确认远端对话不存在。未更改 Notebook 字段或其他对话。Notebook 列表遇拒绝、缺失或结构变化时应返回未验证，不能当空列表。

另一次授权本地报告测试使用 Gemini Web UI 启动的研究：SDK 从 running 观察到报告就绪，stdio actions 返回六项，六种本地渲染均生成非空、路径和大小经核验的文件，网页 HTML 签名也通过检查。`audio_overview` 是 Markdown 旁白稿，`custom_app` 是 Markdown 应用规格。随后读取报告完成默认清理，并独立确认远端测试对话不存在。此前一次 MCP Pro 启动并恢复报告的成功仍有效；近期五次 MCP 新建尝试出现 `plan_missing`，第六次在新保护逻辑下先返回 `partial`，随后用同一句柄恢复原计划，保持原 Pro、extended 与 `retain_chat=false` 并仅确认一次；再次使用同一句柄未重复确认。研究最终返回就绪报告，六项 actions 和六个本地产物均验证通过，`gemini_get_operation_result` 完成默认清理，独立 canonical 检查确认测试对话不存在。另一项预算研究随后也在原句柄上完成：六项 actions、六个本地产物均通过核验，取回报告时默认清理完成，独立 canonical 检查确认测试对话不存在。以上是授权本地定向证据，不代表 MCP 启动研究稳定可靠，也不是专用账号 canary。


### 后台任务与跨连接恢复

后续六次真实媒体提交覆盖图片、视频、音乐和图片编辑，均在一秒内返回操作句柄。每次提交后关闭原 stdio 连接，独立 worker 继续登记进度；新连接可用原句柄查询。五次观察到明确额度限制，另一次音乐返回明确无法提供音频的响应；修复后分别返回 `RATE_LIMITED` 或 `ARTIFACT_NOT_RETURNED`，不再变成参数错误或无限轮询。六个测试会话均自动清理，独立 canonical 检查确认六个全部不存在；未调用删除工具，也未重复发送生成或远端取消请求。

这轮受限调用没有生成新的媒体文件，不能作为后台成功生成的现网证据。离线集成另覆盖独立子进程在调用循环结束、临时工作目录删除后继续保存真实本地图像，SQLite 元数据恢复、过期租约接管、文件哈希变化、明确拒绝、延迟清理与重复取回。当前账号恢复生成额度后仍需补充后台成功生成的现网覆盖。


### 两类入口与清理覆盖检查

本轮授权本地检查通过了功能面和账号面的现代及旧版 MCP 协议工具发现，工具数分别为 17 和 11；未设置 `GEMINI_TOOLS` 的主入口路由到功能面。该检查不证明真实后台生成成功。

对此前登记的 67 个测试聊天做 fresh canonical 审计，均已缺席。原生网页另显示 3 个未入 ledger 的残留候选：两个视频测试及一个图片颜色理解测试。仅对这三个候选读取后，核对其与合成测试模板精确一致，且各为两轮、没有用户后续，再将其登记到当前账号的 MCP-owned ledger。通过新账号入口的 `gemini_cleanup` retry 删除后，三条均返回 `verified_absent`；随后独立 fresh canonical 读回和原生网页刷新均确认这三条已消失。

早期遗漏 CID 的覆盖缺口已修复：SDK 元数据从更早的流帧捕获，异常路径有回退，后台操作也会补入清理队列。ledger 审计只覆盖已登记条目，不能据此推断整个账号没有残留。以上是授权本地定向检查，不是 dedicated-account canary。

随后使用新功能入口真实提交图片，0.04 秒返回操作句柄；关闭提交 stdio 连接后，独立 worker 完成了可验证的本地 JPEG，视觉检查符合橙色三角形要求，重复获取仍为同一产物。另一个文本 nonce 与两轮会话记忆检查均通过。这三个新测试对话全部经独立远端读回确认不存在，测试未主动调用删除工具。该结果补充了新入口后台图片成功生成的实机证据，不代表视频、音乐的重复可靠性或专用账号 canary。

会话断开连接后，远端已独立确认删除，但有一条带租约的清理账本记录仍为 pending。随后只启动服务并保持维护循环，未调用清理/删除工具；约 470 秒后，维护过程自动完成读回并补记，最终该测试队列为 3 条 completed、0 条 pending、0 条 retained。这同时记录了租约恢复的实际等待时间，不应描述为退出进程后立即完成账本确认。

最终离线门禁：Ruff、Mypy、2,032 项全量测试（无警告）及 470 项契约检查通过。精确工具清单、12 次现代/旧版 stdio 握手、六个安装入口、Python 3.14 clean-wheel 与隔离 `uvx` 预检通过；77 个源代码 Python 文件与 wheel、干净安装的字节一致。此处是本地构建验证，未发布。

## 模型策略补充验证（2026-10-31）

- 无新鲜基础 Flash 额度证据时，显式 Lite、ID、别名、字典和 descriptor 都应在生成前被拒绝；认证、网络、扩展思考和媒体专用错误不能授权 Lite。
- 用离线真实 SDK 替身返回一次基础 Flash `UsageLimitExceededError`：原请求只派发一次。下一条新普通查询可使用 Lite，实际请求的强度固定为 SDK 默认值 1；返回实际模型、回退原因及 `thinking_selectable=false`、`effective_thinking_level=null`。
- 证据在 60 秒过期，或较新的基础 Flash 成功后撤销；其他账号、旧请求的并发成功及扩展思考/学习成功不能误改证据。显式 Pro/Argon 和已有会话不得自动改模型。
- 研究指定 Pro/Argon 而注册表仅有 Flash 时，应报告不可用，不能派发 Flash。空响应流应报告 `upstream_response_missing`，不能宣称已经收到回答或确认有效模型。
- 授权本地真实 stdio 拦截测试自动载入浏览器 Cookie，发现四个模型；无额度证据的 Lite 请求被拒绝，生成派发与新增清理记录都是 0。此探针在生成边界另设阻断，不主动耗尽额度，未验证真实耗尽后的成功降级。

对应回归在 `tests/test_model_policy.py` 和 `tests/test_model_policy_integration.py`；当前全量 2,187 项、契约 562 项、静态检查、精确工具清单和 12 次现代/旧版 stdio 握手通过。Skill 内保留社区原帖和场景限制，详细资料见[模型指南](models.md)与[社区证据](model-community-evidence.md)。
