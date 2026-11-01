# 模型选择指南

## 官方调用方式与本项目入口（2026-11-01）

[Gemini Apps 官方说明](https://support.google.com/gemini/answer/18648722?hl=en)将聊天模型与思考等级分开选择：手动选择的模型及低／中／高等级会保留在当前对话中，未选等级时网页默认为 Low。套餐和剩余额度决定可用范围；达到用量限制后可继续使用 Flash-Lite。本项目按用户要求额外收紧为：Flash 默认，Lite 仅在确认额度耗尽后允许，且该回退中不开放等级选择。

同一官方帮助页按体验区分模型：Chat 列 Flash-Lite、Flash、Pro、Argon，Task 仅列 Flash、Argon。本项目在生成前拒绝不支持的 Task 模型，不静默切成 Chat 或更换模型；普通 Chat 和 Notebook Chat 的 Pro 不受这项限制。MCP 已支持 `low` / `medium` / `high`；当前账号的界面配置与公开前端共同确认编码为 1／4／2，请求体和模型请求头同步。旧 `standard` / `extended` 仍兼容低／高。

[官方 API 模型目录](https://ai.google.dev/gemini-api/docs/models)使用以下独立模型 ID。这些是 API 名称，不是本项目 Cookie 传输的模型 ID 或数字选择器：

| 模型 | 官方 API ID | 本 MCP 推荐输入 |
| --- | --- | --- |
| 3.8 Flash | `gemini-3.8-flash` | `model="flash"` |
| 4 Argon | `gemini-4-argon` | `model="argon"` |
| 3.1 Pro | `gemini-3.1-pro-preview` | `model="pro"`，明确需要时 |
| 3.5 Flash-Lite | `gemini-3.5-flash-lite` | 由运行时额度策略处理 |

[Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)是官方推荐的新项目入口，使用 API Key；旧 `generateContent` 仍受支持。官方 Python SDK 调用示例：

```python
from google import genai

reply = genai.Client().interactions.create(
    model="gemini-3.8-flash",
    input="用两句话解释潮汐。",
    generation_config={"thinking_level": "low"},
    store=False,
)
print(reply.output_text)
```

`low`、`medium`、`high` 是[官方 API 思考参数](https://ai.google.dev/gemini-api/docs/thinking)。API 的模型默认值与网页默认值不能混用；API 中 Lite 本身也有思考能力。本项目继续通过已登录网页的 Cookie 和实际发现的模型注册表调用，不自动改接 API Key 接口。官方文档没有发布网页 JSPB 数字字段合同，不能仅据 API 枚举猜测 Cookie 请求的三档映射；下文保留已验证的 `standard`／`extended` 兼容边界。

[Flash API 模型页](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)将其输出列为文本，未提供原生图片或音频生成。本 MCP 在 Gemini App 中选择 Flash 并使用图片、音乐工具，是由 App 编排媒体后端；这不等同于用该 API 模型 ID 直接输出图片或音乐。

## 模型契约

2026-10-31 网页模型菜单观察到四个模型：3.5 Flash-Lite、3.8 Flash、3.1 Pro 和 4 Argon。默认使用 Flash；不自动选择或回退到 Pro。按项目使用经验，这些模型都有较强的看图和推理能力，应按任务效率、风格和实际结果选型。

项目保留以下 MCP 兼容别名，并允许传入当前账户运行时模型名。已发现的运行时模型按确切家族匹配：Flash 不会模糊匹配到 Flash-Lite。缺少可证明匹配时拒绝派发，不用默认模型替换显式选择。

| MCP 别名 | 观察到的 Web UI 显示名 | MCP 请求名 |
| --- | --- | --- |
| flash-lite / lite | 3.5 Flash-Lite | `Flash-Lite` |
| flash / fast | 3.8 Flash | `gemini-3-flash` |
| pro | 3.1 Pro | `gemini-3-pro` |
| argon / 4 Argon / gemini-4-argon | 4 Argon | `gemini-argon`，仅解析已发现的确切家族 |

Argon 别名已实现并通过离线路由测试；没有硬编码或猜测它的模型 ID。当前账号没有发现对应家族时返回不可用，不替换成 Flash。2026-11-01 授权真实 stdio 写作请求约 31 秒返回正文，实际模型元数据为 `gemini-argon`，并完成自动清理。`gemini_list_models` 是兼容入口，可读取运行时注册表；网页显示版本和注册表名称可能不同。

[Google 的 3.8 Flash 公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/)和 [3.5 Flash-Lite 公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-6-flash-3-5-flash-lite-3-5-flash-cyber/)可作为模型背景资料，不替代当前账户的实际验证。

使用 `thinking_level="low"`、`"medium"` 或 `"high"` 选择低、中、高；中文低／中／高也可用。旧 `standard`／`extended` 与标准／扩展继续兼容低／高。中档限额或成功不建立、清除基础 Flash 的额度耗尽证据。`thinking` 仍是旧兼容模型别名。

开发证据中，旧 JSPB 枚举包含 `STANDARD=1`、`EXTENDED=2`、`DEEP_THINK=3` 和 `MEDIUM=4`；这些数值与当前网页标签的对应关系仍需实测，不应据此猜测请求参数。

## 模型与选择

- **Flash**：网页显示为 3.8 Flash，是普通对话、联网搜索、图片理解及多模态生成首选。它也能承担推理任务；具体结果仍须验证。
- **Flash-Lite**：网页显示为 3.5 Flash-Lite；仅作为 Flash 文本额度不足时的受控候选，不作为日常默认。
- **Pro**：网页显示为 3.1 Pro。用于明确的高阶需求或用户显式选择；账户权限和额度会影响可用性。
- **Argon**：网页显示为 4 Argon。项目用户的实践经验偏好它处理写作、创意、角色对话和文风，也会用于特别难的推理。长文一致性、事实和约束仍须独立核查；模型路由与当前账户可用性应以运行时验证为准。

这些是任务选型经验，并非统一排名或每次请求都会成功的保证。正反社区案例及其环境见[模型社区与实测证据](model-community-evidence.md)。

### Lite 额度回退

客户端运行时门控已实现：只有当前账号有新鲜、明确的基础 Flash 文本额度耗尽证据时才允许 Lite；本地证据 60 秒过期，基础 Flash 请求成功也会撤销。扩展思考、文件、研究、媒体专用额度错误及认证或网络失败均不足以授权 Lite。可对下一条新普通查询自动降级，原请求不重发，也不自动改变已有会话或显式 Pro/Argon 选择。Lite 使用 SDK 固定默认强度 1，不接受用户选档；不能宣称请求中没有思考字段。调用方不需要自行判断额度或删除强度参数，返回的 `model_selection` 会给出 requested/effective、fallback reason 和 thinking selectability；Lite 返回 `thinking_selectable=false`、`effective_thinking_level=null`。真实 SDK 离线请求及公开 MCP 集成测试已通过，真实账号额度耗尽后的成功降级场景尚未验证；测试不会刻意耗尽账号额度。

授权本地 stdio 拦截测试通过浏览器 Cookie 自动载入并发现四个运行时模型：无额度证据时显式 Lite 请求返回 `CAPABILITY_UNAVAILABLE`，生成派发和新增清理记录均为 0。测试在 SDK 生成边界另设拒绝派发保护，不会为验证规则消耗生成额度。这只验证真实登录下的拒绝路径，不证明真实耗尽后的成功降级。

## 媒体模型映射

### 图像生成

Flash-Lite 首轮使用 **Nano Banana 2 Lite**；Flash 和 Pro 首轮使用 **Nano Banana 2**。此前 MCP 测试中 Flash 与 Flash-Lite 均保存了有效的本地图片，但上游响应没有标出精确图片后端，`observed_backend` 为 `null`。如果网页里出现 Pro redo，那是首轮生成完成后的二次操作，不是独立首轮图像模型。[Gemini 图片帮助](https://support.google.com/gemini/answer/14286560)说明了这三种图片路线。具体请求仍须验证本地产物。

### 视频生成

当前网页专用 [视频页面](https://gemini.google.com/videos)显示 Gemini Omni。
此前网页页面生成并下载了 10 秒 MP4，旧通用聊天路线只返回文本。2026-10-31 授权本地测试中，MCP 专用模式返回并验证了 10 秒 MP4。后续单次功能面 `model="pro"` 测试返回了 10 秒 1280×720 视频和音轨，Flash 对同一提示返回普通文本拒绝。按当前模型选择要求，功能面的 `gemini_generate_video` 默认 Flash，不自动回退到 Pro，显式模型不会被替换；历史 Pro 成功不构成 Flash 已验证可用。独立缩略图不算视频完成。Google 的[视频帮助](https://support.google.com/gemini/answer/16126339)
也将现行生成模型称为 Gemini Omni。

### 音乐生成

Gemini App 公告介绍 **Lyria 3.5**；音乐模式的 Lyria 选项与聊天模型 Pro 是不同选择。此前 Flash 请求曾返回约 58 秒并经验证的 MP3；Pro/extended 请求也出现过失败，因此不能断言音乐必须使用聊天 Pro 或 Argon。先用 Flash；仅在明确需要某个高阶后端、时长、质量或上游明确不支持时，选择合适模型或音乐选项。上游响应没有可验证的版本标识时，`Lyria` 只是后端标签，`observed_backend` 留空。`thinking_level=extended` 是兼容思考参数，不是音乐版本选择器。实际音视频文件及其格式需要验证。

2026-10-31 的网页观察中，从默认 3.8 Flash 进入“制作音乐”后 Flash 仍被选中，界面显示时长、人声和流派选项；时长菜单为“短版”和“标准”。音乐模式的模型菜单列出四个聊天模型，没有显示必须使用 Pro 或 Argon 的提示。本次只查看菜单，没有提交生成，不能据此保证音频成功。

2026-11-01 再次真实调用默认 Flash/standard 音乐工具，提交连接关闭后由后台 worker 完成。独立 AVFoundation 检查确认 MP3 与 MP4 均约 63.95 秒；MP4 为 1024×1024，含一条音轨和一条视频轨道，另有独立封面。该次请求完成自动清理，未切换到 Pro、Argon 或 Lite；仍不推断精确 Lyria 版本或所有后续请求的成功率。

参见 [Lyria 3.5 公告](https://blog.google/innovation-and-ai/products/gemini-app/better-tracks-lyria-gemini/)和 [Gemini 音乐帮助](https://support.google.com/gemini/answer/16901237)。

## 选择流程

1. 默认使用 Flash，检查回答的来源证据以及媒体或研究的实际结果。
2. 写作、创意、角色对话或特别难的推理，可按用户经验选择 Argon，独立核查长文与约束；可用性依当前账号的运行时注册表。
3. 用户明确选择其他模型或有具体高阶需求时，传入合适选择；不因名称更高级自动切换。额度耗尽时由运行时门控处理 Lite，不手动重发原请求；实际耗尽场景仍待验证。
4. 不因模型名称、响应文字或任务已启动而认定媒体生成、外部应用操作或研究报告已完成。

## 使用方式

默认功能面可直接使用 `gemini_ask`；普通问题、当前联网问题和自然语言应用请求均可直接表达。联网和工具调用不提供启用开关；App 操作是否完成需要独立证据。

以下是旧兼容入口的显式参数示例，并非自动模型选择建议：

```text
gemini_chat, message: 你的问题, model: flash, thinking_level: extended
gemini_chat, message: 你的问题, model: pro, thinking_level: extended
```

会话创建可显式指定默认模型：

```text
gemini_start_chat, model: flash, thinking_level: standard
```

兼容工具 `gemini_list_models` 可读取账户运行时注册表；可用性还取决于账户、地区、配额和上游服务。更多联网与应用边界见 [Connected Apps 与 Task 接入记录](connected-apps.md)。
