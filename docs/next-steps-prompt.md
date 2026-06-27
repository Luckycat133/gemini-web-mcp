# Next Steps Prompt

你在 `/Users/Shared/projects/gemini-web-mcp` 继续工作。当前仓库已经具备 Gemini Web MCP/skill 的核心能力：Pro 账号 Cookie/profile 诊断、scheduled actions list/get/create/delete、聊天记录管理、音乐导出、低 token skill server 静态发现、`gemini_doctor` 预检和 `gemini_cleanup_test_artifacts` 测试产物清理。

下一步按以下顺序推进，不要跳过验证：

1. 实现每日可用性 smoke test。
   - 目标是一个可重复运行的只读优先检查入口，输出机器可读报告。
   - 复用 `gemini_doctor(validate_browser=false)` 作为前置检查。
   - 覆盖 tool manifest、cookie/profile 状态、core/all 工具面数量、annotation 完整性。
   - live 账号检查必须可开关；默认不要创建远端聊天或定时任务。
   - 如果启用 destructive/live E2E，必须先用唯一 marker 创建，再用 `gemini_cleanup_test_artifacts(dry_run=false, markers=...)` 清理。

2. 产品化多客户端配置。
   - 为 Codex、Claude Desktop、OpenAI Agents SDK 和普通 MCP client 写最小配置示例。
   - 每个示例必须说明推荐 `GEMINI_TOOLS`、何时调用 `gemini_doctor`、如何处理 Chrome `Default` 与 `Profile 1` 不一致。
   - 不要写入 Cookie 原值或可恢复密钥。

3. 扩展可安全验证的 Web Pro 能力。
   - 只对有稳定 RPC 契约和明确授权的能力增加 mutation 工具。
   - Drive picker、Canvas mutation、settings mutation、memory import mutation、public-link mutation 仍默认保持 probe/UI-only。
   - 对任何新增 RPC，都要补 manifest、docs、contract evaluation 和实机 smoke evidence。

4. 保持验证标准。
   - 运行 `./.venv/bin/pytest -q tests`。
   - 运行 `py_compile` 覆盖主要模块。
   - 运行 `git diff --check`。
   - 检查 `GEMINI_TOOLS=core` 和 `GEMINI_TOOLS=all` 的 MCP `list_tools()` 数量和 annotation 缺失情况。

当前设计约束：

- 默认工具面应保持高信噪比；只把真正常用且安全的工具放入 always/core。
- `scan_turns=true`、`read/export chat`、删除聊天和删除 scheduled action 都需要明确意图。
- 任何 live 测试创建的聊天、定时任务或媒体文件都必须有 marker 和清理路径。
- Chrome profile 对齐问题要作为环境诊断处理，不要误判为 Gemini Web 功能缺失。
