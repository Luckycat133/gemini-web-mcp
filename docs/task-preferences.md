# Task 浏览器与权限偏好

`gemini_preferences` 位于 `GEMINI_TOOLS=account-full gemini-mcp-server`，用于管理已保存的设置。Task 仍可能返回原生许可卡。

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

## 后台一次性批准

在 MCP 服务的启动环境中设置 `GEMINI_TASK_AUTO_APPROVE=true`，再重启该 MCP 连接。默认关闭。已运行的后台进程沿用各自的启动环境。只开启此开关时，后台处理 MCP 持有的 Task。Task 是 Gemini Spark 的现名。Gemini 账号的已保存权限偏好由 `gemini_preferences` 单独管理。 [Google Task 说明](https://support.google.com/gemini/answer/17094507?hl=en)

后台 worker 重新核验同账号、同一回合的原生浏览器许可卡。远程浏览器选项须有匹配的允许与拒绝。按当前浏览器偏好打开浏览器的通用卡须同时提供「始终允许」「允许一次」和「不允许」。这两种卡都只提交当前步骤的授权，每张卡最多派发一次。显式本机 Chrome 选择、会话级或持久授权、App 连接、登录和其他确认继续等待手动处理。 [Google 许可范围](https://support.google.com/gemini/answer/18766567?hl=zh-Hans)

MCP 的操作输出用 🚩 提示开关启用或 Task 等待输入、需要原生设置。`gemini_get_operation_status` 只读，批准由后台 worker 派发。Task 是否继续或完成，以原操作后续读回为准。

2026-11-20 的本地测试观察到没有浏览器选择器的通用许可卡。修复后，后台在原 Task 上只派发一次「允许一次」，任务随后完成，已核验的本地结果包含预期网页标题。独立读回确认原浏览器偏好恢复，测试 Task 和对话均已清理。

## 网页上已有的 Task

要将本机后台的一次性浏览器批准扩展到在 Gemini 网页上发起的 Task，在启动环境中同时设置下面两项，然后重启 MCP 连接。第二项默认关闭。

```dotenv
GEMINI_TASK_AUTO_APPROVE=true
GEMINI_TASK_WEB_APPROVALS=true
```

后台分页检查当前已验证账号中的待输入 Task，并核对同一对话最新回合的原生卡片。遇到受支持的一次性浏览器许可，后台按卡片的原生选项在原 Task 内用默认 Flash 续答，每张卡最多派发一次。网页上创建的 Task 保留在账号中。

用 `gemini_tasks(action="progress", chat_id=...)` 查看指定 Task 的最新进展。`pending_approval` 和 🚩 提示标明正在等待的审批，并附原网页入口。

### 自动批准指定的 Google Tasks 删除项

同时开启上面的两个开关后，在 MCP 启动环境中设置 `GEMINI_TASK_GOOGLE_TASKS_DELETE_RULES`。默认没有删除规则。规则最多 100 项。每项只含 `task_id` 和 `title`，使用完整的 `c_…` 任务 ID 和 1–300 字符的精确待办标题。标题按原样匹配，通配符没有特殊含义。格式或字段错误时整份规则不启用。以下是合成示例。

```dotenv
GEMINI_TASK_GOOGLE_TASKS_DELETE_RULES='[{"task_id":"c_demo123","title":"合成测试待办"}]'
```

后台核对原 Task 的最新回合、单项删除卡的结构化明细和确认问句。匹配后，用临时隔离的无界面 Chrome 打开原站，并点击该回合的「允许」。同账号、同 Task、同标题最多派发一次。已匹配的待处理卡显示 `browser_approval_configured`，以 Task 的后续读回核对是否继续。没有匹配规则或卡片结构无法核验时，`native_review_required` 提供原网页确认入口。

macOS 默认使用已安装的 Chrome，也可以通过 `GEMINI_TASK_APPROVAL_CHROME` 指定 Chrome 或 Chromium 可执行文件。点击流程已在 macOS 上用合成卡验证，Linux 路径尚无本轮实测记录。真实账号上的核对仅为只读。
