# 当前工具分支与已发布版本

[PR #39](https://github.com/Luckycat133/gemini-web-mcp/pull/39) 的开发源位于 `codex/gemini-tools-polish-20261101`。`main` 和 [0.2.2 发布](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.2.2) 属于另一条发布线。当前分支包含后续工具与兼容性修复，尚未合入 `main`，也没有发布新包或标签。

## 两边的合同

| 方面 | 本地工具分支 | 已发布的 0.2.2 |
| --- | --- | --- |
| 推荐功能入口 | `gemini-mcp-function`，17 个问答、理解、会话、Task、媒体和 Research 工具 | `gemini-mcp-assist` 与 `gemini-mcp-create`；创作面有 7 个工具 |
| 账号入口 | `gemini-mcp-account`，17 个扁平参数工具 | 同名入口有 7 个 `request` 判别联合 facade |
| 图片／编辑 | 后台操作句柄 | 创作面同步返回产物 |
| 长任务控制 | 显式 `operation_id`；不提供取消 | `idempotency_key`、状态／结果和取消；取消须有独立证据 |
| 状态存储 | `GEMINI_OPERATION_DB_PATH` 与 `GEMINI_CLEANUP_DB_PATH` | `GEMINI_STATE_DB_PATH`，包含作用域、幂等、取消及清理元数据 |
| 账号能力差异 | Task 浏览器、原卡续答、偏好、附件校验、Notebook 来源和原生 Skills 管理 | 还包含 Canvas、Library、分享、原生 Skills ZIP 和其他嵌套账号操作 |

工具同名不代表输入和返回合同相同，尤其是 `gemini_history`、`gemini_notebooks`、`gemini_cleanup` 和 Research。两种账本不能仅通过改名环境变量互换。本分支的“兼容 profile”指共同祖先的配置，不能据此声称覆盖已发布 0.2.2 的全部新接口。

## 安装与验证范围

[客户端安装示例](client-examples.md)已指向开发分支。固定源码时，把安装地址 `@` 后的分支名换成已审核的完整 commit SHA。切换版本前，为 `GEMINI_OPERATION_DB_PATH` 和 `GEMINI_CLEANUP_DB_PATH` 指定本分支的独立私有路径，只用此版本读取它签发的句柄。

运行离线预检并核对 `profile=function`、`enabled_tools=17`、`text_tool=gemini_ask`。0.2.2 的预检也可能成功，但验证的是 `model` 工具组。最近一批运行时修复为 [`4e0becd`](https://github.com/Luckycat133/gemini-web-mcp/commit/4e0becd22636eff2987f683cb2975c02bcbca839)，其 [CI](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/42226171553) 与 [真实请求验证](development-status.md#flash-and-browser-permission-follow-up-2026-11-06) 分开记录。[逐工具质量记录](tool-quality.md)覆盖本分支的 34 个业务工具。

合入主线前还需逐项保留已发布入口、请求／结果格式与原生模式；验证旧状态、账号隔离、幂等、取消和清理恢复。这里明确保留这些集成缺口，不用覆盖文件或缺失目录来解决冲突。
