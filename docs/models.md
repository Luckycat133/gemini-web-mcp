# 模型与思考等级

## 当前目录

2026-10-18 已用登录的 Chrome 网页和 MCP 运行时目录核对：

| 网页显示名 | 当前目录名称 | 兼容别名 |
| --- | --- | --- |
| 3.5 Flash-Lite | `gemini-flash-lite` | `flash-lite` / `lite` |
| 3.8 Flash | `gemini-flash` | `flash` / `fast` |
| 3.1 Pro | `gemini-pro` | `pro` |
| 4 Argon | `gemini-argon` | 直接使用当前名称或显示名 |

primary 的 `gemini_list_models`、compact 的 `account(action="models")`，
以及 focused account 的 `gemini_account(request={"action":"models"})`
读取当前账号目录。模型可能随账号、地区和上游更新而不同。请求可使用目录中的
`model_name`、`display_name` 或 `model_id`；请求头取已初始化目录的实际模型类别，
不为新模型硬编码 ID。旧请求名仍兼容，`thinking` 作为旧模型别名归到 Flash。

## 思考等级

| `thinking_level` | 当前网页 | 协议值 | 兼容值 |
| --- | --- | --- | --- |
| `low` | 低点：快速高效 | 1 | `standard` / `标准` |
| `medium` | 中等：深度适中 | 4 | `中等` |
| `high` | 高点：超级详尽 | 2 | `extended` / `扩展` |

默认 `standard` 继续等价于 `low`。思考等级与模型名称独立；
`thinking` 别名不隐式选择 `high`。focused create 的 schema 接受这三个英文值
及 `standard` / `extended`；共有客户端还接受当前中文标签。
请求正文和模型请求头使用同一等级，未知模型类别或请求形状不能静默降为默认等级。
底层保留 `deep_think` 协议枚举兼容，但本次账号菜单未显示该选项，不将其视为已开通。

```json
{"tool":"gemini_chat","arguments":{"message":"解释这个方案的取舍","model":"4 Argon","thinking_level":"medium"}}
```

## 原生任务模式

当前 `/app` 的“对话／任务”切换对应独立的原生 feature selector。
primary chat 工具和 compact `chat` / `session` 的 `task_mode=true` 选择该模式；
思考等级仍可独立指定。它与 Guided Learning、定时操作、MCP 协议 Tasks 分别处理。

任务结果包含 `task_status` 和已观察的来源 `chat_id`。用
`gemini_history(action="task_status", chat_id=...)`、compact `history` 同名动作，
或 focused account 的 `gemini_history(request={"action":"task_status","chat_id":...})`
读回原任务；状态查询不会重提提示、删除来源或把“流结束”当成任务结束。
非终态或未知状态保留来源。取消任务的服务端协议尚未验证，因此未暴露取消动作。
`task_mode` 不与 `learning_mode`、临时聊天或媒体选择器混用。

## 媒体模型

图片、视频和音乐使用各自的原生创建模式，产物验证与文本模型选择分开。
当前图片路由为 Flash-Lite → Nano Banana 2 Lite，其他既有图片别名 → Nano Banana 2；
Pro 图片请求也使用后者。音乐报告 Lyria；视频要求实际可用的本地视频文件。
模型显示名或提示中的版本要求都不能证明实际媒体后端版本；检查结果里的
`request_model`、`requested_backend`、`observed_backend` 和产物验证状态。

网页及公开前端协议证据见
[2026-10-18 来源记录](../compatibility/web-request-source-20261018.json)；
真实调用范围和限制见 [实测记录](live-ui-coverage.md)。
