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

`always_allow` 保存原生持久偏好，只在用户要求该范围时设置。结果的 `permission_preference` 是保存值，`effective_permission="unknown"`、`behavior_verified=false`、`verification_scope="stored_preference"` 表示当前证据来自设置读回。Google 分别说明了浏览器默认权限、本机 Chrome 的任务确认和敏感动作的额外确认。Task 返回卡片时按该卡片的实际动作和授权范围处理。[Google 权限说明](https://support.google.com/gemini/answer/18766567?hl=en) · [Task 浏览器说明](https://support.google.com/gemini/answer/17094507?hl=en)

2026-11-06 的授权本地测试将浏览器偏好设为「仅远程」和「始终询问」。真实 Task 返回中文许可卡，选中「允许一次」后，原任务继续打开公开网页。新截图分别确认了点击跳转、键盘导航、地址输入和滚动，重复使用同一确认卡或点击 ID 都未再次派发。测试窗口结束后，自动删除通过远端回读，原浏览器偏好也经独立读回确认恢复。

同日复测分别使用「仅远程＋始终允许」和「自动选择＋始终允许」。两条 Task 均直接打开同一个公开页面并取回标题，自动选择的实际远程浏览器画面也经过独立核验。两条测试对话均自动清理，原偏好经独立读回恢复。早期弹卡缺少足以确认其类别的记录，未能确定那一次的触发条件。

许可卡含多个浏览器或授权选项时，从 `input_options` 选择精确 `input_option_id`，连同原 `operation_id`、`input_id` 和选项 `prompt` 调用 `gemini_run_task`。不按显示文字猜测原生选项身份；已消费的旧卡等待续答，不再次要求输入。App 连接前置与权限卡不同，必须按原生连接流程处理，不能用文字“允许”代替。

服务在修改前新读、比较原状态并检查账号；这不是服务器原子 CAS。每次写入只派发一次，随后独立读取完整目标状态。`mutation_sent`、`changed=null` 或写响应回显都不是修改成功；未知结果不自动重发。结果不返回 Cookie、原生授权 ID 或完整权限对象。

`thinking_level="low" | "medium" | "high"` 用于 Chat 和 Task 请求；旧 `standard` / `extended` 仍对应低／高。档位与浏览器、权限的持久设置分开管理；Lite 的不可选思考限制继续由运行时执行。
