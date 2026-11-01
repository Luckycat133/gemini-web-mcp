# Task 浏览器与权限偏好

账号面 `gemini_preferences` 管理已保存的设置。联网和工具调用无需编造启用开关；Task 仍可能返回原生许可卡。

先读取当前浏览器设置及版本：

```text
gemini_preferences(action="get_browser")
```

用户要求改变时，用返回的 `revision` 防止覆盖过期状态：

```text
gemini_preferences(action="set_browser", browser="remote_only",
                   permission="always_ask", expected_revision=<返回的 revision>)
```

`automatic` 允许 Gemini 在可用的本机 Chrome／远程浏览器中选择；`remote_only` 限制为远程。省略浏览器或权限参数会保留该项原值。读到未知状态时不会猜成已保存的默认值。

按应用动作管理权限时，先取得真实可管理的动作和不透明句柄：

```text
gemini_preferences(action="list_permissions", limit=20, offset=0)
gemini_preferences(action="set_permission", permission_id=<返回的句柄>,
                   permission="always_ask", expected_revision=<该项 revision>)
```

`always_allow` 保存原生持久偏好，只在用户要求该范围时设置。结果的 `permission_preference` 是保存值；`effective_permission="unknown"`、`behavior_verified=false`、`verification_scope="stored_preference"` 明确表示设置读回不证明实际免确认。本轮真实 MCP Task 与原生网页在保存“始终允许”时都仍弹出了浏览器许可卡；具体原因尚未证实。一次／本线程许可继续使用原 Task 卡片。目录动作也不代表应用已连接或任意操作可执行。

许可卡含多个浏览器或授权选项时，从 `input_options` 选择精确 `input_option_id`，连同原 `operation_id`、`input_id` 和选项 `prompt` 调用 `gemini_run_task`。不按显示文字猜测原生选项身份；已消费的旧卡等待续答，不再次要求输入。App 连接前置与权限卡不同，必须按原生连接流程处理，不能用文字“允许”代替。

服务在修改前新读、比较原状态并检查账号；这不是服务器原子 CAS。每次写入只派发一次，随后独立读取完整目标状态。`mutation_sent`、`changed=null` 或写响应回显都不是修改成功；未知结果不自动重发。结果不返回 Cookie、原生授权 ID 或完整权限对象。

`thinking_level="low" | "medium" | "high"` 用于 Chat 和 Task 请求；旧 `standard` / `extended` 仍对应低／高。档位与浏览器、权限的持久设置分开管理；Lite 的不可选思考限制继续由运行时执行。
