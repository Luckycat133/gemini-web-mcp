# Gemini 网页功能覆盖与实测矩阵

基础盘点日期为 2026-11-01。2026-11-06 使用现有 Chrome 登录会话复测的结果见[最新开发记录](development-status.md)和[逐工具验证](tool-quality.md)。官方帮助、原生界面观察和 MCP 实测分别记录。账号、地区、订阅、模型和上游发布状态可能影响可用性。

状态含义：

- **已实现**：仓库已有对应入口及服务合同；不单凭离线测试声称真实可用。
- **实测通过**：指定的合成场景取得独立结果证据；不外推至所有模型、参数或私人 App。
- **待核验**：实现或新一轮检查正在进行，尚未取得完整结果。
- **协议未明**：缺少可安全实现的完整读写、资源身份或结果验证合同。
- **仅原生**：本 MCP 尚无对应操作入口，需回到 Gemini 原生界面。

“后台运行”“调用 App”“多步骤”本身都不要求使用 Task。默认用 Chat 获得答案、联网、写作、理解资料或完成 Chat 支持的直接 App 请求；用户明确要求 Task，或委托持续执行、查看工作进度、操作网站时使用 Task。联网和工具调用无需另行开启。模式与参数用法见 [Notebook、Chat 与 Task 工作流](notebook-task-workflows.md)；模型选择见[模型说明](models.md)。

## 问答、理解与生成

| 功能 | 官方范围 | 原生 UI 观察 | MCP 实现范围 | 本轮实测与剩余覆盖 |
| --- | --- | --- | --- | --- |
| 普通 Chat、写作、推理 | [模型与思考程度](https://support.google.com/gemini/answer/18648722?hl=en)区分 Chat、Task 的模型与思考选项。 | 已观察 Chat／Task 切换、模型选择及低／中／高思考选项。 | **已实现**：`gemini_ask`；Flash 默认，按需选择模型和思考程度。 | 已有真实 Chat 与 Task 的 Flash 三档、Argon 中档结果。每项检查只证明实际选中的组合，不代表所有模型均完成复测。 |
| 联网搜索与来源 | [来源说明](https://support.google.com/gemini/answer/14143489?hl=en)区分回答与可用来源链接；并非每次回答都有来源。 | 已观察原生搜索、App 菜单；本轮未穷举每个搜索入口。 | **已实现**：问答与联网合并到 `gemini_ask`；返回观察到的来源和 grounding 状态，无联网开关。 | 此前有搜索结果。11-06 的新对照返回 `UPSTREAM_REJECTED`，没有可核验的来源。 |
| 图片、文件、URL 与混合输入理解 | [上传与分析文件](https://support.google.com/gemini/answer/14903178?hl=en)覆盖多种文件、媒体及 Notebook 输入。 | 已观察本地文件、Drive、Notebook 和代码导入等入口；入口出现不等于全部导入流程已适配。 | **已实现**：`gemini_understand_image`、`gemini_understand`，支持已有合同覆盖的文件／URL／混合输入。 | 此前已验证混合输入。11-06 的图片和混合输入请求返回上游错误，原生上传协议对照也未取得合格识图结果。 |
| 连续 Chat 会话 | [Chat 与模型说明](https://support.google.com/gemini/answer/18648722?hl=en)提供聊天体验；本地 session 句柄属于本 MCP 的管理方式。 | 本轮原生 UI 未逐项复核会话恢复行为。 | **已实现**：启动、续聊、列出和重置本地会话；续聊保留原有会话身份。 | 既有两轮和重置成功记录。11-06 新对照的首轮未得到预期回答，续聊和原轮次恢复超时。 |
| 图片生成 | [生成与编辑图片](https://support.google.com/gemini/answer/14286560?hl=en)包括生成、编辑及参考图片。 | 11-06 原生 Flash 图片请求返回可解码的图片。 | **已实现**：`gemini_generate_image` 后台提交，保存操作句柄并取回本地产物。 | 11-06 Flash 低档 MCP 取回可解码 2048×2048 JPEG，像素符合生成要求，自动清理已复查。 |
| 图片编辑 | [图片帮助](https://support.google.com/gemini/answer/14286560?hl=en)包含基于图片的修改。 | 已观察原生图片编辑能力；未逐项重测所有编辑交互。 | **已实现**：`gemini_edit_image` 接受参考图片与修改要求，异步保存结果。 | 11-06 Flash 低档 MCP 完成改色并保留源标记，解码、原句柄恢复和自动清理均通过。 |
| 视频生成 | [视频帮助](https://support.google.com/gemini/answer/16126339?hl=en)还包括原生视频编辑、多轮修改和界面选项。 | 原生界面可生成视频；完整原生编辑流程未在本轮逐项覆盖。 | **已实现**：`gemini_generate_video` 后台生成及媒体产物保存。 | **实测通过**：本轮视频生成。原生视频编辑、完整模板／比例／多轮控制未全部适配，不能由生成成功推定。 |
| 音乐生成 | [音乐帮助](https://support.google.com/gemini/answer/16901237?hl=en)包括音乐及带媒体上下文的生成；时长、模型和额度有独立限制。 | 已观察音乐入口和时长选项；不能把历史某次模型成功当作统一模型要求。 | **已实现**：`gemini_generate_music` 后台生成，保存实际返回的音频／视频等产物。 | **实测通过**：本轮音乐生成。完整时长、所有人声／器乐组合及原生下载形式未穷举。 |
| Deep Research | [Deep Research](https://support.google.com/gemini/answer/15719111?hl=en)包含研究计划、来源选择、报告和后续导出。 | 已观察原生研究工具和计划确认流程。 | **已实现**：计划、确认、后台报告与同一操作的状态／结果读取；不以重发查询充当恢复。 | 11-06 从新的 Pro Research 原句柄取回并核验了完整 Markdown 报告，测试对话经远端回读确认删除。 |
| 从研究报告生成衍生内容 | [研究帮助](https://support.google.com/gemini/answer/15719111?hl=en)列出原生报告的后续使用方式。 | 本轮未逐项复核原生报告的全部创建／导出菜单。 | **已实现但范围不同**：本地网页／信息图 HTML、测验／抽认卡 Markdown；`audio_overview` 是旁白稿，`custom_app` 是应用规格。 | 11-06 从真实报告按原句柄列出六种操作，每种都保存了一个本地产物。 |

四个媒体工具默认后台运行。成功标准是取回原操作的可用产物并验证实际文件；“已接受”、链接、生成说明或进度文本不足以证明完成。已有产物与具体工具参数见[工具说明](tools.md)。

## Task、资料与原生工作区

| 功能 | 官方范围 | 原生 UI 观察 | MCP 实现范围 | 本轮实测与剩余覆盖 |
| --- | --- | --- | --- | --- |
| 委托 Task 与进度 | [Task 帮助](https://support.google.com/gemini/answer/17094507?hl=en)包含持续执行、工作面板、追加指示和文件。 | 已观察 Task 状态、活动面板和等待确认卡片；原生也可在运行中追加指示。 | **已实现**：`gemini_run_task`；持久操作句柄、有限活动元数据、完整回答产物和原操作恢复。活动标签不冒充逐步完成状态。 | **实测通过**：本轮四个浏览器 Task 在各自卡片获授权后完成。任意追加指示、修改运行中计划、Task 暂停／恢复／停止尚无 MCP 入口。 |
| Task 确认卡续答 | [权限帮助](https://support.google.com/gemini/answer/18766567?hl=en)区分单次、当前线程和跨线程的选择，具体选项随功能变化。 | 真实浏览器卡含浏览器选择和允许／不允许；批准后原生 UI 标注本线程已获许可。 | **已实现**：原句柄、精确 `input_id`、返回的 `input_option_id` 与提示联合续答一次。隐藏、禁用、未知或 subagent 卡片拒绝猜测执行。 | **实测通过**：同一 Task 续答后继续执行；重复消费不重发。按钮文字不能自行解释成 once／thread／always；只发送原卡确切提供的选择。 |
| Notebook 发现与来源问答 | [Gemini Notebook](https://support.google.com/gemini/answer/16972047?hl=en)支持 Notebook、来源、聊天组织及 Chat／Task 使用。 | 添加 Notebook 的选择器实际显示 Notebook 与来源数量；Notebook 和项目视图属于同一集成产品。 | **已实现**：`gemini_notebooks` 发现；普通 `gemini_ask(notebook_ids=...)` 与 Task 使用相同已发现资源，但保留各自模式。 | 两种模式都有历史来源标记证据。11-06 的 Task 再次从来源取回标记并在断开后保存答案，普通 Notebook Chat 复测返回上游错误。 |
| 经 Task 管理 Notebook／来源 | [Notebook 帮助](https://support.google.com/gemini/answer/16972047?hl=en)列出创建、修改和删除 Notebook／来源等原生能力。 | 真实删除会要求确认；确认与最终资源不存在分别验证。 | **已实现**：可在用户授权的 Task 请求中使用 Notebook；Task 完成不能替代资源侧核验。 | **实测通过**：合成 Notebook 新增来源、改名、第二来源问答及确认后的删除；不外推为任意用户 Notebook 修改均可靠。 |
| 专用 Notebook／来源管理 | [Notebook 帮助](https://support.google.com/gemini/answer/16972047?hl=en)还支持原生来源管理。 | 原生 Notebook 的来源管理与来源选择器是不同操作入口；不能猜测资源 ID 转换。 | **已实现**：`gemini_manage_notebook` 的创建、读取、来源列表、添加纯文本、精确删除来源和删除 Notebook。 | **实测通过**：合成 Notebook 与纯文本来源完成创建、读取、删除及独立不存在核验；Notebook Chat 从来源取回查询未包含的随机标记。URL／文件来源及文本更新仍需独立验证。删除 Notebook 会连带其对话，需明确意图。 |
| 原生 Gemini Skills | [Skills 帮助](https://support.google.com/gemini/answer/17094296?hl=en)说明 Chat／Task 可使用保存的技能，支持创建、编辑、启停、上传及删除。 | 已观察原生 Skills 管理页及其读写效果；这与仓库里的 Agent Skill 文件不是同一资源。 | **已实现**：`gemini_skills` 列表、精确读取、创建、修改、启停、删除及独立读回。正文按明确请求读取。 | **实测通过**：本轮合成技能完整 CRUD 并清理。原生文件夹／zip、参考文件、脚本和显式附加 Skill ID 到生成请求尚未完整适配。 |
| Connected Apps 与自定义 MCP App | [应用支持表](https://support.google.com/gemini/table/17434654?hl=en)逐项区分模式；[自定义 MCP App](https://support.google.com/gemini/answer/17209137?hl=en)也有独立接入条件。 | 已观察 Chat／Task 的 `@` 菜单有重叠也有差异；菜单可见不等于每个动作可执行。 | **已实现**：在提示中自然指定应用；按实际模式、目录和返回的授权请求处理。不要求用户开启联网／工具开关。 | 本轮区分了等待 App 设置的任务；没有授予新连接，类型化实时复测因认证失效受阻。仅指定场景有真实证据，未完成全部 App 的读写矩阵。官方当前 GitHub 仓库导入／代码问答为 Chat-only，Contacts 连接器为 Task-only，不外推到其他浏览器／MCP 路线。 |
| Canvas | [Canvas 帮助](https://support.google.com/gemini/answer/16047321?hl=en)包含文档、应用、幻灯片、代码编辑与导出。 | 旧轮次已观察 Canvas 入口；本轮未完成原生文档生命周期复测。 | **仅原生／协议未明**：现有探测与普通内容生成不是 Canvas 文档的创建、编辑、版本、导出和分享服务。 | 未验证完整 Canvas 闭环。生成 HTML、应用规格或普通回答不能标为原生 Canvas 项目。 |
| Task 原生生成文件 | [Task 帮助](https://support.google.com/gemini/answer/17094507?hl=en)描述文件打开、编辑与下载。 | 公共前端存在文件／链接面板；链接展示本身不证明二进制文件可保存。 | **部分实现**：Task 完整回答和已识别的结构化文件附件可保存为本地 Artifact；任意附件的归属、下载、编辑与导出合同仍不完整。 | **局部实测通过**：本轮结构化附件在默认清理前保存并经验证；不能把这一结果外推到所有原生文件。专用图片／视频／音乐产物由各自已适配流程验证。 |
| Guided Learning、代码／Drive 导入等入口 | [文件帮助](https://support.google.com/gemini/answer/14903178?hl=en)与[应用表](https://support.google.com/gemini/table/17434654?hl=en)只支持其中各自声明的范围。 | 历史界面已观察学习、导入代码与 Drive 选择器；本轮未逐项复测。 | **部分实现／仅原生**：兼容入口保留已有学习模式；没有完整选择器、学习交互或仓库导入管理面。 | 未覆盖完整原生流程；普通文件理解、学习模式透传或目录探测均不能替代端到端验证。 |

## 浏览器、权限与计划

| 功能 | 官方范围 | 原生 UI／行为观察 | MCP 实现范围 | 本轮实测与剩余覆盖 |
| --- | --- | --- | --- | --- |
| Task 远程浏览器观察与输入 | [Task](https://support.google.com/gemini/answer/17094507?hl=en)与[浏览器权限](https://support.google.com/gemini/answer/18766567?hl=en)说明委托访问网站及授权。 | 真实远程浏览器显示公开网页；实际点击后页面转到 RFC 2606。 | **已实现**：`gemini_task_browser` 取验证 PNG，显式 click／scroll／key／type；输入绑定精确回合、截图摘要和唯一 action ID。 | **实测通过**：IANA → RFC 2606 的 PNG 独立确认、重复不重发、旧帧拒绝。部分 Task 截图恢复仍待完成；截图、输入已发送与网站实际效果分别判断。 |
| 浏览器选择与询问偏好 | [浏览器设置](https://support.google.com/gemini/answer/18766567?hl=en)提供让 Gemini 决定／仅远程及询问／允许偏好。 | 四种组合写入和独立读回曾通过。原生 UI 和 MCP 在已保存「始终允许」时也曾出现许可卡。 | **已实现**：`gemini_preferences` 读写已保存偏好。设置结果用 `effective_permission="unknown"`、`behavior_verified=false` 区分运行时证据。 | **实测通过**：11-06 在「仅远程」及「始终询问」下收到真实许可卡，明确选中「允许一次」后远程浏览器执行，原偏好经读回恢复。 |
| 按 App／动作的权限偏好 | [应用动作权限](https://support.google.com/gemini/answer/18766567?hl=en)区分跨线程偏好与单次／当前线程批准。 | 已观察动作目录与已存覆盖项不同；服务端可能补充询问标记。 | **已实现**：精确发现可管理动作、单动作修改、旧快照保护和独立读回；不伪造动作 ID，不提供全应用一键放行。 | **设置读回通过**：公开信息动作的 ask → allow，随后撤销测试覆盖并核验原缺省恢复。尚未证明所有 App 的实际行为；偏好读回始终只标记 `stored_preference`。 |
| 浏览器接管、终端、登录及远程数据清理 | [权限帮助](https://support.google.com/gemini/answer/18766567?hl=en)包含原生远程浏览器数据删除；[Task](https://support.google.com/gemini/answer/17094507?hl=en)描述原生工作体验。 | 本轮观察过原生浏览器与任务设置；没有建立完整接管／终端合同。 | **仅原生／未覆盖**：无独占接管、任意终端命令、自动登录、自动批准未知卡片或远程浏览器数据删除入口。 | 输入接口不代表 Task 已暂停或取得独占控制。保存全局权限不等于获准自动处理任意新任务中的敏感请求。 |
| Task 时间计划 | [Task 计划](https://support.google.com/gemini/answer/17094710?hl=en)支持一次、小时、日、周、月、年及监控型计划。 | 已观察 Scheduled 创建／管理和 Task 关联计划，区别于 Chat 计划。 | **已实现**：`gemini_tasks` 读、创建、改、暂停／恢复和删除六类时间计划，保留时区并独立读回。 | **实测通过**：六类频率、读回、暂停／恢复、创建／删除；未穷举所有起止日期、窗口和夏令时组合。有关联计划的自有线程保持清理保护。 |
| Task 事件计划与通知 | [Task 计划帮助](https://support.google.com/gemini/answer/17094710?hl=en)另列 Gmail／主题监控和相关管理。 | 本轮未完成事件触发与通知的行为检查。 | **仅原生／未覆盖**：当前专用计划接口只覆盖时间计划，无监控触发、通知偏好和 Run now 管理入口。 | 不用普通时间计划冒充事件监控；计划创建成功也不证明将来的实际运行或通知已送达。 |
| Chat scheduled actions | [Chat 计划帮助](https://support.google.com/gemini/answer/16316416?hl=en)明确独立的聊天计划功能。 | 历史已观察 Chat scheduled actions；不能与 Task Scheduled 管理混用。 | **部分实现**：独立 list／get／daily create／delete 工具；未开放完整编辑、启停和全部频率。 | **局部实测通过**：本轮合成条目完成创建、读取和删除及独立不存在核验；未验证实际定时执行或全部频率。Task 六类计划通过不补足此行缺口。 |

浏览器偏好与实际许可卡的差异是本轮**已观察到、尚未解释的行为边界**。官方也说明某些操作可能追加确认，但这不能证明本轮公开网页卡片的具体原因。每个等待中的 Task 均通过原卡精确续答；没有以重发任务、猜按钮范围或静默扩大授权替代处理。详见 [Task 偏好](task-preferences.md)。

## 历史、账号与其他设置

| 功能 | 官方范围 | 原生 UI 观察 | MCP 实现范围 | 本轮实测与剩余覆盖 |
| --- | --- | --- | --- | --- |
| 历史发现、读取、搜索与导出 | [查找和管理聊天](https://support.google.com/gemini/answer/13666746?hl=en)说明原生聊天管理。 | 已观察历史列表、搜索和单条聊天；本轮未穷举原生搜索／导出界面。 | **已实现**：紧凑元数据发现；按明确意图读取、搜索或导出；默认避免整段聊天正文。 | **局部实测通过**：本轮对自有合成 Task 完成有界读取和本地导出验证；未重跑全部历史操作组合。列表命中、正文读取和导出文件验证分别记录。 |
| 改名、置顶、删除、移动到 Notebook | [聊天管理](https://support.google.com/gemini/answer/13666746?hl=en)和[Notebook](https://support.google.com/gemini/answer/16972047?hl=en)支持各自原生组织方式。 | 已观察聊天菜单与 Notebook 项目视图。 | **已实现**：准确 ID 的改名、置顶、取消置顶和删除，移动前独立解析项目目标并读回归属。 | **实测通过**：11-06 的合成聊天改名、置顶、取消置顶和删除经独立回读，另一条测试聊天移入 Notebook 后精确核验归属。 |
| 原生聊天分支及回合界面控制 | [聊天帮助](https://support.google.com/gemini/answer/13666746?hl=en)还列出从已有回合分支新聊天。 | 本轮未重新覆盖分支、编辑提示、重生成、反馈等完整菜单。 | **仅原生／未覆盖**：无统一的回合编辑、分支和反馈管理入口；普通续聊不能冒充已有回合修改。 | 未完成这些交互的合同和真实闭环；语音／Live 等其他入口亦不在本轮覆盖范围内。 |
| MCP 自有临时聊天清理 | [活动管理](https://support.google.com/gemini/answer/13278892?hl=en)说明原生活动与聊天管理。 | 普通历史曾漏掉两个仍可按 ID 读取的合成 Task。 | **已实现**：默认清理已登记的自有线程，`retain_chat` 明确保留，结果不明、等待确认或关联计划时保护线程。Task 须核验目标和普通历史均不存在。 | 11-06 的新合成 Task 保存答案后自动清理，两处均独立核验不存在，重连仍能从原句柄取回答案。 |
| Gems | [Gems 帮助](https://support.google.com/gemini/answer/15146780?hl=en)描述创建、使用和管理。 | 历史已观察 Gems 页面；本轮未重新覆盖全部原生选项。 | **已实现**：Gems 列表、创建、修改、删除及已有聊天使用合同；修改须读回。 | **局部实测通过**：本轮合成 Gem 完成创建、列表、修改、删除及独立不存在核验；知识附件和分享矩阵尚未核验。原生 Gemini Skills 与 Gems 分别管理。 |
| 模型、思考和额度 | [模型帮助](https://support.google.com/gemini/answer/18648722?hl=en)区分 Chat／Task 可选项与思考设置。 | 当前 UI 有四个模型家族；Task 当前仅提供 Flash／Argon。 | **已实现**：默认 Flash、低／中／高；Lite 由新鲜的基础 Flash 额度耗尽证据限制，Lite 不可选择思考强度。 | 已有真实 Flash／Argon 组合验证；**真实耗尽后进入 Lite 的正向路径仍未核验**。媒体或高思考额度耗尽不能冒充基础 Flash 耗尽。 |
| Library、公开链接与分享 | [图片](https://support.google.com/gemini/answer/14286560?hl=en)／[视频](https://support.google.com/gemini/answer/16126339?hl=en)帮助分别说明其原生下载或分享选项。 | 历史已观察 Library 与公开链接入口；本轮未复测完整资产管理。 | **部分实现／仅原生**：有只读能力／链接探测；无完整 Library 资产和分享链接的创建、撤销、删除生命周期。 | 本地媒体保存不等于 Library 同步或已分享；聊天删除也不能充当公开链接撤销的证据。 |
| 主题、语言、活动、个性化、订阅与账号设置 | [活动管理](https://support.google.com/gemini/answer/13278892?hl=en)说明活动记录及删除；其他设置以其原生页面为准。 | 历史已观察主题、订阅、活动和个性化入口；本轮没有修改这些设置。 | **仅原生／未覆盖**：不提供通用设置写入、活动总清理、付费／订阅管理或个性化配置工具。已有账号状态读取不等于这些管理能力。 | 没有本轮写入与最终状态验证。临时聊天、MCP 自动删除与 Keep Activity 是不同合同，不能互相替代。 |

## 本轮尚待补齐的证据

以下项目保持待核验；仅在取得对应最终状态后更新本页，不能凭进程启动、工具接单或文件名出现改成通过。

<!-- web-feature-live-session-research:start -->
- 此前的**连续会话**两轮和 reset 已真实通过，11-06 复测超时，原回合未读到答案。旧的合成 **Research** 对话已按准确 ID 删除并经回读核验。
<!-- web-feature-live-session-research:end -->
<!-- web-feature-live-notebook-admin:start -->
- **专用 Notebook 管理**已验证合成 Notebook 和纯文本来源的创建、精确读取与删除。URL 和文件来源仍待验证。
<!-- web-feature-live-notebook-admin:end -->
<!-- web-feature-live-browser-recovery:start -->
- 11-06 的**浏览器 Task**经单次许可后打开公开页面。新截图核验点击、键盘导航、输入地址和滚动的效果，相同动作 ID 重试未重发。原浏览器偏好已独立读回恢复，旧的浏览器控制 Task 和 App 设置 Task 已按准确 ID 删除并核验。始终允许是否能跳过许可卡仍未证实。
<!-- web-feature-live-browser-recovery:end -->

11-06 的功能面与账号面均用当前 Chrome 会话自动认证。普通 Flash Chat、续聊、联网、普通 Notebook Chat 和识图仍未取得合格结果。图片生成、编辑和两种远程浏览偏好已取得端到端证据。详情见开发状态。

尚未覆盖的原生能力应继续作为明确缺口：Canvas 文档／应用全生命周期、未识别的 Task 附件类型及任意导出（已识别结构化文件卡的下载已实现并局部实测）、运行中自由追加指示和 Task 控制、独占浏览器接管／终端、全部 App 行为、事件型计划／通知、Library／分享生命周期、完整 Skills 资源上传、Drive／Photos／代码导入以及其他账号设置。通过一个生成任务或偏好读回不能关闭这些条目。

本页实测指用户明确授权的本地合成检查，**不等于专用账号兼容 canary，也不是 PR CI 的在线验证结果**。后续发布仍遵守[验证合同](agent-verification.md)；历史快照见[旧 UI 盘点](live-ui-coverage.md)，本轮过程证据见[开发状态](development-status.md)。
