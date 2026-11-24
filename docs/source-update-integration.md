# 当前工具分支与已发布版本

当前工作区的开发版本为 `0.20.7`，基于 `codex/gemini-tools-polish-20261101` 的工具与兼容性改动。这是本地开发版本，尚未公开发布；本次仅修新实证的有限中文拒绝漏识别，新bc4c wheel已真实安装并通过门禁，原句柄已按预期拒绝收口（不算视频生成），见[0.20.7验收](live-media-0.20.7-2026-11-22.md)。此前64e8的真实`0.20.6`安装与生成证据保留在[原验收](live-media-0.20.6-2026-11-22.md)。官方 `v0.3.0` 已于2026-11-22 03:09:21 UTC发布，保留其与`0.2.2`的原记录，不把本地补丁里程碑冒充公开版本。 [0.3.0 发布](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.3.0) · [版本证据](version-history.md)

版本以 `pyproject.toml` 为准。未定版的改动先记入 [变更日志](changelog.md#unreleased)，定版时从上一正式版本接续。本地安装以当前工作区直接构建的同一 wheel 为来源，保留公开 `gemini_` 工具合同。较早的 `0.20.0+codex.3` 私有兼容包属于旧安装记录，不再作为维护另一份业务逻辑的来源。

先前把累计开发内容重建为 `0.1.0` 至 `0.20.0` 的功能迭代；`0.18.0`至`0.20.0`来自工作区工具目录、多参考图和后台审批。本次将之后已完成的修复按六个合同范围回填为`0.20.1`至`0.20.6`，均是同一未提交工作区的开发批次，不代表曾存在六个历史提交、已产包或发布。当前`0.20.6`已真实构建安装并独立验收；各旧`0.20.0`构建hash和实测版本不改。详见[Changelog](changelog.md)与[原版本记录](version-history.md)。

## 本机同源验收与后续更新

App已实际Restart两次、64e8新child已启动，但本轮真实调用仍到旧60fe worker；原因未证，不能将App重载标完成。全局Gemini原Default／dsh／开关已恢复、command固定64e8，其他配置parse-equal；新stdio的版本／六工具／Default真实答案与清理验证独立于App路由。`0.20.7`安装和原句柄读回会追加新证据，不改旧build。

本节保留前轮`0.20.0`安装快照；新的`0.20.6`最终安装为64e8，身份和门禁见[独立验收](live-media-0.20.6-2026-11-22.md)，不复用旧hash。前轮共享`current`已切换到 `60fe2a5025546098`，完整 wheel SHA-256 为 `60fe2a5025546098e0e4735ef9d3be1273b5107c2d9fcf206961072613859cc2`：144 源码／data 文件及 46 固定依赖零差异，无额外文件，Ruff／Mypy（153 文件）、5,009 离线／2,804 合同检查通过。本轮 fresh stdio 已通过13项既有结果读回（五个文件case、七个unavailable、一个拒绝），身份保留且零create／generate，六入口／profile通过。60fe当轮Hydrogen视频44.09秒明确提交后上游拒绝并清理、没有文件；67b旧H视频／音乐、2663 Neon Low音乐和b562 Neon standard视频仍可按原身份读取，不重标为新生成。旧客户端连接仍需独立核对加载身份。原提示、人工验证 Cookie、菜单状态与分轮产物证据见[当前诊断](live-media-diagnosis-2026-11-22.md)，不把旧包成功重标为新包生成。

此前候选包的运行身份与验收详情保留在[11-21 历史矩阵](live-model-matrix-2026-11-21.md)及[11-22 进展报告](live-model-matrix-2026-11-22.md)，不代表活动长连接已加载当前源码。11-21 的 19 个组合中 13 项通过，历史结果不回写。

2026-11-22 的源码把网页音乐和视频默认改为原生浏览器 composer／ProcessSession；显式 `GEMINI_WEB_MEDIA_TRANSPORT=sdk` 保留兼容路线。`ba8e` 的 V5 标识本轮 19 项 cohort 包，产品版本仍为 `0.20.0`；它不是后续源码修复的最终交付包。详情见 [11-22 同包报告](live-model-matrix-2026-11-22.md)。

原生 Cookie 注入的一律 HttpOnly 设置已证实是鉴权缺陷；修复后已有音乐产物通过。此前 guest CID 未持久化不证明存储隔离；移除未验通的适配后复用「验证本地产物 → 释放保护 → canonical 删除／独立读回」。旧 Hydrogen 视频／Neon 音乐保护句柄由 cohort 包按原句柄读回为 `UPSTREAM_REJECTED`／`ARTIFACT_NOT_RETURNED`，清理均 completed。19 项 cohort 已结束本地观察，严格 12 项通过、1 项保存产物链未验证、6 项调用错误；不将 Neon 本地控制图编辑或历史成功补入链路通过数。

客户端配置使用稳定命令 `~/.local/bin/gemini-mcp-function`。前轮验收时共享 `current` 指向 `~/.local/share/gemini-web-mcp/releases/fcdf1138268623cc`，wheel SHA-256 为 `fcdf1138268623cc4ccdc5491002e74292947958d50dcf12770d1457d7b62d15`，版本 `0.20.0`。该快照通过 143 文件一致性／零额外文件、46 固定依赖比较、4,699 离线／2,522 合同及记录的安装／协议门禁。它与 ba8e、7c 的生成／读回记录均保留原身份，不覆盖当前继续修复的结果。

7c fresh stdio 已验证 Carbon 单参考图同步视频、Neon 真保存产物链及 Carbon 音乐；fcdf 按原句柄补证 Hydrogen 音乐拒绝并完成清理，六个已知目标独立缺席检查发送零次修改。该历史批次四个视频没有身份／远端结果，不能声称全模型全入口通过。早前App连接`655d1612cb725167`曾出现`AUTH_REQUIRED`；随后已观察到App60fe真实调用，但新`0.20.6`连接重载与实际调用尚未核实，最近synthetic工具读回确认进程仍为60fe，共享指针和fresh stdio不能替代该证据。旧账号绑定、账本、租约与去重保留，不重建状态库。

<!-- APP_RELOAD_UNVERIFIED_20261122: Original60fe App calls are observed; new0.20.6 App reload/use is not yet verified. Original13 saved/cached reads retain their own build. -->

## 两边的合同

下表保留`0.2.2`历史接口比较。最新公开`0.3.0`的tag metadata仍把`gemini-mcp-account`指向`src_legacy`、保留`gemini-mcp-create`，并以`gemini-mcp-account-next`暴露新账号入口；它与本工作区默认六工具账号面仍有差异，不因版本更高就假定接口完全相同。该tag的全部工具schema未在此次版本记账中重测。

| 方面 | 本地工具分支 | 已发布的 0.2.2 |
| --- | --- | --- |
| 推荐功能入口 | `gemini-mcp-function` 提供 6 个工具，涵盖问答、Task、媒体、Research、状态和结果 | `gemini-mcp-assist` 与 `gemini-mcp-create`，创作面有 7 个工具 |
| 账号入口 | `gemini-mcp-account` 独立提供 6 个工具，管理已有对话和 Notebook，并读取 Task 元数据 | 同名入口有 7 个 `request` 判别联合 facade |
| 图片／编辑 | 后台操作句柄 | 创作面同步返回产物 |
| 长任务控制 | 显式 `operation_id`；不提供取消 | `idempotency_key`、状态／结果和取消；取消须有独立证据 |
| 状态存储 | `GEMINI_OPERATION_DB_PATH` 与 `GEMINI_CLEANUP_DB_PATH` | `GEMINI_STATE_DB_PATH`，包含作用域、幂等、取消及清理元数据 |
| 账号能力差异 | Notebook 来源和 Task 元数据。Task 浏览器、偏好和原生 Skills 管理通过 `account-full` 加载 | 还包含 Canvas、Library、分享、原生 Skills ZIP 和其他嵌套账号操作 |

同名工具的输入和返回合同仍须逐项核对，尤其是 `gemini_history`、`gemini_notebooks`、`gemini_cleanup` 和 Research。两种账本不能仅通过改名环境变量互换。旧主入口 profile 兼容共同祖先的工具组。完整目录 profile 保留本分支上一版接口。

已依赖本分支先前 17 个功能或账号工具的客户端可在 `gemini-mcp-server` 连接上配置 `GEMINI_TOOLS=function-full` 或 `GEMINI_TOOLS=account-full`。两种配置分别恢复原先的目录和请求字段。

## 安装与验证范围

从经过验证的当前工作区运行 `uv build --wheel --out-dir <临时构建目录>`，在独立虚拟环境安装该 wheel 并执行协议和工具清单检查。运行目录以 wheel 哈希标识，检查其中全部 `src` Python／data 文件与构建工作区逐字节一致，再把目标客户端命令指向该不可变运行目录。源码和安装版共用业务实现，不在安装后修改模型白名单或媒体入口。其他客户端使用的共享指针及其正在运行的任务须单独处理。

共享入口更新复用既有本地 updater，以同一个已验收 wheel 原子切换 `current`，保留旧不可变 release 和活动进程；不重建状态库。若 updater 来自另一工作树，其预检和协议 smoke 必须核对当前源码的 `gemini_` 合同。只有已完成 wheel、源码一致性和安装包预检的 runtime 才能登记验证标记，不能用手写标记替代验证。活动 MCP 连接重新加载进程后才使用新目录；指针切换不证明旧进程已经更新。

[客户端安装示例](client-examples.md)已指向开发分支。固定源码时，把安装地址 `@` 后的分支名换成已审核的完整 commit SHA。从已发布 facade 切换时，为 `GEMINI_OPERATION_DB_PATH` 和 `GEMINI_CLEANUP_DB_PATH` 使用本分支的独立私有路径；上述同源本地 v6 构建更新则保留其已有路径和句柄，不能仅因版本标签不同重建账本。

运行离线预检并核对 `profile=function`、`enabled_tools=6`、`text_tool=gemini_ask`。0.2.2 的预检验证的是 `model` 工具组。历史验证与当前目录分别记录。[`4e0becd` 的 CI](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/42226171553) · [真实请求验证](development-status.md#flash-and-browser-permission-follow-up-2026-11-06) · [逐工具质量记录](tool-quality.md)

合入主线前还需逐项保留已发布入口、请求／结果格式与原生模式；验证旧状态、账号隔离、幂等、取消和清理恢复。这里明确保留这些集成缺口，不用覆盖文件或缺失目录来解决冲突。
