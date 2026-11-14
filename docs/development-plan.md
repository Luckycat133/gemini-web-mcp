# Gemini Web MCP：当前双 Surface 开发计划

修订：2026-11-14。审查基线为 PR #47 分支
`codex/gemini-tools-polish-20261113` 的 `5c9f53087398f614958acf06d3c9d14f54958c52`。
本分支包版本为 0.2.1；已发布 v0.2.2 的接口与状态库另列迁移，不能直接视为兼容。

## 从这里开始

加载 [开发 Skill](../.agents/skills/gemini-web-mcp-development/SKILL.md)，
阅读 [当前 roadmap](../.agents/skills/gemini-web-mcp-development/references/roadmap.md)。
Function/Account 已实现，不再创建第三套生成入口。保持“所有账号可用生成能力”
与“全部 Gemini 内容管理”两个同等目标，不用缩小功能范围解释删除的旧版能力。

MCP v2 契约指纹、全量离线契约与真实协议 smoke 已有对应提交通过证据；
它们是回归基线，不是下一轮重新建设的任务。下一步优先核对发布兼容及未迁移能力，
然后并行推进连续多模态生成与完整内容查找/导出/管理。

## 执行与验收

```bash
SKILL_DIR=.agents/skills/gemini-web-mcp-development
python "$SKILL_DIR/scripts/plan.py" check
python "$SKILL_DIR/scripts/test_plan.py"
python -m pytest -q tests/test_development_skill.py
```

机器计划保留 26 个工作包和 52 个案例编号，已更新基线、版本、共享 owner 和 roadmap
对应章节。它是未来工作账本，不是“26 包已经完成”的报告。旧进度必须按新计划版本
显式核对；不得因为 CI 通过就将 live/agent 门禁标 accepted。私有进度/原始证据不入 Git。

详细目标：[生成](../.agents/skills/gemini-web-mcp-development/references/generation.md)、
[管理](../.agents/skills/gemini-web-mcp-development/references/management.md)、
[验收](../.agents/skills/gemini-web-mcp-development/references/acceptance.md)。
当前代码合同与失败边界见 [架构](../.agents/skills/gemini-web-mcp-development/references/architecture.md)，
具体命令和指纹刷新规则见 [验证](../.agents/skills/gemini-web-mcp-development/references/validation.md)。

## 本轮审查改动

修正旧分支安装指令、旧发布版本指令、图片同步/异步混用、统一 DB/分离 DB 混用、
普通 session 与 operation 恢复混用、status/result 副作用混用以及脚本/原生生成产物混用。
恢复 machine plan 所需工作包章节，且不改运行时、MCP golden 或发布版本。

此任务不授权访问 Google 账号、花费生成配额、公开分享或删除内容；在线验收另按已有
明确授权执行。发布兼容差异见 [集成说明](source-update-integration.md)。
