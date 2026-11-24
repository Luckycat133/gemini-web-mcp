# 同包 19 组合验收（2026-11-22）

本页记录 `ba8e0ab20c1bf3b4` 测试包的 19 条调用观察，已全部形成记录，结果有缺口。runner 标记 13 项工具／产物通过、6 项错误；按有序参考图与保存产物续编的严格要求，实际为 **12 项通过、1 项保存产物链未验证、6 项调用错误**。Neon 编辑用了本地合成控制图，不能计为同 cohort 保存产物续编。部分超时请求的远端结果未知，不能把“19 条记录已结束”写成远端任务全部完成。

产品版本保持 `0.20.0`。`ba8e` 是本轮 19 项 cohort 包，不是后续源码修复的最终交付包。[11-21 历史矩阵](live-model-matrix-2026-11-21.md)的 13／19 原样保留，不与本页合算。本轮使用一个真实安装版 MCP stdio client，并发最多两个后台请求。

## 19 组合矩阵

| 网页聊天选择器 | Chat | Task | 有序参考图 | 保存产物续编 | 视频 | 音乐 |
| --- | --- | --- | --- | --- | --- | --- |
| Auto | 通过 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| Hydrogen | 通过 | 通过，清理已补证 | 通过 | 通过 | 身份观察前超时 | 通过 |
| Neon | 通过 | 通过，清理已补证 | 网络错误 | 未验证；本地控制图编辑通过 | 身份观察前错误 | 超时，远端结果未知 |
| Carbon | 通过 | 通过，清理已补证 | 通过 | 通过 | 身份观察前错误 | 模型选择超时，未提交 |

Auto 仅用于 Chat。通过项为四个 Chat、三个 Task、两个有序参考图、两个保存产物续编和一个音乐请求。Neon 本地控制图编辑仅证明该次编辑能力，不补入 G03 保存产物链通过数，也不因上游图片失败而跳过这一证据缺口。

## 通过项与证据边界

四个 Chat 均答案正确、选择器匹配且无回退，清理 `completed`：Auto 为 `gemini-自动`、27.33 秒，思考档不可选；Hydrogen 为 `gemini-4-hydrogen`、14.79 秒；Neon 为 `gemini-neon`、14.96 秒；Carbon 为 `gemini-carbon`、12.19 秒。Auto 不推断其内部子模型。

三个 Task 的答案、本地产物及实际派发选择器均验证通过，耗时 Hydrogen 41.08、Neon 43.07、Carbon 43.05 秒。初始 matrix 快照中的清理字段为 `failed`，随后本地自动重试已全部 `completed`。独立只读检查确认三个 Goal 已缺席、三个精确 canonical 对话已缺席；该检查发送零次修改，不把“本来已缺席”计为新删除。

五张图片均已实际查看并核对保存文件：Hydrogen／Carbon 有序双参考图保留左侧红圆、右侧蓝方块；两者各自保存产物续编保留这些内容并加入居中绿色三角形，来源操作与保存产物链均验证。Neon 本地合成控制图的编辑也满足画面要求，但其 `source_mode=synthetic_local_control`，不是 `saved_operation`。尺寸如下，不宣称像素级保真或原生同对话编辑。

| 模型／步骤 | 已验证尺寸 | 保存产物链 |
| --- | --- | --- |
| Hydrogen 双参考图 | 1376×768 | 本 cohort 原始生成 |
| Carbon 双参考图 | 2048×2048 | 本 cohort 原始生成 |
| Hydrogen 续编 | 2752×1536 | 已验证 |
| Carbon 续编 | 2048×2048 | 已验证 |
| Neon 本地控制图编辑 | 2880×1440 | 未验证，不计续编通过 |

Hydrogen 音乐保存 MP3 和含 AAC 音轨的 MP4，独立 ffprobe 及信号检查确认约 176 秒音频、非静音，平均音量约 −14.3 dB，清理 `completed`。这不证明所有模型、时长或精确隐藏生成器版本均可用。音乐允许有独立验证音轨的 MP4，不保证每次必有 MP3。

## 六条调用错误

| 模型／工作流 | 本地观察 | 证据边界 |
| --- | --- | --- |
| Neon 图片 | 28.06 秒，`NETWORK_ERROR` | 未观察到远端身份，无本 cohort 源图可供保存产物续编 |
| Hydrogen 视频 | 31.02 秒，`TIMED_OUT` | 没有生成身份，未取得视频；历史具体阶段及派发未核实，不写为已证初始化或上游拒绝 |
| Neon 视频 | 10.03 秒，`INTERNAL_ERROR` | 在身份观察前失败，没有可验证视频 |
| Carbon 视频 | 16.05 秒，`INTERNAL_ERROR` | 在身份观察前失败，没有可验证视频 |
| Carbon 音乐 | 64.04 秒，`TIMED_OUT`，`native_media_model_selection_not_submitted` | 模型选择未通过，未提交该生成 |
| Neon 音乐 | 578.18 秒，`TIMED_OUT`，`native_media_outcome_unverified` | 观察到发送但没有 CID／回复身份；远端结果未知，不能写成明确拒绝、取消或远端终态 |

没有身份的记录不能据 `cleanup=not_applicable` 推断远端不存在；该值是初始工具快照，不是独立缺席证明。恢复工作使用精确原句柄，不因超时切换路线重发。Neon 音乐此次显式请求 `short`／`instrumental`；Hydrogen／Carbon 音乐参数留空，保留网页实际默认，不能从提示风格推断已选选项。

## Cohort 构建与后续源码

本轮测试包 wheel SHA-256 为 `ba8e0ab20c1bf3b42d1dc5dcdbfed733e3126191952a3e1bdbd9291d25e0a041`，产品版本 `0.20.0`。构建当时验证了 142 个源码／data 文件与安装包逐字节一致、47 个安装 distributions（含项目）中 46 个固定依赖零差异，4,628 项离线／2,316 项合同检查，以及 Ruff、Mypy（151 个源文件）、清洁 wheel 六入口、current／legacy 协议、updater 和 profile 检查。所有 19 条 cohort 记录绑定该测试包，不改写为后续新 wheel 的身份。

本轮结束时，同步原生保护／账号 fence 与参考视频仍待后续交付补证；这些待办已在[独立补测与最终 fcdf 交付](live-model-followup-2026-11-22.md)中分别记录。不能用本轮包的字节一致性代替后续包，也不把新成功回填本轮失败或续编链缺口。

<!-- HISTORICAL_COHORT_20261122: Keep this 19-case cohort bound to ba8e. Subsequent delivery/readback evidence is linked separately. -->

## 后续针对性实测

后续 7c 补测及最终 fcdf 的原句柄恢复见[补充报告](live-model-followup-2026-11-22.md)。Neon 真保存产物续编、Carbon 单参考图同步视频与音乐的成功、两个顺序独立视频尝试及 Hydrogen 音乐最终拒绝／清理均按 round／attempt 分开，不改变本页 ba8e 的 19 项结果、通过数或构建身份。

## 历史附注

早前候选包曾有 Hydrogen／Carbon 音乐成功和 Neon 独立浏览器 Short＋Instrumental 播放器，也有默认无音频、视频拒绝和发送前选择错误。旧的 Hydrogen 视频／Neon 音乐保护句柄已由本轮包按原句柄分别分类 `UPSTREAM_REJECTED`／`ARTIFACT_NOT_RETURNED` 并完成清理，未重发生成。这些不是本轮新 19 项通过记录。

早前 Auto 超时的请求曾捕获 CID 并清理，不能描述为没有提交。另一次十目标诊断为八条本来已缺席、两条精确 marker 核对后删除并独立确认，不计为十条删除。guest CID 未持久化也不证明历史存储不兼容。

## 脱敏报告草稿

[Schema 报告](live-model-matrix-2026-11-22.json)状态为 `degraded`，本地验收终态为 `failed`；[逐项证据说明](live-model-matrix-2026-11-22-evidence.json)保留工具状态、严格验收、来源模式、清理补证和未知远端结果的区别。两份文件均含 19 个唯一组合，不含原始响应、账号内容、凭据、远端标识或私有文件路径。这是用户授权的本机 `default` 测试，不冒充专用 canary 环境，不进入 PR CI。

Schema 使用 [live-canary-report schema](../compatibility/live-canary-report.schema.json)，但本轮采集的是 MCP 黑盒结果，没有原始 RPC／HTTP 遥测。因此 `rpc_id=not_observed` 是明确的未观察 sentinel，`source_path` 为公开代码入口，`parser=mcp_cohort_result`；HTTP 状态为空、计数为零表示未采集，不表示没有远端请求。`terminal_state=failed` 描述本地调用／验收，不代表 Neon 无身份音乐等远端任务已终止。Neon 续编的 `unavailable` 指保存产物链证据缺席，不宣称模型能力不可用。
