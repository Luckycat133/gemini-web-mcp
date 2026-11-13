# 验收案例与独立判定

这些是实施后的验收要求，不是已执行结果。每项的门禁在 plan.json 中；一个案例里列出的不同类型/选择器需展开为子案例并逐一记录，不能只跑一个代表就声称全部支持。

真实账号用最小、明确拥有的 fixture；不为了负例制造真实超额、泄露或损坏。离线故障注入可以用合成数据，live 字段必须来自真实 MCP→上游路径。无法使用的能力仍保留待验收/受限状态。

每次记录 implementation revision、测试/客户端/依赖版本、UTC 时间、账号类别和授权范围摘要、case 子项、结果与独立 oracle、产物校验/资源清理证据；不提交原文、Cookie、私密 ID 或生成文件。

## A01 — 基线与范围
门禁：`docs`。
执行：在固定提交上导出实际 entrypoint/tool/schema 清单，重读变动来源，核对已有代码与历史实测。
通过条件：所有能力有 owner、来源、状态与未支持项；没有把历史实测当成今天的结果。

## A02 — 错误读取与只读隔离
门禁：`offline protocol`。
执行：构造 None、错误 CID、畸形 envelope、缺页和未授权 reset/cleanup；实际 stdio 调用只读目录。
通过条件：无 empty success/误删；只读调用不能通过 action 绕过策略；返回正确错误/partial。

## A03 — 兼容参数与发布状态
门禁：`offline protocol`。
执行：对旧 profile/name/schema 跑 golden；测试 low/medium/high 与 standard/extended；核对 release 文档。
通过条件：旧客户端可调用，别名含义一致，未知值拒绝，文档不再误称已发布版本待发布。

## A04 — 动态能力目录
门禁：`offline live`。
执行：fixture 加入新模型、退役模型及账号切换；授权账号读目录并匹配真实网页。
通过条件：目录不串账号，缓存过期可刷新，未知 ID 不硬编码；supported 与 availability 分开。

## A05 — 后端与额度证据
门禁：`offline live`。
执行：正常单次调用核对 requested/effective/observed；用 fixture 模拟配额下降和后端替换，禁止耗尽真实额度。
通过条件：不静默降级；无法观测的 backend/quota 为 unknown，不以文本自报冒充证据。

## A06 — 混合输入安全
门禁：`offline live`。
执行：授权上传最小 text/image/audio/video/file/native refs；离线测穿越、SSRF、ZIP 炸弹、大小/类型限制。
通过条件：顺序与 input ID 保持，拒绝输入零提交，原生 ID 不猜测，忽略/失败输入明确。

## A07 — 产物交付
门禁：`offline protocol`。
执行：以真实合成可解码文件经 stdio 返回原件、预览、resource/link；模拟远端客户端。
通过条件：bytes/hash/mime 正确，远端不依赖服务器绝对路径；local conversion 标明来源。

## A08 — 幂等与并发
门禁：`offline`。
执行：两进程相同 key 同 payload 并发；不同 payload 复用 key；租约过期和账号错配。
通过条件：同任务仅一个 start，冲突不串结果，错误账号无法读句柄；无 prompt 明文持久化。

## A09 — 硬中断与迁移
门禁：`offline live`。
执行：在 reserve/start/locator/save/cleanup 边界注入故障；授权做一次 provider 已有 locator 的硬杀恢复；加载旧 DB。
通过条件：不重复生成；提交未知保持 unknown；迁移不丢定位；未保存结果不删来源。

## A10 — 会话续接
门禁：`offline live`。
执行：同一会话两轮生成后重启、跨客户端续接；模拟换账号、凭据刷新、并发 turn。
通过条件：续接真实上下文且分支正确；不能验证 lineage 时拒绝，不重放历史 prompt。

## A11 — 交互 setup
门禁：`offline package`。
执行：临时 HOME/config 中测 TTY 与非 TTY、配置预览、原子写、备份回滚、诊断失败和 auth-free preflight。
通过条件：不破坏已有配置，拒绝未经确认写入；默认不读 Cookie/不请求 Gemini；stdout 不污染协议。

## A12 — 两套选择
门禁：`offline protocol agent`。
执行：新生成入口实际 list/call，旧五面兼容 smokes；让独立 agent 区分生成/管理和混合任务。
通过条件：单装生成即可工作；不需全账号删除权限；旧工具/schema 不意外变动。

## A13 — 所有可用模型
门禁：`offline live`。
执行：按当前目录对每个可用模型及不同 selector 类做有界文本/多模态代表调用；测试临时会话和 auto。
通过条件：提交/响应选择一致，受限变体单列；未实测变体不可标 live accepted。

## A14 — 原生资料与代码导入
门禁：`offline live`。
执行：最小授权本地文件/目录、仓库、Drive/Photos/Notebook 类引用分别验收；测 source 不存在/未连接。
通过条件：真实素材到达对应 native 输入；不以下载文本替代原生引用而不披露；仓库未泄漏隐藏凭据。

## A15 — 多参考图与持续编辑
门禁：`offline live`。
执行：两张可区分合成参考图→组合生成→用结果继续修改；中途断开再取原任务。
通过条件：图片独立解码；人工核对对应参考和修改；lineage 连贯，无重复 start。

## A16 — 图片 Pro redo
门禁：`offline live`。
执行：对已生成的明确图/response 发起原生 redo；测无原图/无权限/错误类型。
通过条件：请求包含已观察的 redo 身份，记录新 operation/parent；不把 model=pro 当完成证明。

## A17 — 视频输入与参数
门禁：`offline live`。
执行：有界文生/图生/视频参考调用，逐一测试被声明支持的比例/长度/模板 selector。
通过条件：所需视频 stream 存在并完整解码；实际尺寸时长匹配容差；不以封面代替。

## A18 — 视频多轮与 avatar 引用
门禁：`offline live`。
执行：同一来源视频继续编辑、重启恢复；仅使用已有明确授权 avatar，未有授权时拒绝。
通过条件：保留父视频/会话，不重新创建原任务；不伪造录制或共享授权。

## A19 — 音乐选项
门禁：`offline live`。
执行：对声明支持的短/完整、人声与风格路径做有界调用，参考素材单独测试。
通过条件：选择器真实发送，音频完整解码；实际长度记录，不硬认 prompt/思考等级即生效。

## A20 — 部分媒体与下载失败
门禁：`offline`。
执行：音频成功/伴随视频失败、签名 URL 失效、文件被移走、磁盘满、只返封面。
通过条件：partial 和缺失原因准确；成功文件保留；恢复不再生成也不提前删来源。

## A21 — Research 计划与批准
门禁：`offline live`。
执行：创建/修改计划后批准对应版本，保留 handle；模拟陈旧批准和启动响应丢失。
通过条件：计划未批准不启动；原计划一次 start；陈旧版本不覆盖用户修改。

## A22 — 报告真实交付
门禁：`offline live`。
执行：一次研究取得正文、来源和附件，停止所有服务后用同 handle 取回并交给下游。
通过条件：报告文件可读且内容/引用完整度可核对；done 无正文不成功；取消未确认不报 stopped。

## A23 — Canvas 原生生成
门禁：`offline live`。
执行：文档、代码/app、演示及声明支持的原生类型逐项生成并继续修改。
通过条件：类型、源身份、版本/结果匹配；代码不自动执行；未支持类型独立列出。

## A24 — 原生文件格式
门禁：`offline live`。
执行：各声明支持文件格式逐项生成/下载；离线让伪扩展名和损坏 ZIP 容器通过负例。
通过条件：独立 reader 可打开且包含任务内容；不靠扩展名成功；转换明确标 local_conversion。

## A25 — Audio Overview
门禁：`offline live`。
执行：从授权资料或报告生成音频，恢复并下载。
通过条件：可播放音频与原材料关联，不以音乐/朗读脚本代替原生 overview。

## A26 — 学习模式矩阵
门禁：`offline live`。
执行：Guided Learning、quiz、flashcards、practice test、study guide 每项独立 fixture/真实案例。
通过条件：每项都有真实结构/交互或明确退役证据；一个 quiz 成功不能代表全部类型。

## A27 — Storybook 与退役模式
门禁：`offline live`。
执行：最小故事书检查页图、顺序、版本和导出；读取当前 Deep Think/替代模型状态。
通过条件：故事书不是纯文本；退役能力记录替代/不可用，不强制旧 enum 或自动换模型。

## A28 — 跨资源完整清单
门禁：`offline live`。
执行：预置已知 fixture 集合跨 pinned/unpinned/Notebook/Task/Library；离线测循环页 token 和并发变动。
通过条件：逐源 coverage、continuation 和去重正确；不漏 moved 对话、不空报读取错误。

## A29 — 全文检索
门禁：`offline live`。
执行：命中放在标题之外、旧页和长对话末尾；对 opt-in 索引测试更新/删除/清除。
通过条件：结果可定位，缺页/截断明确；索引不默认外发内容，不串账号。

## A30 — 全量备份
门禁：`offline live`。
执行：对已知资源集导出正文/附件/关系，故障中断后续传，离线重新打开。
通过条件：manifest hash/相对路径一致，缺失项明确；截断不能标 complete。

## A31 — 恢复与归档导入
门禁：`offline live`。
执行：读取合成 Takeout/备份并 dry-run；仅对上游支持类型执行小范围恢复，测 ID 冲突。
通过条件：不覆盖现有资源，不宣称恢复原始 ID/版本；不支持部分完整列出。

## A32 — 批量变更预览
门禁：`offline live`。
执行：对 fixture 列出 rename/move/pin/delete 计划，明确批准后执行并逐项读回。
通过条件：实际目标与预览一致，无搜索扩张；同范围批准不反复打断。

## A33 — 批量部分完成
门禁：`offline`。
执行：第 N 项后崩溃，目标版本变更，批准过期，操作结果不确定后恢复。
通过条件：已完成项不重做，陈旧项 conflict，unknown 先读回，不宣称远端事务回滚。

## A34 — Notebook 类型与身份
门禁：`offline live`。
执行：对声明支持普通/study/business 类型及字段执行创建读改，检测新旧命名空间映射。
通过条件：字段保留，实际种类匹配；不存在或错误 namespace 的资源拒绝。

## A35 — Notebook 源与成员
门禁：`offline live`。
执行：text/URL/file/native source 各验收 ready/read/update/remove；移动 fixture chat 并读取成员。
通过条件：索引 ready 不充全文一致；目标成员与源状态独立核对；未实现动作不返回成功。

## A36 — Skill 文件生命周期
门禁：`offline live`。
执行：ZIP 导入、读/改附件、替换、启停、导出；恶意 ZIP 离线拒绝。
通过条件：附件 hash/字段正确；外部 live refs 标 omitted；不执行脚本或自动启用。

## A37 — Gems 迁移
门禁：`offline live`。
执行：观察账号迁移状态，先备份 fixture Gem；对支持的迁移路径建立旧新映射并二次运行。
通过条件：不重复创建、不删除旧 Gem，知识文件/共享损失显式记录；缺迁移账号证据不标通过。

## A38 — Library 精确管理
门禁：`offline live`。
执行：跨类型分页、下载并删除允许的 fixture image；测试更新后 snapshot ID 和不支持删除的文档。
通过条件：身份精确，文档单删不支持时拒绝，不删整条来源当替代。

## A39 — Canvas 版本与保真
门禁：`offline live`。
执行：有引用/格式的 fixture 上局部编辑、版本冲突、导出；旧 plain replace 单独验收。
通过条件：不丢无关结构；冲突不覆盖；plain replace 明示格式丢失。

## A40 — Task 生命周期
门禁：`offline live`。
执行：提交无外部副作用 fixture，读取进度/needs_attention/输出，组织 thread 并验证支持的取消路径。
通过条件：删除 thread 与取消不混淆；恢复原任务，不自动批准外部动作。

## A41 — Schedules 完整矩阵
门禁：`offline live`。
执行：time triggers 每种构造测试、DST/时区/暂停恢复/删除；授权有界 run-now 与支持的 event fixture。
通过条件：保留 backend/ID；每种宣称的触发契约与执行分别有证据；不把轮询伪装原生事件。

## A42 — 分享与撤销
门禁：`offline live`。
执行：只发布获授权 fixture，读取确切范围后撤销；测 stale token/分页/既有共享。
通过条件：确认发布与撤销成功，备份不创建共享；不删除其它链接。

## A43 — 记忆与设置
门禁：`offline live`。
执行：在隔离 fixture 账号读取并修改一个授权字段，测试未知/拒绝/并发变化；逐项补声明支持的设置。
通过条件：独立状态读回而非模型回答；不为其它功能暗开 Keep Activity，不覆盖用户新值。

## A44 — 连接与 avatar 权限
门禁：`offline live`。
执行：读取可见连接和已有授权 avatar；对允许范围做撤销/管理 fixture，登录与录制仅人工。
通过条件：不获得额外 Workspace 权限，不绕过同意；共享与外部动作另有批准。

## A45 — 两套端到端交接
门禁：`protocol live agent`。
执行：管理找旧图片→生成编辑→归档；研究→文件→Notebook；独立 agent 完成任务。
通过条件：两套使用相同合法引用，实际产物在目标可用，不把技术 receipt 当最终交付。

## A46 — 清理与持久作品
门禁：`offline live`。
执行：任务/Notebook hold 与 cleanup 并发，进程在交接期间崩溃；对 disposable fixture 验证清理。
通过条件：已保留或未交付来源不删；授权临时来源最终独立确认 absent。

## A47 — 真实 agent 选择
门禁：`agent`。
执行：盲测正例、近似意图、中英改写、恢复 vs 新变体、图片理解 vs 编辑、搜索网页 vs 私人历史。
通过条件：输出真实完成率/调用数/重复 start/误授权，失败保留；fixture route 不是模型分数。

## A48 — 全矩阵 canary
门禁：`live`。
执行：独立授权账号按 capabilities×actions×selector equivalence classes 跑有界 canary 并对账清理。
通过条件：每个发布支持项有当前证据；不可用/未测不充 passed；零越权/误删/隐式重提交。

## A49 — 安装包与客户端
门禁：`package protocol`。
执行：构建 wheel/sdist/Skills，干净环境 uvx auth-free；实际支持客户端中安装并调用两套及旧入口。
通过条件：资源与依赖齐全，无 checkout 偶然导入；协议 modern/legacy 均符合声明。

## A50 — 平台范围
门禁：`package`。
执行：在每个宣称支持的 OS 验证私有文件/凭据权限；不支持 Windows 的路径验证明确拒绝。
通过条件：只报告实际测过的组合；不能把配置 JSON 解析成功当 OS 或桌面客户端验收。

## A51 — 升级与候选
门禁：`docs package`。
执行：核对 metadata/tag、升级/回滚、数据库兼容与 runtime Skills 源/包一致；提交 review PR。
通过条件：不覆盖旧 tag，不自动合并/发布；用户可按说明复现当前候选。

## A52 — 最终证据审计
门禁：`docs`。
执行：对每包状态、revision、case/gate、报告文件 hash 与覆盖账本逐项核对，并运行 --complete。
通过条件：所有要求有真实证据；blocked/skipped 不算完成；受限范围与上游退役明确说明。
