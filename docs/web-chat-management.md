# 紧凑的 Gemini 对话管理

账号入口 `gemini-mcp-account` 提供对话元数据控制。先用 `gemini_history` 找到准确的 `c_` ID，再使用 `gemini_manage_chat` 重命名、置顶或取消置顶。删除仍由独立的 `gemini_delete_chat` 执行；`gemini_cleanup` 仍只清理已登记的 MCP 自有对话。

## 面向任务的调用

```text
gemini_history(action="list", limit=10)
gemini_history(action="search", query="项目讨论", limit=10)
gemini_manage_chat(action="rename", chat_id=<返回的ID>, title="新标题", expected_title="之前看到的标题")
gemini_manage_chat(action="pin", chat_id=<返回的ID>)
gemini_manage_chat(action="unpin", chat_id=<返回的ID>)
```

账号面的历史工具默认返回紧凑 JSON：ID、标题、置顶状态和分页信息，不返回整页 DOM、截图或完整聊天正文。只有显式 `action="read"`／`"export"` 或 `scan_turns=true` 才读取正文；普通 read 默认每轮最多 800 字符，并保留截断提示。需要原有详细输出时指定 `response_format="markdown"` 或 `"json"`；显式旧 profile 的默认格式保持兼容。

`expected_title` 是可选的陈旧选择检查。实际修改始终以准确 ID 为目标，不用标题匹配执行修改。重命名标题有本服务的 200 字符限制；这不是宣称 Google 的上游限制。

## 可验证的结果

修改前读取最新元数据，修改后再独立读回。每个请求最多发送一次修改；相同状态可返回已验证的无操作结果。读回缺失、结构改变、字段不匹配或账号切换时都不报告成功。已发送但未验证的修改返回 `mutation_sent=true`、`changed=null`，不能盲目重发。

元数据修改不会读取会话正文，不触发模型生成。协议调用、分页和核验由服务完成；agent 只接收精简结果。

## 来源与协议边界

[官方对话管理说明](https://support.google.com/gemini/answer/13666746?hl=en)确认搜索、重命名、置顶、取消置顶和删除，但没有公开网页 Cookie RPC API。

2026-11-01 下载的 Google 公共 `BardChatUi` 前端模块确认 `BardFrontendService.UpdateConversation`，使用 `MUAZcd`，更新字段掩码为 `title` 或 `pinned`；请求中的 conversation 字段 1 是 ID，字段 2 是标题，字段 3 是置顶状态。中央注册表保存这些构造器，纯解析器分别覆盖成功、空值、拒绝和结构变化；RPC 应答不单独作为成功证明。

观察来自当日公开应用 HTML 指向的 `gemini.gstatic.com` JS 构建 `Q2P6mkfHaiI.2018.O`、模块 `xqG9Nd`，源文件 SHA-256 为 `30e70a242d07f671598a7ff36a24efa6f47949e4c810f369c0119558b944babc`。没有将账号、Cookie、私有会话或原始响应写入仓库。

授权本地合同检查已在一个专门创建的测试对话上分别验证重命名、置顶和取消置顶的独立读回。完整 MCP 入口集成与最终清理证据另见[开发状态](development-status.md)。

Task 计划与聊天 scheduled actions 是不同资源。账号 `gemini_tasks` 管理 Task 计划并独立核验写入；MCP 自有 Task 线程有关联计划时保持清理保护。最后计划确认删除后，也只能释放符合条件的自有临时线程。
