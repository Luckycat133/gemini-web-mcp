# Gemini Web MCP：双主线开发与验收计划

计划修订：2026-11-07。基线：`d5dbb406357ef21df3a612aa51bc9888efe07879`，包版本 `0.2.2`。

本计划贯彻两个同等目标：使用 Gemini Web 当前账号可用的全部生成模型/原生工作流；管理 Gemini 网页全部受支持内容与相关配置。它不是功能已完成公告，也不把账号权限或上游缺失能力伪装成实现成功。

## agent 从这里开始

使用已有 [gemini-web-mcp-development Skill](../.agents/skills/gemini-web-mcp-development/SKILL.md)。先读 checkout 的 AGENTS.md，再按任务依赖实施和验收。保留现有五套兼容入口；新生成入口是计划目标，不是现有安装命令。

```text
$gemini-web-mcp-development 按双主线计划执行开发与验收。
先核对当前代码和进度，完成所有未阻塞工作包；保留兼容性和用户工作。
每包记录实现、离线/协议/在线/agent/打包证据；未测或受限不得标为完成。
```

```bash
SKILL_DIR=.agents/skills/gemini-web-mcp-development
python "$SKILL_DIR/scripts/plan.py" check
python "$SKILL_DIR/scripts/test_plan.py"
python "$SKILL_DIR/scripts/plan.py" init --progress "$PRIVATE_RUN_DIR/progress.json"
python "$SKILL_DIR/scripts/plan.py" next --progress "$PRIVATE_RUN_DIR/progress.json"
```

PRIVATE_RUN_DIR 必须由执行者设为仓库外私有目录。init 不覆盖旧进度。在线操作另遵守具体账号授权和预算；此计划不授权读取/删除私人历史、消费生成配额或发布链接。

## 内容导航

| 内容 | 位置 |
| --- | --- |
| 26 个工作包、依赖和实施顺序 | [路线](../.agents/skills/gemini-web-mcp-development/references/roadmap.md)、[机器计划](../.agents/skills/gemini-web-mcp-development/assets/plan.json) |
| 生成与管理的具体实现要求 | [生成](../.agents/skills/gemini-web-mcp-development/references/generation.md)、[管理](../.agents/skills/gemini-web-mcp-development/references/management.md) |
| 52 个验收案例，39 组初始能力覆盖 | [验收](../.agents/skills/gemini-web-mcp-development/references/acceptance.md) |
| 34 个官方来源及协议发现方法 | [资料](../.agents/skills/gemini-web-mcp-development/references/sources.md) |
| 测试命令、证据格式、完成门禁 | [验证](../.agents/skills/gemini-web-mcp-development/references/validation.md) |
| 兼容、安装、发布候选与交付 | [交付](../.agents/skills/gemini-web-mcp-development/references/delivery.md) |

39 组能力还需按账号实际 actions、模型/输入/selector 变体展开；不是只测试 39 个样例。官方说明、源码实现、离线通过、协议通过与真实账号验收分别记录。所有静态检查通过仍不能代替真实执行证据。

最后使用 `plan.py check-progress --progress ... --evidence-root ... --complete`，随后人工核对独立报告；只有所有范围和门禁均满足才称全部验收。上游退役/无权限保留真实状态，通过有来源的范围修订处理，不偷偷删除失败项。
