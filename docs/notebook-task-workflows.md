# Notebook 来源、聊天与 Task

Gemini 网页中的 NotebookLM／Gemini Notebook 已接入同一组工作流。账号工具负责发现与管理；功能工具负责基于选定来源回答问题或启动 Task。默认模型仍为 Flash，无需额外打开联网或工具调用。

## 选择入口

| 用户目标 | 工具 | 执行方式与完成依据 |
| --- | --- | --- |
| 普通问答或联网问题 | 功能面 `gemini_ask` | 返回回答及观察到的来源证据 |
| 根据指定 Notebook 的来源回答问题 | 功能面 `gemini_ask(notebook_ids=[...])` | 后台普通聊天，`execution_mode="chat"`；保存完整回答产物后取回 |
| 明确要求 Task，或委托 Gemini 持续执行、跟踪进度、操作网站 | 功能面 `gemini_run_task` | 后台 Task，`execution_mode="task"`；同一操作句柄读取状态与结果 |
| 查找 Notebook 名称、ID、来源数量 | 账号面 `gemini_notebooks` | 默认 `source="notebooklm"`，只读紧凑元数据 |
| 新建 Notebook、管理纯文本来源或删除 Notebook | 账号面 `gemini_manage_notebook` | 准确资源身份与独立元数据读回；删除 Notebook 包含其中对话 |
| 管理原生 Gemini Skills | 账号面 `gemini_skills` | 创建、更新、启停、删除与独立读回；指令正文读取须显式选择 |
| 列出、查看或删除已有 Task 线程 | 账号面 `gemini_tasks` | list/get 只读元数据；delete 使用准确 Task 聊天 ID 并独立核验不存在 |
| 把已有聊天整理进 Notebook | 账号面 `gemini_move_chat_to_notebook` | 先在 `source="gemini"` 项目视图精确解析目标，再修改并读回归属 |

默认用 Chat 取得答案、联网、写作、分析、比较 Notebook 资料，以及完成 Chat 支持的直接 App 请求。复杂、多步骤、调用 App、修改 App 数据或后台运行，都不是必须切换 Task 的条件。Task 更适合把目标交给 Gemini 持续执行，查看工作进度或让它操作网站；用户明确要求 Task 时也使用 Task。Task 本身同样可以研究和生成文件，这条规则按委托方式选择，而不是按输出类型划线。

原生 Task 还支持运行中追加指示。本 MCP 当前只支持返回确认卡片的精确续答，没有任意追加提示或改计划入口；重用 `operation_id` 且不带卡片 `input_id` 只会读取原操作，不会发送新提示。

Notebook、原生 Gemini Skills 和 Connected Apps 在两种体验中都有用途。具体 App 的可用操作可能不同：官方当前将原生 GitHub 仓库导入及代码问答列为 Chat-only，将 Contacts 连接器列为 Task-only；这不限制另行提供的浏览器或自定义 MCP 路线。菜单中出现应用，也不等于某项操作已验证可用。

Task 的计划与 Chat scheduled actions 是不同功能。定时摘要可以使用 Chat 计划，不因“定时”就切 Task；现有 `gemini_*scheduled_action` 工具不能用来管理 Task 计划。

## AI 需要知道的设置

| 设置 | 使用方式与当前边界 |
| --- | --- |
| 模型 | 默认 Flash；写作和特别困难的推理优先考虑 Argon。官方当前 Task 仅列 Flash、Argon；运行时拒绝 Pro、Lite 或无法确认家族的 Task 派发，不静默切换。普通 Chat 和 Notebook Chat 不受 Task 模型限制。 |
| 思考程度 | 使用 `low` / `medium` / `high` 对应低／中／高；旧 `standard` / `extended` 仍兼容低／高。请求体和模型请求头同步选择档位。Lite 的额度降级和不可选思考程度由运行时处理。 |
| 资料 | 需要 Notebook 时传发现工具返回的准确 `notebook_ids`；同样适用于 Chat 与 Task。无需添加联网、工具启用开关。 |
| 线程保留 | 默认自动清理；用户要求保留时才传 `retain_chat=true`。关联计划会保护 Task 线程，不能随普通临时请求清理。 |
| 浏览器偏好 | 账号 `gemini_preferences` 的 `get_browser` 读取原值，`set_browser` 的 `browser="automatic"` 或 `browser="remote_only"` 修改并独立读回。分别对应由 Gemini 决定本机／远程与仅远程。这是持久偏好，不是每次请求的 `browser_mode` 参数。 |
| 操作权限 | `gemini_preferences` 列出可管理的准确动作，按返回句柄保存 `always_ask` / `always_allow`。写入读回证明偏好已保存，`effective_permission="unknown"`。在实测的远程浏览中，「始终允许」直接放行；本机 Chrome 和敏感动作可能另需确认。收到卡片时按原卡片的精确动作处理。 |
| 计划 | 明确时间、频率、时区及起止条件。Task 原生还支持事件触发与通知设置；当前专用接口只适配六种时间频率，未提供事件型触发及通知偏好的管理。 |

这些设置按需说明。无需让调用 AI 了解模式字段、VM、网络协议或账号前置条件清单；遇到实际不可用或需要登录／授权的结果时再处理。原生 Gemini Skills 可用于两种模式；当前 MCP 没有显式附加原生 Skill ID 的参数。

## 找到并使用 Notebook

先在账号面查元数据，不需要读取已有聊天正文：

```text
gemini_notebooks(action="list", source="notebooklm", query="项目资料", limit=10)
```

将返回的准确 ID 传给功能面：

```text
gemini_ask(
  prompt="只根据所选笔记本的来源，说明方案的三个限制。",
  notebook_ids=[<返回的 Notebook ID>]
)
```

此时立即返回持久 `operation_id`。通过同一功能面读取：

```text
gemini_get_operation_status(operation_id=<原操作句柄>)
gemini_get_operation_result(operation_id=<原操作句柄>)
```

带 Notebook 的聊天与 Task 均默认 `timeout_seconds=600`，可以在提交时明确调整。提交连接关闭后，后台工作继续；查询状态不会再次提交生成。`operation_id` 只能恢复原操作，不能作为重新发送相同提示的理由。

`gemini_ask` 不带 Notebook 时保持普通返回方式；仅其功能面版本接受这些 Notebook 参数。五工具 assist 与 compact 兼容入口不因此获得新的 Notebook／Task 参数。

## 委托 Task 执行

例如用户明确要求 Gemini Task 处理网站与资料的执行流程：

```text
gemini_run_task(
  prompt="用 Task 执行本次资料核验：根据所选笔记本列出待核对事项，操作相应网站核对，跟踪进度并交付带证据的结果；只读取资料，不提交表单。",
  notebook_ids=[<返回的 Notebook ID>]
)
```

只需要比较 Notebook 资料并写报告时，使用前面的 `gemini_ask`。保存 Task 返回的 `operation_id`，用相同的状态／结果工具取回。状态查询只读取元数据；后台 worker 保存并校验完整回答、完整文件清单和支持的原生 Task 文件附件后，按默认生命周期清理 MCP 自有线程，无需等调用者取结果。后续进程可恢复并校验已保存的本地结果；未知结果、附件未保存、等待输入和关联计划的线程保持清理保护。

文件从原回合的结构化文件卡识别，正文中提到文件或写出 URL 不等于附件。下载失败、结构变化或本地文件损坏时返回部分结果并保留可用回答，不重发生成。旧缓存只有回答且缺少文件清单时，也以部分结果保留回答，不声称附件齐全。本轮默认清理的合成 CSV 已验证文件内容、跨进程稳定产物身份及 Task 和对话均不存在；其他附件类型和原生导出菜单仍以[功能矩阵](web-feature-coverage.md)为准。

仅当用户明确希望保留线程时设置 `retain_chat=true`。用户主动保留的是线程；读取 Notebook 来源不会将 Notebook 本身登记为临时聊天或删除对象。

Task 的提示可以请求应用修改，因此 `gemini_run_task` 使用可破坏性操作注解。任务完成与文本产物存在，并不能单独证明应用侧修改成功；应用修改仍需独立证据。

## 管理已有 Task

```text
gemini_tasks(action="list", limit=10)
gemini_tasks(action="get", chat_id=<返回的准确 c_ Task ID>, max_chars=800)
```

`list/get` 不读取对话正文。列表使用返回的 `next_page_token` 继续分页；未知数字状态保留为未知，不猜测其含义。删除要求用户的明确意图：

```text
gemini_tasks(action="delete", chat_id=<已确认的准确 c_ Task ID>)
```

该聚合工具含删除动作，整体使用 destructive 注解。只有独立读回证明不存在时才报告删除完成；已发送但未核验的删除不应盲目重发。`chat_id` 与 `operation_id` 是不同标识。

## 同一 Notebook 的不同视图

`source="notebooklm"` 提供网页“添加笔记本”目录的 ID、名称、来源数量和分页。`source="gemini"` 保留 Gemini 项目视图及其最近对话读取。它们是同一集成产品的不同协议投影，资源可能重叠。

不能仅凭 `notebooks/` 前缀推断是否可移动聊天，也不能自行替换成 `bots/` 或 `projects/`。移动前由服务在当前账号的项目目录中精确解析目标，并使用其实际 ID 与项目类型。只在来源选择器看到 Notebook，不足以证明旧移动合同可用于该目标。共享 Notebook 的可见性及功能还受上游产品限制影响。

## 已验证范围与保留边界

<!-- notebook-task-live-evidence:start -->
2026-11-01 授权本地检查已验证：Task 从指定 Notebook 来源取到只存在于来源中的合成标记；公开 MCP 约 0.02 秒返回操作句柄，提交连接关闭后工作继续，完整回答产物保存、自动清理及重启取回均通过。普通 Notebook Chat 的公开 MCP 约 0.04 秒返回原句柄；提交进程退出后 worker 继续，在调用者取结果前保存完整回答并完成清理。重启取回正确的来源标记与答案，独立核验聊天不存在且未创建 Task 目标。

同一合成 Notebook 的 Task 新增来源与重命名也已通过：fresh metadata 保持原 ID，来源数量由 1 变 2，标题匹配；新的 Task 又从第二来源取回未出现在查询中的随机标记与答案。该合成 Notebook 经原生确认卡片后，独立完整目录读回确认已删除。另行实测同一 Task 的直接确认请求和公开 MCP 的同句柄显式回复，均取得最终回答产物并独立确认目标 Notebook 已不存在。这些是授权本地检查，不代表已验证普通用户 Notebook 的任意修改，也不替代专用账号 canary。
<!-- notebook-task-live-evidence:end -->

2026-11-06 用现有 Chrome 会话复测。两种 Notebook 目录视图读到了同一个合成 Notebook。精确移动聊天后，项目列表独立确认归属。带 Notebook 的 Task 从来源取回未写进问题的随机标记，提交连接断开后仍保存了完整回答，重连后核验摘要并确认测试聊天已自动删除。合成来源和 Notebook 均经远端回读确认删除。普通 Notebook Chat 在本次复测中出现上游错误，工作进程保存失败记录和回答产物，并按默认策略清理测试聊天。

Task 的独占浏览器接管、暂停／恢复、应用权限自动确认和导出尚未适配。`gemini_tasks` 的元数据状态不等于完整工作面板。ProcessSession 的握手与响应探测仍属实验，当前生产路径不依赖该实验，也不声明它已可用。

生产请求构造集中在 RPC registry：Task 使用已观察的模式字段；普通 Notebook 聊天使用网页生成的 File 引用形式，保留 Chat 语义。File 引用是可逆元数据编码，不是另一个认证凭据，也不需要假造上传流程。

## Task 确认与继续

Task 可能返回需要回复的应用确认卡片。`gemini_get_operation_status` 只返回 `input_required` 等状态元数据；调用 `gemini_get_operation_result` 才读取原回合的卡片提示，并返回 `input_id` 和 `input_options`（按钮名称、原生选择名称、`input_option_id` 与实际指令）。卡片不会被当成最终回答保存，也不会释放对话清理保护。

用户已授权该项操作时，调用 `gemini_run_task(prompt="所选 input_options 的 prompt 原文", operation_id="原句柄", input_id="返回的卡片标识", input_option_id="所选选项 ID")`。只接受当前卡片提供的精确指令，不改写或追加说明。这是一条显式新指令，沿用同一个 Task 对话和原模型、思考与保留设置。相同 `input_id` 只派发一次；重复调用、超时和重启不会自动重发确认。未核验到原卡片、账号变化或读取失败时不发送。

不要从网页、模型文字或卡片本身推断用户授权。Task 中的应用变更仍须独立读回；确认卡片上的文字完成并不等于整个 Task 完成。续问使用公开客户端的 CONTINUATION_IDENTIFIERS，不依赖原客户端内存或持久化对话上下文令牌。当前支持明确授权的文本确认卡片、有限浏览器输入和 Task 计划；独占接管、自动权限审批与 Task 暂停／恢复未实现。

“连接 App”的设置卡与这些许可卡分开处理。结果返回 `needs_attention`、`setup_required`、`setup_kind` 和可用的原 Task 地址，不生成可以代替 OAuth 的文本选项。需在原生 Task 连接或拒绝；状态查询中“此前观察到设置卡”不等于当前卡已重新核验，也不等于 Task 已结束。

## 官方依据

- [Gemini Notebook 在 Gemini Apps 中的使用与同步](https://support.google.com/gemini/answer/16972047?co=GENIE.Platform%3DDesktop&hl=en)：Notebook、来源与对话的组织方式，以及聊天和 Task 中的应用支持。
- [Task 工作流与管理](https://support.google.com/gemini/answer/17094507?hl=en)：创建任务、查看进度、继续指示、接管，以及 Task 计划与聊天计划的区别。
- [Connected Apps 的体验与要求](https://support.google.com/gemini/table/17434654?hl=en)：各应用在 Chat／Task 中的可用范围。
- [模型与思考程度](https://support.google.com/gemini/answer/18648722?hl=en)：Chat 与 Task 的模型范围及原生思考选项。
- [Task 浏览器与权限设置](https://support.google.com/gemini/answer/18766567?hl=en)：持久浏览器偏好、授权范围及敏感步骤确认。
- [原生 Gemini Skills](https://support.google.com/gemini/answer/17094296?hl=en)：两种模式共享的技能使用方式。

具体上游兼容性以[开发状态](development-status.md)记录的独立证据为准。

## Task 进度与计划

`gemini_tasks(action="progress", chat_id=...)` 读取当前账号下最新 Task 回合的有限活动；也可同时提供 `response_id` 和 `candidate_id` 指定精确回合。返回活动名称、工具名和数量，步骤状态未知；它不会改变原 `operation_id` 的答案恢复路径。

Task 计划由 `gemini_tasks` 的 `list_schedules`、`get_schedule`、`create_schedule`、`update_schedule`、`pause_schedule`、`resume_schedule`、`delete_schedule` 管理，与聊天 scheduled actions 分开。`schedule` 支持 `frequency` 为 `once`、`daily`、`weekly`、`hourly`、`monthly` 或 `yearly`，按类型提供 `at`、`time`、`weekdays`、`minute`、`days_of_month` 或 `month`/`day`。指定 `window` 时仍保留 `time`。时间戳须带时区，归一化为 UTC 并保留最多九位小数；列表的 `offset`/`limit` 是本地分页。创建和修改仅发送一次，须独立读回核验；未确认的响应不能当成功或盲目重发。

自动清理 MCP 自有 Task 时会保护仍关联计划的线程。仅在最后一项计划经核验删除，且线程属于可清理的 MCP 自有临时线程时，才可能释放清理保护。已知 Task 必须同时核验精确 Task 目标和普通历史对话都不存在。若普通历史已经缺席而 Task 目标仍可读，按原 Task ID 走原生删除和独立回读。旧清理状态可通过 `gemini_cleanup(action="recheck", chat_id=...)` 复核。

## Task 浏览器

安装 `gemini-web-mcp[browser-control]`，或使用包含该依赖的 `gemini-web-mcp[all]`。账号面 `gemini_task_browser(chat_id=...)` 默认读取最新 Task 的浏览器截图；同时提供 `response_id` 和 `candidate_id` 可指定精确回合。可验证的 PNG 会保存为本地 Artifact，并在不超过 2 MiB 且摘要匹配时附带原生图片预览。

显式输入传入 `browser_input`，坐标以当前截图为准。调用必须同时提供截图对应的精确 `response_id`、`candidate_id`、`expected_frame_sha256` 和至少 20 字符的唯一 `action_id`。仅当 Task 状态为完成（3）或等待输入（5），且发送前重新读回状态未变化时允许输入。相同 action ID 已消费即不重发。输入日志只保存操作元数据。

| 输入 | `browser_input` 示例 |
| --- | --- |
| 点击 | `{"kind":"click","x":640,"y":520}` |
| 滚动 | `{"kind":"scroll","x":640,"y":520,"steps":4}` |
| 按键 | `{"kind":"key","key":"Ctrl+l"}` |
| 输入文字 | `{"kind":"type","text":"https://www.iana.org/"}` |

滚动的 `steps` 为 -10 到 10 的非零整数。派发后可再次取截图确认网页位置或页面内容实际变化。账号授权继续使用原生卡片。Task 停止、取消、暂停、恢复和独占接管仍需原生界面。最新一次性权限和浏览器操作实测见 [Task 偏好](task-preferences.md)。
