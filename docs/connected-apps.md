# Connected Apps 与 Task 接入记录

本文记录 Gemini Web 的公开能力、已观察到的界面和 MCP 的接入方向。界面展示或官方文档描述本身不能证明本项目已经支持相应操作。

## 产品选择

- 默认使用 Flash。联网与可用工具调用作为默认能力，不向调用 AI 暴露启用开关。
- 用户可直接用自然语言指定应用，例如“去用 Google Keep 查找我的笔记”；`@` 是可选的明确指定方式。
- 不自动选择 Pro。Flash-Lite 只在运行时持有当前账号新鲜的基础 Flash 文本额度耗尽证据时允许，回退时锁定思考强度；真实耗尽后的成功回退仍待验证。
- Flash 的低、中、高思考级别与请求协议字段的对应关系尚待核对，不能假定现有 standard/extended 已覆盖三档。

默认功能面通过 `gemini_ask` 接收普通对话、当前联网问题和自然语言应用请求，返回观察到的来源与 grounding 状态；旧五工具 assist 兼容入口保留 `gemini_search`。这些入口不表示 MCP 当前已能可靠执行跨应用操作。可用性仍取决于账号、地区、语言、设备和应用连接状态。

## 官方能力与已观察界面

Google 的 [Connected Apps 使用说明](https://support.google.com/gemini/answer/13695044?hl=en) 描述了自然语言请求、可选 `@` 指定应用和连接应用的方式。[应用能力与要求表](https://support.google.com/gemini/table/17434654?hl=en) 按应用列出支持的操作、可用条件和适用体验；不同应用在 Chat 与 Task 的支持范围可能不同。

[Task 说明](https://support.google.com/gemini/answer/17094507?hl=en) 描述了委托执行、工作面板、继续指示及浏览器操作。默认用 Chat 回答、写作、分析和完成受支持的直接 App 请求；需要持续执行、跟踪工作或操作网站的委托再选 Task。Chat 同样能处理多步骤请求、修改 App 数据和运行定时内容。Task 与 Chat scheduled actions 不等同，不能将现有定时操作当作 Task 的实现。

此前界面观察到的 Chat 模型为 3.5 Flash-Lite、3.8 Flash、3.1 Pro 和 4 Argon；Flash 显示低、中、高三个思考级别。以下是当时看到的入口，不代表所有入口均已验证可执行：

| 界面位置 | 观察到的应用 |
| --- | --- |
| Chat 的 `@` 菜单 | Keep、Tasks、文档、Drive、日历、联系人、YouTube Music、Notebook、Gmail、VerifyAI、Workspace |
| Task 的 `@` 菜单 | Keep、文档、Drive、联系人、YouTube Music、Notebook、Gmail |
| Connected Apps 已安装目录 | Workspace、搜索服务、YouTube、联系人、YouTube Music、Notebook、VerifyAI、GitHub |

某应用没有出现在某个菜单中，不能单凭这一点判断它不可用；需要结合具体应用、任务和官方能力表验证。目录条目也不等于本项目已能调用该应用。

2026-11-01 核对的官方表明确列出：Workspace 与 Notebook 支持 Chat／Task；原生 GitHub 仓库导入及代码问答为 Chat-only，Contacts 连接器为 Task-only。这里按具体连接器和操作判断，不能将某条浏览器或自定义 MCP 路线一并判为不可用。[自定义 MCP 应用](https://support.google.com/gemini/answer/17209137)同样可用于 Chat 直接请求与 Task 工作流。原生 Skills 也支持两种模式；添加 App、Notebook 或 Skill 本身不要求切换模式。

## 当前 MCP 接入

默认功能目录使用 `gemini_ask` 处理普通问答与联网问题；提示可自然指定应用，默认 Flash，不增加联网或 App 启用开关。响应保留观察到的来源与 grounding，应用修改的完成仍须独立证据。

NotebookLM／Gemini Notebook 已有明确接入：账号 `gemini_notebooks` 默认从网页来源选择器返回紧凑元数据，功能面的 `gemini_ask(notebook_ids=[...])` 启动后台普通聊天，`execution_mode="chat"`。Notebook 附件不会将聊天转为 Task。`source="gemini"` 保留同一 Notebook 产品的项目视图及聊天整理；移动前必须在该视图精确解析目标，不能自行替换资源前缀。

`gemini_run_task` 明确启动 `execution_mode="task"` 的后台工作，支持相同的 Notebook 来源选择。提交返回持久 `operation_id`，状态只读元数据；完整回答按原 CID/RID/候选定位，保存并校验本地产物后再清理自有线程。账号 `gemini_tasks` 提供 Task list/get 与显式删除。它们不会把 Task 的计划管理重定向给聊天 scheduled-action 工具。

完整示例、模式选择与特殊设置见 [Notebook、聊天与 Task](notebook-task-workflows.md)。当前已实现有限进度、显式确认卡片、Task 时间计划和有限远程浏览器输入；运行中任意追加指示、独占接管、Task 暂停／恢复、自动权限审批、事件型计划和导出尚未适配；持久浏览器／动作权限偏好通过 `gemini_preferences` 管理。

### 协议来源与实验边界

生产请求通过集中 registry 使用已观察的 legacy 合同：Notebook 来源目录使用 `ListNotebooks`，Task 模式与普通聊天保持区别。普通 Notebook 聊天的 File 引用由网页同等的 protobuf／base64 编码构造，不需要假造上传合同；Task 可以使用直接 Notebook 附件。引用不是另一种认证凭据。

公共 [聊天会话模块](https://www.gstatic.com/bg3/boq-bard-web/islands/assets/useChatSession-Bco4Wfpn.js) 中可见 `remyProcessingState`、步骤与工具事件，以及独立的 Notebook 附件构造。此前检查的工具同意和 TaskConfirmation 结构并未建立完整权限提交合同；包含 ScheduledTask 的结构不能直接当作新 Task 模式。

[WebChannel 编解码](../src/infrastructure/webchannel.py) 和 ProcessSession 的授权握手／响应探测属于独立研究。握手成功、看到 CID 或收到控制帧都不足以证明完成生成；不将未完成的实验描述为生产支持。当前已验证的 Notebook／Task 路径不依赖生产 ProcessSession 适配。

## 尚待适配的控制

1. 扩展丰富 Task 步骤和独占浏览器接管；当前显式卡片与有限输入不能当作完整工作面板或暂停／恢复。
2. 扩展事件触发、通知设置和导出；已支持的时间计划不能复用 Chat scheduled actions 的对象标识。
3. 按具体应用核验外部修改，扩展拒绝、未知和等待用户输入的恢复证据；查看目录或收到任务结果本身不证明应用操作成功。
4. 持续验证跨进程恢复、同账号约束与完成后清理。运行中、等待结果或结果未知的任务保持清理保护，不能因暂时未返回就删除。

官方文档、界面观察、离线协议测试和授权真实调用分别记录；只有具体工具和操作得到核验后才标为可用。
