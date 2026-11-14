# 双 Surface 分支与已发布版本的集成边界

更新：2026-11-14。当前重构位于 [PR #47](https://github.com/Luckycat133/gemini-web-mcp/pull/47)，
分支 `codex/gemini-tools-polish-20261113`，本次审查提交
`5c9f53087398f614958acf06d3c9d14f54958c52`。旧 PR #39 / 20261101 分支不是本次修改基线。
审查时 `main` 为 `ec12c1a5b4953728aac666eeeb95435a9fcc18a7`，不能假设重构已经合入。

## 已验证的范围

对应提交的 [CI](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/43433504485)
与 [CodeQL](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/43433504654) 已通过。
CI 覆盖 Python 3.11/3.12 全量离线测试、契约、profile 与真实 stdio、clean wheel、
隔离 onboarding、Skill 校验与安装。用户另报告本地已同步指纹并通过全量契约和协议 smoke；
未取得本地 diff 前，不把未推送状态假定为与该 SHA 相同。

这些成功证明当前提交的相应合同，不证明旧发布功能全保留，也不是新的 Gemini 在线验收。

## 两代合同

| 方面 | 当前 Function/Account 分支 | 已发布 v0.2.2 |
| --- | --- | --- |
| 源包版本 | 0.2.1，仍是开发源 metadata | 0.2.2 已发布；历史 tag 不可覆盖 |
| 推荐功能入口 | `gemini-mcp-function`；主入口未设置 GEMINI_TOOLS 时同此 | assist/create 分离；存在 `gemini-mcp-create` |
| 账号入口 | 同名 `gemini-mcp-account`，17 个扁平参数工具 | 七个 request 判别联合 facade |
| 图片/编辑 | 与视频音乐一样返回后台句柄 | create 面同步产物 |
| wire 结果 | SDK content wrapper；domain-backed block 的 `_meta.domain_result` | 部分 focused tools 直接 typed domain envelope |
| 长任务 | 显式 operation_id、独立 worker、status/result；无公开取消 | idempotency_key、status/result/cancel 等合同 |
| 持久状态 | GEMINI_OPERATION_DB_PATH 与 GEMINI_CLEANUP_DB_PATH | GEMINI_STATE_DB_PATH 统一 store |
| 新增方向 | Task/Notebook Chat、原卡输入、浏览器、偏好及完整附件保护 | 不能据此推定所有新方向已存在 |
| 待迁移旧能力 | Canvas、Library、公开分享、Skills ZIP 等缺少当前对应业务调用 | 有对应的已实现/有界验收记录 |

同名不保证 input/output/annotation 兼容。当前“兼容 profile”只描述本分支保留的路径。
保留的 RPC/parser fixture 不等于存在服务或公开工具；删掉旧实现测试也不能充当迁移验收。

## 合入前的最小决定与验证

默认优先薄适配器保留有用户价值的旧行为，缺口逐 action 记录。无法兼容的部分必须显式
版本化和迁移说明，不悄悄删除功能。合入准备至少包含：旧 entrypoint/schema/result 映射、
缺失账号能力处置、旧 DB/句柄的合成迁移或明确拒绝与回滚、账号隔离和未结束任务保护。
不要仅改环境变量名、清空数据库或重绑定账号。

状态文件先用分支独立私有路径。更换版本前备份并记录有效句柄，只有已验证迁移才共用旧数据。
无 locator 的未知提交不能自动重新生成。现有 operation 恢复不等于普通会话已支持跨进程续接。

发布时先查询实际已发布版本，选择新的正确版本并同步 metadata；不按旧 Skill 指令发布 v0.2.1，
不自动 merge/tag/release。本轮只更新开发指导与计划，不改运行时或指纹。

后续实施顺序与验收见 [开发计划](development-plan.md)。
