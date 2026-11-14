# 管理套件实施说明

2026-11-14 对齐双 Surface。以下为全范围目标，不是当前已实现声明。优先恢复已发布但未迁移的能力；见 [roadmap.md](roadmap.md) 与 [architecture.md](architecture.md)。

## M01 — 全资源清单、读取与统一检索

Owner：现有 history、account registration、Notebook/Task services；Library/Canvas/sharing 需按旧发布行为迁移；资料 GO10/GO11/GO15。

建立跨 chat/task/message/attachment/artifact/library/notebook/source/canvas/gem/skill/schedule/share/settings 的 ResourceRef 索引与关系。资源类型可以分阶段接入，但完整清单必须保留每一类的 coverage。管理多个账号需要用户明确选账号，默认只操作当前一个，不自动汇总账户隐私。

分页用稳定 cursor 或服务端 snapshot 标识；上游不支持快照时标 best_effort，并处理翻页期间新增/删除/重复/排序变化。逐源游标保存，循环 token 有界报错；不能反复 offset 从第一页重扫导致 O(n²)。完整读另有预算和 continuation，默认摘要可短但必须标 truncated。

默认在已授权范围按需正文检索；可选本地 FTS 索引需明确启用、账号隔离、增量更新、远端删除传播、可清除和 stale 标志。语义检索是后续策略，不默认引入外部 embedding 服务。返回命中片段、来源、完整性及未扫描来源；“没找到”不等于“全账号不存在”。

## M02 — 完整导出、备份与可验证恢复

Owner：history export/artifacts，新增独立 backup service；资料 GO21/GO22/GO29。

导出消息顺序/分支、metadata、附件、媒体、来源引用及 Notebook 结构，生成版本化 manifest + 相对路径 + 每个文件 SHA-256。流式写入，原子完成标记、游标和断点续传；软失败逐项留缺失原因。只拿到前 N 轮的导出必须标 partial。跨页一致性只能按实际提供的快照保证。

校验备份离线可读、hash 一致、没有路径穿越和意外绝对路径；二次导出增量但不覆盖用户自改文件。导入/恢复先 plan/dry-run，重新映射 ID、检测冲突并只用上游已支持的写入动作。导出成功不表示可恢复原会话 ID、时间戳、版本史或所有引用。用户提供的官方 Takeout 归档可作为补充解析来源，不能自动改成全 Google 数据导出或假称它等同在线完整快照。

## M03 — 可恢复批量操作

Owner：新增 batch service 复用状态/账号服务。

两步 plan/apply：固定目标 ID、version、操作参数、影响、过期和用户批准的范围。单个明确操作不强制多余确认；批量删除/发布/大范围变更先预览并沿用已有同范围授权。确认字符串本身不是安全边界，服务策略与调用客户端授权都须生效。

逐项持久化 prepared/submitted/verified/conflict/failed/unknown；部分完成后继续未完成项，不重做成功项，不在执行时重新搜索扩大集合。版本变化先停该项，明确不可能的 rollback；禁止“事务成功”掩盖远端非原子操作。读回可重试，变更不能因网络异常盲目重发。

## M04 — Notebooks 与资料源

Owner：现有 notebook_admin、Notebook/source adapter、chat management 与 infrastructure；按 action 迁移缺失路径；资料 GO11/GO05。

补齐 title/instructions/description 的能力边界、sources 上传/URL/文本/原生引用、增删改、ready/error、全文可得性、chat 移入/移出/成员关系以及当前出现的 study/business subtype。未知字段不得写默认值覆盖原配置。

上游已出现连接式 Gemini Notebook，必须验证映射而非继续假定旧 bot ID 永久有效。不能自动拿 Gemini Web Notebook ID 当 NotebookLM 附件 ID。网页菜单支持、RPC 被接受、来源索引 ready、原文读回一致是不同层级。

move 后从目标成员清单核对；若历史列表不含 moved chat，应走准确的目标读取路径，不反复 rename 来“试”。不支持的 move-out/description/source-content 保留明确缺口，不能返回完成。

## M05 — Skills、Gems 与迁移

Owner：现有 managed_skills/gems 与 infrastructure；ZIP 路径需迁移，不能假称已存在；资料 GO12/GO13。

补齐 native Skill 内容/附件的读改、ZIP 导入/替换/导出、开关，保留 Gems 的现有兼容调用和知识文件引用。分离本地 Prompts、运行 Skill、Gemini 原生 Skill 的 ID/目录/许可。

历史资料把 Gems→Skills 列为迁移需求；实施时重新核实当前官方说明与账号状态，不假定所有账号已切换。先检测资源状态，保存原字段/知识文件可得性与旧新 ID 映射，显示丢失/不支持部分。自动迁移已发生时关联，不创建重复 Skill。手动迁移须 preview、创建、读回再记录，永不自动删除旧 Gem。共享关系和外部 Drive/Notebook 引用不应假装已打包进 ZIP。

ZIP 防 Zip Slip、symlink、解压炸弹、重复 root/冲突名、隐藏文件/大小和不支持格式；导入内容不执行。生成套件按显式选中的新/旧资源调用，不自动启用停用的 Skill。

## M06 — Library、Canvas 与文件管理

Owner：待迁移的 Library/Canvas 行为，复用当前 artifacts、account surface 与 RPC boundary；资料 GO07/GO21。

Library 分页与类型过滤要区分模板/能力库和用户真实产物；获取原文件、来源、版本和可用操作。快照 ID 变化时必须重读身份，不能仅凭标题匹配删除。统一关联 conversation、Canvas 与 Library 中的同一产物，但不凭相同文件名去重。

Canvas 提供有类型的 get/versions/export/direct edit；保真编辑只修改目标内容并校验完整结构、引用和版本。期望版本不匹配报 conflict，不最后写入覆盖。UI 上不能独立删除的文档明确拒绝；更大范围的来源删除必须另行授权。

导出原生文件与本地转换分开标注。重命名、下载、删除、分享等 action 按具体类型发现，不对所有 Library 资源宣称同一 CRUD 能力。

## M07 — 任务、日程与分享

Owner：现有 web_tasks/task_schedules/scheduled/history；sharing 行为待迁移；资料 GO14/GO15/GO16/GO17。

管理 native Task 的列表、进度、needs_attention、详情、输出、thread rename/pin/move/delete 及上游支持的 cancel。删除 thread 不等于取消执行；取消请求不等于已停止。不要把 MCP Tasks 扩展、Gemini Task、Remy schedule 和 legacy scheduled chat 混为一个状态机。

保留 schedule backend 与原始 ID；补全部时间触发、时区/DST、暂停/恢复、run-now、完成/运行记录，以及有证据的条件触发创建/更新。事件 trigger 缺协议时显式 blocked，不把它退化成时间轮询而不告知。创建测试 schedule 前确保目标是无外部副作用的 fixture。

分享创建是发布，不是只读；确认精确 chat/response/resource 和可见范围后执行。撤销单链接独立读回，处理过期/已撤销、分页遗漏与并发变化；不得在备份时自动创建公开链接。共享 Gem/Skill/avatar/notebook 的协议按种类独立验证。

## M08 — 个性化、连接、头像与设置

Owner：新增按资源类型分离的 settings/memory adapters，经共享 account service；资料 GO18/GO19/GO20/GO27/GO28。

纳入 saved info/custom instructions、memory summary、memory/Keep Activity 状态、自动模型选择、网页可见的语言/外观/水印偏好、Connected Apps/权限状态、avatar 已有资源与分享授权。逐项发现可读/可写与地区/账号限制，不把普通模型回答“我记得你”当作后台存储证据。

全局开关、断开连接、权限撤销、记忆删除、头像发布均需精确影响预览及授权。Keep Activity 修改不能作为其它功能的隐式前置动作；保留原状态但仅在本次授权允许时恢复，不能覆盖用户期间所改设置。账号登录、OAuth 同意、相机/麦克风及本人录制仍由用户完成。

只使用供应商允许的水印设置，不删除溯源标记、不伪造来源。管理 Gemini 内的连接状态不等于获得邮箱发送、日历删除、付款或所有 Workspace 文件管理权；这些外部副作用必须另有范围授权，不纳入本项目默认账号清理。

## 全量覆盖的约束

“所有内容”意味着所有资源种类与已发现 action 都进入矩阵，不意味着任何账号都能绕过地区、年龄、订阅或管理员限制。不能把拒绝当空资源，不能为了宣称完成而移除难实现项。必须交付明确能力状态和可重现阻塞；其余未阻塞工作继续开发。

## 当前实现边界

Account 当前是 17 个扁平参数工具，不是发布版七个 request facade。任务/Notebook 管理与偏好已实现部分动作，但这不意味着 ZIP、Library、Canvas、公开分享及全部原生日程合同均已迁移。逐 action 记录保留/迁移/计划/明确退役/阻塞。Notebook 删除可能连带对话；cleanup 仅限 MCP-owned ledger，不能以 title 匹配扩大范围。Task schedules 与 Chat scheduled actions 分开；Task 删除不等于取消。存储偏好不证明运行时效果，浏览器连接/确认/输入不可混为一谈。
