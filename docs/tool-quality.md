# 工具质量与验证范围

本表覆盖当前 function 和 account 两个独立入口，各 17 个工具。`stdio` 负例指在无凭据、关闭浏览器自动加载与刷新、独立空状态库的 MCP 连接上检查参数拒绝；本地读取指不接触 Gemini 的空状态检查。它们验证注册、调用和拒绝路径，**不证明上游功能、额度或账号权限可用**。实际使用证据仅覆盖所述具体场景，不代表所有参数和后端可靠。

2026-11-01 的 source MCP stdio 审核在无凭据、空状态目录和系统禁网沙箱中调用了 34 个工具：17 项空值语义负例、15 项 schema 类型拒绝、2 项本地读取。15 项 schema 拒绝均返回 MCP `isError`；17 项业务负例均返回结构化 `INVALID_ARGUMENT`。其中发送空会话消息的文本是概括性的“Invalid session request”，与错误分类一致，但未重复结构化错误的完整说明。无 traceback 或合成凭据片段。空会话列表成功；清理账本因无账户返回 `AUTH_REQUIRED`，并未读取任何账号账本。额外对非空 `gemini_ask` 设置生成方法派发陷阱：无账号时返回 `AUTH_REQUIRED`，陷阱未触发，隔离 SDK 缓存保持为空。测试文件列出相关回归，不表示每个测试都访问上游。上游观察见 [功能覆盖](web-feature-coverage.md)。

## Function

| 工具 | 作用 | 离线检查 | 相关测试 | 既有真实证据与限制 |
| --- | --- | --- | --- | --- |
| `gemini_ask` | 对话、联网信息和自然语言 App 请求 | 空 prompt；非空 prompt 无账号 `AUTH_REQUIRED`、无派发 | `test_assist_surface.py`, `test_search_service.py` | Flash 公共 IANA 搜索曾返回结构化来源；不证明任意 App 已执行。 |
| `gemini_understand_image` | 单图理解 | 空 image 与不存在的本地路径负例 | `test_understanding_service.py` | 有历史真实图片理解；本轮单独工具未复测。 |
| `gemini_understand` | 多输入理解 | 空 task 负例 | `test_understanding_service.py`, `test_assist_surface.py` | Flash 合成图片与文件混合请求识别颜色与随机标记；当次未留逐输入 outcome 证据。 |
| `gemini_research` | 生成研究计划并保留原操作 | 空 query 负例 | `test_research_identity.py`, `test_research_native_contract.py` | 合成任务已确认并在原生界面显示进行中，原 handle 保留；完整报告尚未核验。 |
| `gemini_start_chat` | 创建会话 | model 类型负例 | `test_chat_service.py`, `test_chat_tools.py` | 合成会话已真实创建并使用。 |
| `gemini_send_message` | 会话续聊及原轮次恢复 | 空 message 负例 | `test_session_recovery.py`, `test_chat_service.py` | Flash 两轮成功，第二轮正确回忆随机标记；曾观察慢响应超时后原轮次可恢复。 |
| `gemini_list_sessions` | 列出本地会话 | 空会话读取 | `test_chat_session_lifecycle.py` | 本地状态工具；跨进程持久会话不在此结论内。 |
| `gemini_reset_session` | 重置单个本地会话 | session_id 类型负例 | `test_conversation_lifecycle.py` | 上述合成会话重置后独立确认远端不存在。 |
| `gemini_generate_image` | 后台生成图片 | 空 prompt 负例 | `test_media_jobs.py`, `test_media_tools.py` | Flash 合成图真实保存、解码及 hash 验证，原 handle 恢复、清理通过；近期并发修复仅离线验证。 |
| `gemini_edit_image` | 后台编辑本地图片 | 空 prompt、独立不存在路径负例 | `test_media_jobs.py`, `test_media_tools.py` | Flash 合成编辑真实保存、视觉内容及 hash 验证，原 handle 恢复、清理通过。 |
| `gemini_generate_video` | 后台生成视频 | 空 prompt 负例 | `test_media_generation.py`, `test_media_jobs.py` | Flash 合成 MP4 实测含视频及音轨、时长 10 秒，预览另存，原 handle 恢复、清理通过。 |
| `gemini_generate_music` | 后台生成音乐 | 空 prompt 负例 | `test_music_recovery.py`, `test_media_jobs.py` | Flash 合成结果的 MP3、MP4、封面分开保存并核对音视频轨及 hash；不保证每次均成功。 |
| `gemini_run_task` | 委托 Task 执行工作 | 空 prompt 负例 | `test_task_jobs.py`, `test_task_files.py` | 合成 Task 曾完成，CSV 附件与回答保存、原 handle 重读和任务／对话清理均核验；App 授权另有边界。 |
| `gemini_get_operation_status` | 查询已有操作 | 空 operation_id 负例 | `test_operations.py`, `test_operation_tools.py` | 媒体、Task 和研究原 handle 状态曾真实读取；不同操作状态分别判断。 |
| `gemini_get_operation_result` | 取回操作结果 | 空 operation_id 负例 | `test_operations.py`, `test_task_jobs.py` | 媒体与 Task 本地结果跨连接重读通过；研究完整报告尚未核验。 |
| `gemini_list_research_report_actions` | 查看已保存报告的派生动作 | 缺少报告身份负例 | `test_research_report_helpers.py` | 仅离线覆盖；真实已保存报告路径待核验。 |
| `gemini_create_from_research_report` | 从已保存报告生成本地产物 | 缺少报告身份负例 | `test_research_report_helpers.py` | 仅离线覆盖；不声称调用了原生网页创建菜单。 |

## Account

| 工具 | 作用 | 离线检查 | 相关测试 | 既有真实证据与限制 |
| --- | --- | --- | --- | --- |
| `gemini_history` | 有界发现或明确读取历史 | action 类型负例 | `test_history_compact.py`, `test_manage_history_account_tools.py` | 自有合成 Task 有界读取与导出曾验证；未穷举账号历史。 |
| `gemini_delete_chat` | 精确删除聊天 | chat_id 类型负例 | `test_history_typed_parity.py` | 自有合成对话删除及独立不存在检查曾验证。 |
| `gemini_notebooks` | 发现 Notebook 和来源 | action 类型负例 | `test_notebooklm_service.py`, `test_manage_notebook_history_facade.py` | 合成 Notebook 来源发现与问答有真实证据；两种 Notebook ID 不应互换。 |
| `gemini_move_chat_to_notebook` | 把精确聊天移入 Notebook | 空 chat_id：结构化 `INVALID_ARGUMENT` | `test_notebook_move_fencing.py` | 真实移动及独立归属读回未在此轮确认。 |
| `gemini_account_inventory` | 读取能力／账号元数据 | surface 类型负例 | `test_account_inventory_stdio.py` | 账号可见列表不等于具体服务可用；本表不声称完整 entitlement 验证。 |
| `gemini_list_scheduled_actions` | 列出 Chat 定时操作 | limit 类型负例 | `test_manage_scheduled_actions.py` | 具体账号实时列表及各调度变体未全面复测。 |
| `gemini_get_scheduled_action` | 读取单个 Chat 定时操作 | 空 action_id：结构化 `INVALID_ARGUMENT` | `test_manage_scheduled_actions.py` | 未声明当前账号上的完整读回覆盖。 |
| `gemini_create_scheduled_action` | 创建 Chat 每日定时操作 | title 类型负例 | `test_scheduled_mutation_evidence.py` | 不把 Task 的多步骤执行视为定时计划；本表不推断真实创建成功。 |
| `gemini_delete_scheduled_action` | 精确删除 Chat 定时操作 | 空 action_id：结构化 `INVALID_ARGUMENT` | `test_scheduled_mutation_evidence.py` | 精确 ID 删除与独立读回应分别验证；此轮无真实删除结论。 |
| `gemini_manage_gems` | 管理 Gems | action 类型负例 | `test_manage_gems_models_inventory.py` | 合成 Gem 创建、读取、修改、删除与不存在核验曾通过；不外推附件。 |
| `gemini_cleanup` | 查看／处理 MCP 自有清理账本 | 无账号 list：`AUTH_REQUIRED`，未读取账本 | `test_owned_cleanup.py`, `test_cleanup_runtime.py` | 自有生成线程的自动清理有真实不存在证据；不是全账号清理。 |
| `gemini_manage_chat` | 精确改名或置顶聊天 | chat_id 类型负例 | `test_chat_management_service.py` | 合成 Task 的改名、置顶、取消置顶曾独立读回；未穷举其它组合。 |
| `gemini_tasks` | Task 读取、删除与 Task 专属计划 | action 类型负例 | `test_web_tasks_service.py`, `test_task_delete_readback.py` | 合成 Task 专属删除与不存在读回曾通过；所有计划变体不因此自动通过。 |
| `gemini_task_browser` | 读取 Task 浏览器并按精确状态输入 | action 类型负例 | `test_task_browser_service.py`, `test_task_browser_turn.py` | 浏览器 Task 授权和继续执行有真实观察；不把画面或点击当网站副作用验证。 |
| `gemini_preferences` | 读取或明确修改 Task 偏好 | action 类型负例 | `test_preferences_service.py`, `test_preference_tools.py` | 保存偏好与其运行时效果分开；不从离线测试推断浏览器行为。 |
| `gemini_skills` | 管理原生 Gemini Skills | action 类型负例 | `test_managed_skills.py` | 合成技能 CRUD 曾通过；ZIP、参考文件与显式绑定未完整验证。 |
| `gemini_manage_notebook` | 管理 Notebook 及其文本来源 | action 类型负例 | `test_notebook_admin.py` | 合成 Notebook 与文本来源创建、读取、删除及独立不存在验证通过；URL／文件来源待核验。 |
