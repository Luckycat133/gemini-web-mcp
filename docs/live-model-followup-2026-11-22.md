# 7c 候选包独立补测（2026-11-22）

本页保留 `7c2757c28cc21ce5` 原八项补测快照、两个顺序独立尝试，以及最终交付包 `fcdf1138268623cc` 的原句柄恢复证据，产品版本仍为 `0.20.0`。原八项最初四通过／一拒绝／两本地超时／一 pending；最后一项已由新包只读恢复为明确拒绝并完成清理。连同两个顺序尝试，十次生成的当前本地结果为四通过、两拒绝、四个身份未知的视频超时，不宣称远端均已结束。[ba8e 原 19 项报告](live-model-matrix-2026-11-22.md)保留其 12 通过／1 链路未验证／6 错误，不回填旧矩阵。

## 原八项结果（followup8，attempt 1）

本轮通过真实新 stdio 连接、`media` profile 的 `gemini_generate_media` 执行。补测先完成 Neon 两图生成，再用它的真实保存产物编辑；媒体调用并发最多两个。各项绑定同一 final7 安装身份。

此表固定为原八次生成调用，不用后追加的同模型记录覆盖。同句柄 drain 是读回，不是新增生成；随后 H／N 视频顺序独立尝试属于另一 round，各自保留 attempt 与结果。

| 模型／工作流 | 当前结果 | 已核验的证据 | 清理 |
| --- | --- | --- | --- |
| Neon 有序双参考图 | 通过，51.15 秒 | 精确 `gemini-neon`，2752×1536 图片；两张参考顺序与画面已核验 | `completed` |
| Neon 保存产物续编 | 通过，47.11 秒 | `source_mode=saved_operation`；SQL 精确父产物关联、哈希及人工视觉通过，2752×1536 | `completed` |
| Carbon 单参考图视频，同步等待 | 通过，146.12 秒 | 真实 `background=false` 和一张 `image_path`；初次调用 `inline_initial_state=local`，保存 10.026667 秒、1280×720 H.264＋AAC 视频 | `completed` |
| Carbon 音乐 | 通过，101.09 秒 | 64.733333 秒、1024×1024 H.264＋AAC MP4，44.1 kHz 双声道；非静音信号已验证 | `completed` |
| Neon 音乐 | 明确拒绝，88.08 秒 | 精确 `gemini-neon` 回复，`ARTIFACT_NOT_RETURNED`／`model_media_refusal_observed`，没有音频 | `completed` |
| Hydrogen 视频 | 身份观察前超时，31.03 秒 | 原 worker 已退出，持久 `TIMED_OUT`／`partial`；没有回复身份，原始派发未核实 | 远端缺席未验证 |
| Neon 视频 | 身份观察前超时，31.03 秒 | 原 worker 已退出，持久 `TIMED_OUT`／`partial`；没有回复身份，原始派发未核实 | 远端缺席未验证 |
| Hydrogen 音乐 | 待原句柄最终读回 | 434.17 秒快照为 `partial`／`wait_elapsed`，精确回复身份已观察，尚无可验证产物，无 error_code | 保持保护，不重发 |

上表固定为 7c 原快照；Hydrogen 音乐在该时点不能算已确认的失败、取消或终止，后续最终包补证见下节。无身份视频的 `cleanup=not_applicable` 不能当作远端不存在证明。Neon 音乐仅证明本次显式 Short＋Instrumental 请求被拒绝，不推断所有 Neon 音乐请求均不可用。

## 独立 QA 与实际限制

六个保存文件的字节数和 SHA-256 均已独立匹配。Neon 的父图片与续编图已实际查看，符合左红圆／右蓝方块及续编新增居中绿三角的合成要求；保存产物链经过精确数据库关联验证，不使用本地控制图冒充。

Carbon 视频的第 1 秒和第 8 秒帧已检查，显示蓝色纸立方体旋转；ffprobe 确认 H.264 视频和 48 kHz 双声道 AAC 音轨。此次验证了 native 单参考图、主入口同步等待返回 local 和已保存视频／清理的端到端路径。它没有验证所有模型、15 秒／竖屏、默认输出目录、自定义文件名或 compact 同步 live 路径；后几项行为合同仍由针对性离线测试覆盖。

Carbon 音乐 MP4 为 3,659,520 字节，音轨信号平均约 −14.4 dB、峰值约 −0.3 dB，验证为非静音；没有声称人工试听。含音轨 MP4 是有效音乐产物，不要求必须另有 MP3，也不从聊天选择器推断隐藏生成器版本。

最初五个精确目标的独立只读清理复核已通过；最终又包含 Hydrogen 音乐补证，共六个已知目标全部 canonical 缺席且账号绑定有效，发送零次修改。这里只补证已有清理，不把 already absent 计为新删除，也不覆盖四个没有身份的视频。

两个 31 秒视频超时不是 observer 的 30 秒轮询提前退出：原 worker 已退出且错误已持久化。SDK 存在两类约 30 秒初始化窗口，可作为候选解释；但该 worker stderr 没有保留具体阶段，因此不能断言历史请求必然卡在初始化、必然未 Send，或由缓存竞态导致。只读诊断结果待另行补证。

一次隔离 Default 的 no-Send SDK 初始化探测在 9.785 秒通过，未复现该超时；它不能回溯证明旧请求的派发阶段。随后两个顺序视频独立尝试见下节，既不重放旧句柄，也不改写原八项记录。

Hydrogen 音乐的独立 exact 读回证明唯一精确回复已完成（completion flag 2）、富媒体与 URI 均为空，并明确表达工具／会话无法提供音乐；旧分类谓词未匹配这一措辞，曾导致操作持续 pending。最终包的同句柄分类与清理补证见下节，不公开原始回复内容。

该原句柄在旧包另一次 93.09 秒只读恢复仍为 `partial`／`wait_elapsed`，不算新增生成。旧只读观察器按精确进程身份退休，回复元组与保护保留，没有发起远端生成或取消。新交付包补齐拒绝措辞和首个可见文本的引用／代码示例保护；后部无关引用不遮蔽真实拒绝，精确 raw completed-empty gate 未放宽。

## 顺序视频独立尝试（video_sequential，attempt 1）

| 新独立 case | 本地结果 | 远端证据边界 |
| --- | --- | --- |
| Hydrogen 视频 | 219.11 秒，`TIMED_OUT`／`native_media_outcome_unverified` | 内部有发送尝试，但没有 CID／回复身份；远端是否受理与结果均未核实 |
| Neon 视频 | 31.01 秒，`TIMED_OUT`／`media_outcome_unverified` | 没有身份；不能将其断言为已证初始化阶段或未发送 |

两项是新独立生成尝试，不是重放旧操作；均未取得视频产物，不能计为通过。与原八项分开保留 round／attempt；无身份状态不支持远端缺席或取消声明。

## 本轮候选安装身份

本轮候选 wheel SHA-256 为 `7c2757c28cc21ce5056992be356f60087f13c94bcbdb9e2c8d5e9a9966a88a65`。该轮部署时，共享稳定命令 `~/.local/bin/gemini-mcp-function` 的 `current` 切至该构建，fresh stdio 请求记录也绑定 `7c2757c28cc21ce5`。这是候选快照，不替代后续交付身份。已验证：

- 143 个源码／data 文件逐字节一致，wheel 与安装包额外文件均为 0。
- 47 个安装 distributions（含项目）中，46 个固定依赖与根虚拟环境版本差异为 0。
- Ruff、Mypy（152 个源文件）、4,676 项完整离线测试、2,499 项 retained contract 和 9 项 manifest 检查通过。
- 六个安装入口、current／legacy 协议及 profile 检查通过。

Worker 使用独立、权限 0700 的临时 SDK Cookie 缓存，父进程和用户缓存保留，防止并发 worker 写入／chmod／删除共享缓存的冲突。这是隔离修复，不是历史 `INTERNAL_ERROR` 根因已被证实。

补测使用的 fresh stdio 绑定 7c；这不代表 App 活动连接更新。最终交付与 App 状态分开见下节。

## 最终交付与同句柄补证（fcdf）

最终 wheel SHA-256 为 `fcdf1138268623cc4ccdc5491002e74292947958d50dcf12770d1457d7b62d15`，共享 `current` 已切至 `fcdf1138268623cc`，产品版本保持 `0.20.0`。143 个源码／data 文件字节完全一致、wheel／安装包额外文件均为 0；47 个 distributions 中 46 个固定依赖零差异。Ruff、Mypy（152 个文件）、4,699 项离线测试（58.58 秒）、2,522 项合同检查（28.79 秒）、clean-wheel 六入口、无鉴权文本／本地图像 smoke、current／legacy 协议及 profile 检查通过。另有 50 项 browser 引用保护和此前 124 项严格拒绝检查的独立审查证据，未放宽 raw completed-empty gate。

Hydrogen 音乐使用**同一原句柄**在最终运行时只读恢复，16.07 秒返回 `failed`／`ARTIFACT_NOT_RETURNED`、`model_media_refusal_observed`、阶段 `media_rejected`，清理 `completed`。生成运行时仍为 7c，读回运行时为 fcdf；没有新生成或发送。这个新读回事件与原八项 pending 快照分别保留，不能作为第十一次生成。

最终独立只读检查确认六个已知精确目标全部 canonical 缺席，账号绑定均有效，零次修改。补测库共十个操作：六个有身份目标清理完成，四个无身份视频操作保持保护，live worker 数为 0。没有遗留本轮自有 worker，不表示这四个未知远端目标已缺席或已取消。

App 活动连接仍为旧 `655d1612cb725167`，曾出现 `AUTH_REQUIRED`，尚未验证重载至最终 fcdf。共享稳定入口、新 stdio 最终运行时读回与 App 活动进程分别核验，不能声称 App 已完成切换。

## 脱敏结构报告与未决项

[Schema 报告](live-model-followup-2026-11-22.json)列十次生成的当前本地结果，Hydrogen 音乐通过最终包的同句柄读回补齐，不新增生成计数。[补测证据 sidecar](live-model-followup-2026-11-22-evidence.json)保留原八项、两个新生成尝试和两次同句柄只读恢复，按 round／attempt 区分。Schema 的 `degraded`／`failed` 描述四个视频未知结果带来的本地验收缺口，不声明这些远端任务已终止。RPC／HTTP 未采集项继续使用明确 sentinel `not_observed`、公开代码入口与零／空遥测字段，不伪造网络结果。

实际未决项为四个视频尝试的远端受理／结果与失败阶段，以及 App 最终构建重载／鉴权验证；不把它们表述为已通过。公开材料不含原始响应、账号内容、凭据、远端标识或私有文件路径。这是授权本机 `default` 补测，不是专用 canary 环境或 PR CI。

<!-- APP_RELOAD_UNVERIFIED_20261122: App still needs actual final-build reload/auth proof. Preserve all generation and readback round identities. -->
