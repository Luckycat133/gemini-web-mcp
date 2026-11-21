# 生成套件实施说明

2026-11-14 对齐双 Surface；以下是目标与验收要求，不是当前支持清单。当前合同以 [architecture.md](architecture.md) 为准，实施次序见 [roadmap.md](roadmap.md)。

## G01 — 完善现有 Function 入口

已实现 `src/surfaces/function.py` / `gemini-mcp-function`，不是再新增 generate/create 产品。复用 assist/chat/operation 注册器和 shared services，扩展当前 Function 能力。当前成员以 `src/surfaces/catalogs.py` 为准；不要把 17 个工具当作覆盖全部生成模式的证明或永久上限。

对旧发布的 `gemini-mcp-create` 及 facade 兼容单独建立迁移矩阵：薄适配器或明确的版本化迁移，不能把当前 profile 兼容写成 0.2.2 全兼容。让单装 Function 的 agent 能取得必要能力目录、传入素材、恢复原任务并使用真实产物；不依赖全账号历史或删除权限。

## G02 — 全模型文本、多模态与会话

Owner：现有 chat/understanding/search、session manager、model resolver 与 infrastructure 输入适配；资料 GO01/GO05/GO23/GO26。

支持当前目录模型的显式选择、低/中/高等级、经观察的 auto 路径、临时/普通会话、已有 Gem/Skill/Notebook 的明确引用。录入 audio/video/document/image/URL/local folder/repository/native provider refs 的真实输入形式。目录出现新模型时更新 capability，而非猜模型 ID。Deep Think 处于替代迁移时返回 deprecated/unavailable 与实际替代说明，不冒充仍可用。

保留输入 ID、顺序、接受/忽略/失败证据；服务接受文件不等于模型已分析全部内容。长输入截断/上传失败逐项披露。代码目录上传尊重 ignore/大小上限，扫描凭据文件并预览传输范围；不自动上传整个 HOME 或私密仓库。

已确认 conversation metadata 可续接才标 resume supported；重启、账号切换、并发写和分支漂移测试不可省。对话流聚合和客户端增量输出分开验收，不声称已有 token streaming。

## G03 — 图片全流程与 Pro redo

Owner：function/background/media_jobs/media_generation/media_storage/artifacts 与 infrastructure 请求合同；资料 GO02。

把单 image_path 向后兼容映射到有序 inputs；实现多参考图、基于 artifact/ref 的编辑、连续 turn 和原生 Pro 重做。首次 Pro 文本模型选择不能替代图片 redo；redo 必须绑定真实原图/response/draft 标识并作为新的显式生成记录。保存父子产物和用户要求的目标后端，未知 observed backend 不瞎填。

Lite 的输入/编辑限制来自当前能力目录，不能发送不支持参数后假装已经遵守。独立保存原图和预览，保留 source；Function 当前全部媒体先返回后台 operation；保留此合同，不能无版本说明改回同步图片。对“只改背景保持主体”同时做文件解码与人工/独立视觉核对，文本承诺不算结果。

## G04 — 视频、参考输入与多轮编辑

Owner：function/background/media_jobs/media_generation 与现有输入/operation 服务；资料 GO03/GO20。

适配当前 Web 原生视频页的协议，不把通用聊天 text prompt 当作必然的视频调用。按账号暴露被观察的时长/比例、图片/视频输入、模板、多轮编辑及已授权 avatar 引用；参数既要进入请求，也要核对实际产物。当前文档数值仅是 discovery 种子，不能永久写死为全账号限制。

图片转视频、视频转视频、编辑必须保存父产物、上游会话及操作 lineage。校验真实视频 stream、时长、尺寸和完整解码；静态封面不能充视频。恢复不重复消耗配额。avatar 初始录制、同意与身份流程由本人完成；只引用已授权资源，不伪造录制/同意。

## G05 — 音乐及选项

Owner：background/media_jobs/media_generation/media_storage/artifacts；资料 GO04。

把网页实际可用的短/完整、人声、风格和参考素材选项加入 typed request；不能用 thinking_level 充当音乐版本/长度选择。明确选择器字段和 prompt hint 的区别；缺少协议证据的参数拒绝或提示不支持，不静默接受。

交付真实音频及存在的 cover/video companion，关联同一 operation。不要求每次都存在未承诺的 companion，也不把仅有封面的视频当音频成功。分别测部分下载、MP3 成功而 MP4 失败、签名 URL 过期、重启恢复和本地文件被删后的 truthful failure。

## G06 — Research 从计划到报告

Owner：现有 ResearchService 与 OperationService；资料 GO06/GO08/GO21。

现有报告保存与恢复链已在当前分支实现；先以当前源码和对应证据核对覆盖，再扩展尚缺的 plan/read/edit/approve/start，不沿用旧基线“尚未实现”的判断。简单任务可明确选择自动批准生成的计划；有审阅要求时必须停在待批准状态。计划批准绑定版本，恢复不能重新启动。来源选择区分公共搜索与用户明确授权的 Workspace/Notebook/附件。

提交后保留原 locator，轮询真实进度；报告正文、引用、内嵌图表/附件分别收集并标覆盖。保存 provider-native 报告及可验证导出，不把总结文本改名成研究报告。当前没有公开 cancel 动作；不得虚构 cancel_requested API。未来取消功能需独立定义请求与服务端确认合同。成功标准是完整报告可再次取得和被下游引用，不是 done 字段或“研究完成”句子。

## G07 — Canvas 与原生文件生成

Owner：现有 chat/task/artifacts 与待迁移的 Canvas 服务；旧 canvas/canvas_edits 文件已从当前分支移除；资料 GO07/GO14/GO21/GO23。

区分生成文档、演示/应用/代码产物、原生 chat/task 文件、直接编辑和本地格式转换。请求目标格式后解析原生 artifact 或 download contract；检查 magic bytes/容器结构/内容可读，不能把 Markdown 写成 .docx。服务器侧转换必须 origin=local_conversion 并保留原件。

生成式修改是一轮新 generation，直接 edit 是版本受控 mutation，二者不要混用。Canvas 保真局部编辑若未找到协议，先迁移旧版 plain-text replace 行为并显式告知 formatting_replaced，不包装成已支持富文本编辑。生成 app 的代码可以导出，不能在开发机/用户浏览器任意执行或授予 OAuth。

## G08 — 其他原生生成模式与退役处理

Owner：shared generation services、native mode parser、Canvas/研究转换；资料 GO08/GO09/GO25/GO26。

覆盖 Audio Overview、Guided Learning、quiz/flashcards/practice test/study guide、storybook、网页后续出现的其他原生类型。每个类型单独列 capability、schema/结果 parser 和 acceptance 子案例，不以“支持 learning”一行遮住缺失项。

Audio Overview 必须有可播放音频；storybook 必须有可读取的页/图片顺序；交互学习产物要有真实对象与交互/导出方式，而不是普通回答提到“测验”。原生 selector 已退役时记录替代和账号状态，不能硬调历史 enum。UI 可见但未发现协议的路径走 evidence discovery 或显式可选 browser adapter，不伪造 support。

## 每一条生成路径的共同测试

合法输入、空输入、超限/类型不支持、拒绝、限额、认证过期、改变模型目录、畸形回包、断流、只返 locator、错误媒体种类、下载失败、磁盘满、已存在目标、重启/硬杀、并发重复 key、不同请求复用 key。对原生参数增加“请求 selector 与实际结果不一致”的负例。不同模型的同类成功不得外推为全模型通过；未知配额不靠暴力重复请求探测。

## 当前边界补充

普通 session 与 recover_only 仍为进程内能力；A10 的跨进程续接是待实现目标。图片已异步；视频已有单 image_path 输入，后续工作是多输入/连续编辑和网页原生选项，不重复包装已有调用。显式 `gemini-omni-1.2-flash` 是付费 API 路径，3–15 秒参数不能假称 Web 订阅选项，不能自动降级到付费请求。报告 audio_overview/custom_app 目前是脚本/规格，原生音频或部署仍需独立实现与验收。
