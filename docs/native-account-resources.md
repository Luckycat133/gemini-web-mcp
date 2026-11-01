# 原生 Skills 与 Notebook 管理

以下工具在 `gemini-mcp-account` 账户面，默认函数面仍只暴露调用 Gemini 的功能。列表不返回用户指令正文；新增、覆盖和删除应有用户对该资源的明确意图。

## 原生 Gemini Skills

`gemini_skills` 与本地 Agent Skill、Gems 分别管理：

```text
gemini_skills(action="list", limit=20, offset=0)
gemini_skills(action="get", skill_id=<精确 ID>)
gemini_skills(action="get", skill_id=<精确 ID>, include_instructions=true)
gemini_skills(action="create", title="my-skill", description="何时使用", instructions="具体指令")
gemini_skills(action="update", skill_id=<精确 ID>, instructions="新指令")
gemini_skills(action="enable", skill_id=<精确 ID>, enabled=false)
gemini_skills(action="delete", skill_id=<精确 ID>)
```

创建和更新采用原生编辑器的 Skill 文件上传流程。更新会保留未指定的字段与启用状态；含额外资源文件或无法确认结构的 Skill 会拒绝覆盖，避免丢失附件。创建、更新、启停和删除均独立读回；未知结果不会重新提交。`include_instructions=true` 只用于明确要求读取的精确 Skill。

## Notebook 与文本来源

`gemini_notebooks` 继续用于紧凑发现资料；`gemini_manage_notebook` 用于原生 Notebook 项目管理：

```text
gemini_notebooks(source="gemini")
gemini_manage_notebook(action="create", title="合成资料")
gemini_manage_notebook(action="get", notebook_id=<返回的精确 ID>)
gemini_manage_notebook(action="list_sources", notebook_id=<精确 ID>, limit=20, offset=0)
gemini_manage_notebook(action="add_source", notebook_id=<精确 ID>, title="资料名", text="来源正文")
gemini_manage_notebook(action="delete_source", notebook_id=<精确 ID>, source_id=<已读取的精确来源 ID>)
gemini_manage_notebook(action="delete", notebook_id=<精确 ID>)
```

删除 Notebook 会同时删除其中的对话。来源 ID 必须在该 Notebook 的新鲜元数据中精确匹配；不拼接资源路径、不猜来源选择器与项目目录之间的 ID 转换。创建后使用模型分析时，仍先通过来源选择器核实可用的 `notebook_ids`。

来源写入的 `verified` 只证明独立元数据读回，不证明内容已完成索引。`content_verified=false` 保留这一边界；真正的内容验证需要使用该来源回答一个未在问题中泄漏答案的查询。本轮合成测试已通过随机标记检索，并分别核验来源、Notebook 和临时聊天的删除。

目前专用来源管理支持纯文本；URL、文件、Drive 来源及 Notebook Studio 产物未由此接口完整覆盖。覆盖状态见[网页版功能矩阵](web-feature-coverage.md)。
