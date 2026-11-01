# 当前工具分支与已发布版本

2026-11-01，本地工具开发分支发现远端 `main` 已发布 `0.2.2`，两边从共同祖先分别发展。当前改动先通过独立分支和 PR 提交；它不是覆盖远端发布版本的升级，也没有创建新标签或发布 Python／Skill 包。

## 两边的合同

| 方面 | 本地工具分支 | 已发布的 0.2.2 |
| --- | --- | --- |
| 推荐功能入口 | `gemini-mcp-function`，17 个问答、理解、会话、Task、媒体和 Research 工具 | `gemini-mcp-assist` 与 `gemini-mcp-create`；创作面有 7 个工具 |
| 账号入口 | `gemini-mcp-account`，17 个扁平参数工具 | 同名入口有 7 个 `request` 判别联合 facade |
| 图片／编辑 | 后台操作句柄 | 创作面同步返回产物 |
| 长任务控制 | 显式 `operation_id`；不提供取消 | `idempotency_key`、状态／结果和取消；取消须有独立证据 |
| 状态存储 | `GEMINI_OPERATION_DB_PATH` 与 `GEMINI_CLEANUP_DB_PATH` | `GEMINI_STATE_DB_PATH`，包含作用域、幂等、取消及清理元数据 |
| 账号能力差异 | 新增 Task 浏览器、原卡精确续答、偏好与附件门禁；Notebook／纯文本来源及原生 Skills 管理 | 还包含 Canvas、Library、分享、原生 Skills ZIP 和其他嵌套账号操作 |

工具同名不代表输入和返回合同相同，尤其是 `gemini_history`、`gemini_notebooks`、`gemini_cleanup` 和 Research。两种账本不能仅通过改名环境变量互换。本分支的“兼容 profile”指共同祖先的配置，不能据此声称覆盖已发布 0.2.2 的全部新接口。

## 安装与验证范围

在本分支验证时，用本分支的准确 commit SHA 替换安装示例中的 `@main`，并使用独立私有状态目录。只读取对应版本签发的句柄；不要把一个版本中的句柄当作另一个版本已经完成、取消或清理的证据。

[逐工具质量记录](tool-quality.md)覆盖本分支的 34 个业务工具；[网页版功能矩阵](web-feature-coverage.md)区分真实请求证据与未完成项目。离线、协议和安装检查不能证明远端认证、Research 报告或待处理删除已经完成。

合入主线前还需逐项保留已发布入口、请求／结果格式与原生模式；验证旧状态、账号隔离、幂等、取消和清理恢复。这里明确保留这些集成缺口，不用覆盖文件或缺失目录来解决冲突。
