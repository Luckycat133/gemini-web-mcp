# 工具质量与验证范围

本表覆盖 function 和 account 两个独立入口，各 17 个工具。`stdio` 负例使用无凭据、关闭浏览器自动加载与刷新、独立空状态库的 MCP 连接。本地读取使用空状态库。2026-11-06 通过现有 Chrome 登录会话复测的结果单独注明，范围限于所列合成场景。

2026-11-01 的 source MCP stdio 审核在无凭据、空状态目录和系统禁网沙箱中调用了 34 个工具：17 项空值语义负例、15 项 schema 类型拒绝、2 项本地读取。15 项 schema 拒绝均返回 MCP `isError`；17 项业务负例均返回结构化 `INVALID_ARGUMENT`。其中发送空会话消息的文本是概括性的“Invalid session request”，与错误分类一致，但未重复结构化错误的完整说明。无 traceback 或合成凭据片段。空会话列表成功；清理账本因无账户返回 `AUTH_REQUIRED`，并未读取任何账号账本。额外对非空 `gemini_ask` 设置生成方法派发陷阱：无账号时返回 `AUTH_REQUIRED`，陷阱未触发，隔离 SDK 缓存保持为空。测试文件列出相关回归，不表示每个测试都访问上游。上游观察见 [功能覆盖](web-feature-coverage.md)。

## Function

| 工具 | 作用 | 离线检查 | 相关测试 | 既有真实证据与限制 |
| --- | --- | --- | --- | --- |
| `gemini_ask` | 对话、联网信息和自然语言 App 请求 | 空 prompt；非空 prompt 无账号 `AUTH_REQUIRED`、无派发 | `test_assist_surface.py`, `test_search_service.py` | 11-06 能按准确 ID 回读请求及回复。Flash 合成问答未通过内容核验，算术对照返回重试提示，联网样本超时，普通 Notebook Chat 样本返回上游错误。 |
| `gemini_understand_image` | 单图理解 | 空 image 与不存在的本地路径负例 | `test_understanding_service.py` | 有历史真实图片理解。11-06 返回明确上游错误，测试聊天自动清理并经复查。共享服务现在保留失败结果。 |
| `gemini_understand` | 多输入理解 | 空 task 负例 | `test_understanding_service.py`, `test_assist_surface.py` | 历史 Flash 图片与文件混合请求识别了颜色和随机标记。11-06 返回明确上游错误，测试聊天自动清理并经复查。共享服务现在返回失败输入及清理状态。 |
| `gemini_research` | 生成研究计划并保留原操作 | 空 query 负例 | `test_research_identity.py`, `test_research_native_contract.py` | 11-06 的真实 Pro 调用从原句柄取回并核验完整 Markdown 报告，测试聊天经远端回读确认删除。 |
| `gemini_start_chat` | 创建会话 | model 类型负例 | `test_chat_service.py`, `test_chat_tools.py` | 合成会话已真实创建并使用。 |
| `gemini_send_message` | 会话续聊及原轮次恢复 | 空 message、空回复和超时恢复负例 | `test_session_recovery.py`, `test_chat_timeouts.py` | 此前 Flash 两轮取回了随机标记。11-06 复测遇到超时，同进程原回合读回未找到答案，测试会话已重置并核验清理。 |
| `gemini_list_sessions` | 列出本地会话 | 空会话读取 | `test_chat_session_lifecycle.py` | 本地状态工具；跨进程持久会话不在此结论内。 |
| `gemini_reset_session` | 重置单个本地会话 | session_id 类型负例 | `test_conversation_lifecycle.py` | 上述合成会话重置后独立确认远端不存在。 |
| `gemini_generate_image` | 后台生成图片 | 空 prompt 负例 | `test_media_jobs.py`, `test_media_tools.py` | 此前已保存并解码 Flash 图片。11-06 的首次请求收到上游失败，修复后用原句柄核实 `UPSTREAM_REJECTED` 和自动删除。 |
| `gemini_edit_image` | 后台编辑本地图片 | 空 prompt、独立不存在路径负例 | `test_media_jobs.py`, `test_media_tools.py` | 此前已保存并检查 Flash 合成编辑。11-06 复测的原回合核实为上游失败，测试聊天自动删除。 |
| `gemini_generate_video` | 后台生成视频 | 空 prompt 负例 | `test_media_generation.py`, `test_media_jobs.py` | 11-06 的网页「创建视频」返回可解码的 10 秒、720p 有声 MP4，自动删除经远端回读。Omni 1.2 Flash 版本来自 Google 的入口标注。 |
| `gemini_generate_music` | 后台生成音乐 | 空 prompt 负例 | `test_music_recovery.py`, `test_media_jobs.py` | 11-06 的 Flash 调用保存可解码 MP3 和含音视频轨的 MP4，另存封面，测试聊天独立核验已删除。 |
| `gemini_run_task` | 委托 Task 执行工作 | 空 prompt 负例 | `test_task_jobs.py`, `test_task_files.py` | 11-06 实测浏览器一次性许可在原 Task 续答并执行，另一个 Task 取回仅存在于指定 Notebook 来源中的标记。此前也验证过 CSV 附件保存与清理。 |
| `gemini_get_operation_status` | 查询已有操作 | 空 operation_id 负例 | `test_operations.py`, `test_operation_tools.py` | 媒体、Task 和研究原 handle 状态曾真实读取；不同操作状态分别判断。 |
| `gemini_get_operation_result` | 取回操作结果 | 空 operation_id 负例 | `test_operations.py`, `test_task_jobs.py` | 11-06 按原句柄取回真实研究报告、媒体和 Task 结果，Notebook 来源回答跨连接重读通过。 |
| `gemini_list_research_report_actions` | 查看已保存报告的派生动作 | 缺少报告身份负例 | `test_research_report_helpers.py` | 从真实本地研究报告按原句柄列出六种派生动作。 |
| `gemini_create_from_research_report` | 从已保存报告生成本地产物 | 缺少报告身份负例 | `test_research_report_helpers.py` | 六种本地渲染均返回保存验证状态。音频概览产物是文稿，自定义应用产物是规格。 |

## Account

| 工具 | 作用 | 离线检查 | 相关测试 | 既有真实证据与限制 |
| --- | --- | --- | --- | --- |
| `gemini_history` | 有界发现或明确读取历史 | action 类型负例 | `test_history_compact.py`, `test_manage_history_account_tools.py` | 11-06 用准确 ID 读回一问一答的合成历史。 |
| `gemini_delete_chat` | 精确删除聊天 | chat_id 类型负例 | `test_history_typed_parity.py` | 11-06 按准确 ID 删除合成聊天并独立核验不存在。验证后的显式删除同步到同账号的本地清理记录。 |
| `gemini_notebooks` | 发现 Notebook 和来源 | action 类型负例 | `test_notebooklm_service.py`, `test_manage_notebook_history_facade.py` | 合成 Notebook 来源发现与问答有真实证据；两种 Notebook ID 不应互换。 |
| `gemini_move_chat_to_notebook` | 把精确聊天移入 Notebook | 空 chat_id：结构化 `INVALID_ARGUMENT` | `test_notebook_move_fencing.py`, `test_notebook_read_evidence.py` | 11-06 移入合成 Notebook 后用项目列表独立核对归属，删除后复查列表为空。 |
| `gemini_account_inventory` | 读取能力／账号元数据 | surface 类型负例 | `test_account_inventory_stdio.py` | 账号可见列表不等于具体服务可用；本表不声称完整 entitlement 验证。 |
| `gemini_list_scheduled_actions` | 列出 Chat 定时操作 | limit 类型负例 | `test_manage_scheduled_actions.py` | 11-06 真实目录读取通过，单个计划另按准确 ID 回读。 |
| `gemini_get_scheduled_action` | 读取单个 Chat 定时操作 | 空 action_id：结构化 `INVALID_ARGUMENT` | `test_manage_scheduled_actions.py` | 11-06 按 ID 读回测试计划的准确时间、时区和删除状态。 |
| `gemini_create_scheduled_action` | 创建 Chat 每日定时操作 | title 类型负例 | `test_scheduled_mutation_evidence.py` | 11-06 创建合成每日计划并核验所选时间和时区。 |
| `gemini_delete_scheduled_action` | 精确删除 Chat 定时操作 | 空 action_id：结构化 `INVALID_ARGUMENT` | `test_scheduled_mutation_evidence.py` | 11-06 独立账号 MCP 按准确 ID 确认合成每日计划已删除。 |
| `gemini_manage_gems` | 管理 Gems | action 类型负例 | `test_manage_gems_models_inventory.py` | 合成 Gem 创建、读取、修改、删除与不存在核验曾通过；不外推附件。 |
| `gemini_cleanup` | 查看／处理 MCP 自有清理账本 | 无账号 list：`AUTH_REQUIRED`，未读取账本 | `test_owned_cleanup.py`, `test_cleanup_runtime.py`, `test_task_owned_cleanup.py` | 11-06 的新合成 Task 在取回结果前自动清理。精确 Task 目标与普通历史均经回读确认不存在。旧测试记录可按准确 ID 复查。 |
| `gemini_manage_chat` | 精确改名或置顶聊天 | chat_id 类型负例 | `test_chat_management_service.py` | 11-06 合成普通聊天的改名、置顶、取消置顶分别独立读回。 |
| `gemini_tasks` | Task 读取、删除与 Task 专属计划 | action 类型负例 | `test_web_tasks_service.py`, `test_task_delete_readback.py` | 合成 Task 专属删除与不存在读回曾通过；所有计划变体不因此自动通过。 |
| `gemini_task_browser` | 读取 Task 浏览器并按精确状态输入 | action 类型负例 | `test_task_browser_service.py`, `test_task_browser_turn.py` | 11-06 用新截图核对点击跳转、键盘导航、地址输入和滚动。相同点击 ID 再次调用未派发。 |
| `gemini_preferences` | 读取或明确修改 Task 偏好 | action 类型负例 | `test_preferences_service.py`, `test_preference_tools.py` | 11-06 读回仅远程和始终询问，实际 Task 出现许可卡。允许一次后远程浏览器开始执行，结束后原偏好经读回恢复。此前原生与 MCP 在始终允许下均仍出现许可卡。 |
| `gemini_skills` | 管理原生 Gemini Skills | action 类型负例 | `test_managed_skills.py`, `test_managed_skill_transport.py` | 11-06 修复原生上传端点，实测创建、精确读取、更新、停用、启用和删除，完整列表核验不存在。 |
| `gemini_manage_notebook` | 管理 Notebook 及其文本来源 | action 类型负例 | `test_notebook_admin.py` | 合成 Notebook 与文本来源创建、读取、删除及独立不存在验证通过；URL／文件来源待核验。 |
