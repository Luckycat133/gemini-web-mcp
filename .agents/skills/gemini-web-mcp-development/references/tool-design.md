# 两套工具、运行 Skill 与 agent 交接

## 接口目标与兼容

新生成入口 `gemini-mcp-generate` 是待实施目标，不能在实现/安装检查前写成可用命令。复用现有 ask/search/understand、creation、Research、chat services，按能力组合；管理继续使用 `gemini-mcp-account`。旧 primary/compact/assist/create/account 工具名、默认语义和调用 schema 保留，安全修复须说明拒绝边界。

不要强迫新套件继续固定 5/7/7 工具数；也不要给每个网页按钮造一个工具。按用户目的、输入 schema、权限和完成条件分工具。复杂 action 使用 discriminated union，禁止自由 JSON 大杂烩。共享注册工厂复用业务适配，不通过另一个 MCP 服务绕一圈。

推荐目标操作族（名称由实现确认，不是当前 API）：

| 生成线 | 管理线 |
| --- | --- |
| capabilities、ask/chat、understand、search | inventory/search/read/export |
| image generate/edit/redo、video、music | history、notebooks/sources、library/canvas |
| research plan/start/result、native files/learning | skills/gems、tasks/schedules、sharing/settings |
| conversation resume、operation status/collect/cancel | batch plan/apply/status、backup/restore |

生成线可以执行用户明确选择资源的最小读取，但不默认遍历账号；管理线按 read/admin 策略部署。同一高风险 facade 含读取与写入时保守标注，并在服务层强制 permission；给只读部署独立 schema/catalog，不能仅靠 `actionSemantics` 自定义元数据让客户端猜。

## 结构化响应

保留兼容 text，同时提供经 outputSchema 校验的结构化状态。统一 request_id、error.code/retryability、warnings、operation state、verification、coverage；不要把完整历史或 base64 媒体塞进每次响应。schema 声明的可选字段必须有准确缺省含义。

- `status` 在新接口只读已有状态；`collect` 明示下载/恢复/清理副作用。旧接口可能有副作用，维持正确 annotations 与文档。
- 分离调用被接受、上游已提交、产物就绪、变更已验证和清理完成；`ok=true` 不自动等于所有阶段完成。
- 文件按能力返回 resource/link、缩略预览和原件定位；远端客户端不能使用服务器裸绝对路径。不要把任意本地路径变成可下载资源。
- continuation 要保留 query/filter/scope 与 source 游标；返回截断理由与下一步，不宣称未知全量数。
- 失败时提供可执行下一步，不把后台响应/headers/私密内容放进错误。

MCP 协议由 SDK 协商；native Gemini Task、scheduled action、应用层 Operation 和 MCP protocol Task 是不同概念。协议 Tasks 只有在当前 SDK/客户端协商并测试后适配，显式 operation_id 路径始终可用。参见 MCP01–03，不回退到自写协议栈。

## 运行 Skill

新增生成运行 Skill 可以叫 `gemini-generate`；管理沿用 `gemini-account`。开发 Skill 仍唯一叫 `gemini-web-mcp-development`。旧运行 Skill 保留兼容路由。更新 descriptions、UI metadata、安装说明、package assets 与 discovery 去重测试；仅运行 Skill 按现有许可政策发布，开发 Skill 保持 AGPL-3.0-only。

主 SKILL.md 只放触发条件、选择流程、非显然约束和产物交付；长参数表、上游样本及历史日志放 references。每条指导要对应代码行为，不用几百条文字掩盖工具设计错误。

## 独立 agent 验收

给执行 agent 任务和最小原始输入，不给预写工具调用答案；审核者独立用 artifacts/read-back 判定。至少包括：

1. 图片理解 vs 编辑；写音乐提示词 vs 真生成音频；网页搜索 vs 私人历史检索。
2. 超时恢复原任务 vs 新建变体；保留作品 vs 删除临时来源；本地文件清理 vs 云端删除。
3. 从旧聊天选素材→修改→Notebook 整理，以及研究→报告→备份的混合意图。
4. 中英改写、同名资源、无权限、缺失来源、提示注入及连接中断。

记录首次/恢复完成率、调用数、tokens/延迟分布和人工介入；费用/额度只报告可观察数。近似意图正确性必须实际 model-backed 测试，静态 expected-route JSON 不是测得的准确率。任何未授权访问、误删或恢复引发重复生成均阻止该能力发布；不规定无依据的全任务 100% 成功率。
