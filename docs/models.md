# 模型选择指南

## 当前网页模型与 MCP 选择（2026-11-22）

本项目通过登录账号的 Gemini Web 注册表发现模型。Chat 和图像沿用共享客户端；视频与音乐默认通过原生网页 composer 的 ProcessSession 工作流提交。`model` 选择的是聊天模型；图像、视频和音乐的生成器由网页媒体入口编排。聊天 selector、网页入口、媒体后端版本和本地产物是不同证据，不能相互替代。

2026-11-21，授权的 `default` Ultra 账号返回 Auto、Hydrogen、Neon、Carbon。当前 [Gemini Apps 模型帮助](https://support.google.com/gemini/answer/18648722?hl=en)也列出这些模型。实际可用性仍取决于账号、地区、套餐、额度和 rollout。历史 Flash／Pro／Argon 菜单观察不代表当前账号目录。

当日的[逐模型真实测试](live-model-matrix-2026-11-21.md)覆盖 19 个支持组合：四个 Chat，以及 Hydrogen／Neon／Carbon 各自的 Task、双参考图、产物续编、视频和音乐。Chat、Task 和图片两类工作流共 13 项通过；三个视频未产出文件，三个新音乐请求也未验证音频。支持规则与这些实际验收结果分别记录。

| MCP 输入 | 当前选择规则 |
| --- | --- |
| 省略聊天 `model`，或 `flash` / `fast` | 优先当前账号发现的 Hydrogen；旧 Flash 仅用于仍提供该家族的目录 |
| `hydrogen` / `gemini-4-hydrogen` | 精确选择已发现的 Hydrogen |
| `neon` / `gemini-neon` / `gemini-4-neon` | 精确选择已发现的 Neon；Pro／Ultra 的 Chat、Task 可用 |
| `carbon` / `gemini-carbon` | 精确选择已发现的 Carbon；需要 Ultra |
| `auto` / `自动` | 选择当前注册表中的 Auto，仅用于 Chat；不提供调用方固定思考等级 |
| `argon` / `pro` | 保留旧兼容选择；只有账号仍返回对应家族时才可用，不替换为 Neon 或 Hydrogen |
| `flash-lite` / `lite` | 仅作为有新鲜同账号基础 Flash 额度耗尽证据时的普通 Chat 候选 |

缺少精确匹配、注册表出现歧义或套餐不允许时，在生成前拒绝，不用模糊匹配把 Flash 选成 Flash-Lite，也不偷偷改变显式模型。Neon 的识别和套餐规则已纳入共享客户端。2026-11-21 的授权真实 MCP Neon 请求返回了预期结果，派发模型为 `gemini-neon`，自有对话清理完成；详细证据与离线测试分别记录在[开发状态](development-status.md)。

Task 按当前官方套餐表使用 Hydrogen，Pro／Ultra 还可选 Neon，Ultra 可选 Carbon。Auto 不用于 Task。旧 Argon 只有仍存在于账号目录时才保留兼容性；普通 Chat、Notebook Chat 和 Task 不会相互替换。

### 思考等级与额度

使用 `thinking_level="low"`、`"medium"` 或 `"high"`。中文低／中／高及 Chat／SDK 的旧 `standard`／`extended`（低／高）继续兼容。既有网页观察验证了 1／4／2 的等级编码；模型数字 selector 来自当前账号描述符，不按模型名称猜测。Auto 和 Lite 返回 `thinking_selectable=false`、`effective_thinking_level=null`，不会宣称调用方固定了思考等级。

原生网页音乐／视频对显式思考等级选择并核验实际 UI；默认 `thinking_level="standard"` 则保留网页默认，不选择 Low，也不报告已固定的有效等级。显式等级的选择证据与实际媒体产物分别验收，均不代表底层生成器版本。

Lite 门控只接受当前账号明确的基础 Flash 普通文本额度耗尽证据，60 秒后失效，基础 Flash 成功也会撤销。文件、媒体、研究、学习模式、扩展思考、认证或网络错误都不足以授权 Lite。回退仅作用于下一条新普通查询，不重发失败请求，不改变已有会话或显式模型。Lite 使用 SDK 固定默认强度。真实耗尽后的成功降级仍未验证，不为测试刻意耗尽账号额度。

## 多模态入口

`gemini_create_media(kind="image"|"edit_image"|"video"|"music")` 默认省略 `model`，选择已发现的 Hydrogen。显式选择 Neon、Carbon 等可用聊天模型时保留原选择。媒体产物中的 `request_model` 或 `model_selection.effective_model` 只标识实际派发的聊天 selector；`observed_backend` 仅记录上游明确返回的生成器证据，没有证据时留空。

视频和音乐默认使用 `GEMINI_WEB_MEDIA_TRANSPORT=native`。原生浏览器保留原用户提示，填写后选择并核验网页模型，发送前复核账号、模型和媒体入口，只发送一次。显式设置 `GEMINI_WEB_MEDIA_TRANSPORT=sdk` 才使用 SDK 兼容路线；失败后不自动切换路线重发。图片与显式付费 API 视频不受此开关影响。[11-22 诊断与安装版实测](live-media-diagnosis-2026-11-22.md)分开记录成功文件、拒绝和超时；不能用前一日 SDK 结果替代新包验收。

当日诊断确认，曾把所有注入 Cookie 都设为 HttpOnly，阻止了网页读取其 JS 鉴权 Cookie；此前八个 guest CID 未持久化，不能据此推断新旧历史存储互不兼容。按 Chrome Default 的实际属性修复后，Hydrogen 与 Carbon 原生音乐均可通过 canonical 精确同 CID／RID／candidate 读回完成状态2和有效 GeneratedMedia，并从原操作句柄保存文件及验证清理。原生 Auto 音乐仅为独立网页对照，不扩大 MCP 的 Auto 媒体支持。

### 图像生成

已记录的官方网页 bundle composer 合同确认图片、视频、音乐分别使用模式 14／11／21，写入同一个 `StreamGenerateRequest` 的字段 50（数组下标 49）。这些字段用于图片路径及显式 SDK 兼容路线；默认视频和音乐由原生网页 composer 编排。SDK 路线从 `GetUserStatus` 目录解析账号提供的媒体选项，只选择上游明确声明且可用的默认项；缺失或形状变化不能由本地猜测补齐。参见[脱敏前端证据 fixture](../tests/fixtures/web_media_composer.json)。

[Google 当前图像产品页](https://gemini.google/us/overview/image-generation/?hl=en-US)介绍免费 Nano Banana 2 和付费计划的 Nano Banana 2.1。MCP 进入网页图像生成路径，不通过静态 `Nano Banana 2` 标签锁定生成器，也不会将显式模型强制改成 Flash。Pro redo 是网页生成后的独立操作，本项目不把它冒充首轮模型选择。

支持按顺序传 `image_paths`，并用 `source_operation_id`／`source_artifact_id` 上传前一步已验证的本地图片开始新一轮编辑。新操作保留源 lineage；这不证明原生同一网页对话续编。参考图是否被遵循需要查看实际画面。2026-11-21 的授权双参考图 MCP 请求已保存并解码 2048×2048 JPEG，实际画面包含左侧红圆和右侧蓝方块，派发聊天模型为 Hydrogen。上游没有返回精确图片生成器版本，`observed_backend` 仍为空；此结果不证明原生同一对话续编或所有参考图都可成功。

### 视频生成

默认使用账号的网页「创建视频」入口。当前 [Gemini Apps 视频帮助](https://support.google.com/gemini/answer/16126339?hl=en)将该入口标为 Gemini Omni 1.2 Flash；这说明产品能力，不单独证明某次请求的精确生成器版本。

```json
{"kind":"video","prompt":"纸飞机穿过图书馆，连续跟拍","duration_seconds":10,"aspect_ratio":"16:9"}
```

网页路径的参数合同保留 `duration_seconds=10|15`、`aspect_ratio="16:9"|"9:16"`。具体选项必须由所选路线和账号的当前网页提供；无法核验时拒绝，不静默忽略指定值。必须保存、解码并验证实际视频和所需音轨；预览图不能代替视频。

原生网页视频接受一张本地 `image_path`，在 Send 前上传并核验预览 token，发前复核同一附件、账号、模型与格式；缺失、变化或未知 UI 时拒绝发送。多图 `image_paths` 仍用于图片流程。[7c Carbon 补测](live-model-followup-2026-11-22.md)已验证一条主入口同步参考视频：10.026667 秒、1280×720、H.264＋AAC，首次返回 local 且清理完成。该结果不外推所有模型、15 秒／竖屏或 compact live 路径，H／N 视频超时的派发阶段仍未核实。

显式 API 兼容路线仍保留：在 MCP 进程配置 `GEMINI_API_KEY`，再明确传 `model="gemini-omni-1.2-flash"`。它使用单独计费的 Developer API，接受整数 3–15 秒、默认 10 秒和一张参考图。普通网页调用不会自动走这条路线。真实付费 API 生成尚未验证。[官方 API 用法](https://ai.google.dev/gemini-api/docs/omni)

### 音乐生成

[Google 音乐产品页](https://gemini.google/us/overview/music-generation/?hl=en)介绍 Lyria 3.5，并提供人声、流派和时长选项。聊天模型和 `thinking_level` 都不是 Lyria 版本选择器。MCP 的实际入口参数、生成文件和精确后端证据须分别验证，不能仅凭 `Lyria` 标签保证最新版本或三分钟时长。默认路线现在通过原生网页音乐入口提交；模式 21 属于保留的 SDK 合同。2026-11-21 的一次 SDK 请求曾保存约 74.135 秒立体声 MP3 和约 74.167 秒、1024×1024 的含音视频 MP4，经独立 ffprobe 检查；该历史成功不替代当日矩阵三个音乐请求的未通过或未验证结果，也不证明新原生路线或精确 Lyria 版本。

音乐成功可保存为带音轨的 MP4；必须独立验证实际音轨和文件，不能仅凭扩展名、播放器或封面判定，也不能把 MP4 误报为 MP3。

新增音乐选项仅用于原生网页 `kind="music"`：`music_duration="short"|"standard"`、`music_vocals="instrumental"|"vocals"|"auto"`。只有用户明确要求时才设置对应项，例如短纯音乐可传以下参数；未指定项保持 `None`，以网页实际默认为准，不从提示风格或菜单首项猜默认。`music_vocals="auto"` 是人声选项，与仅用于 Chat 的 Auto 模型不同。显式 SDK 兼容路线不支持这些选项，会在提交前拒绝而非忽略。当前交付包已同步这些合同；实际补测仍有拒绝和超时，不保证每个模型／选项组合生成成功。

```json
{"kind":"music","prompt":"A short instrumental piano piece","model":"neon","thinking_level":"low","music_duration":"short","music_vocals":"instrumental"}
```

上述为默认 `gemini_create_media` 的调用格式。相同 Neon／Low／short／instrumental 参数已在 `2663` 无 hook 安装版的兼容入口 `gemini_generate_media(media_type="music")` 实测成功：248.17 秒保存 65.149333 秒 MP3、65.167333 秒 1024×1024 含音轨 MP4 和封面，独立大小／哈希、全片解码及非静音检查通过，原对话清理完成。这是共享服务的一次实际成功，不冒充默认入口的另一次真实调用。

同提示、同 short／instrumental 的 High attempt 2 只返回完整乐谱，以 `ARTIFACT_NOT_RETURNED` 收口并完成清理；更早 High 尝试还遇到提交前菜单状态失效／确认超时。这些单次失败不证明 High 全局不支持音乐。服务器仍默认 `standard`，不把它等同 Low，不自动从 High 降档重发。各轮原身份、失败和其他模型补测见[诊断报告](live-media-diagnosis-2026-11-22.md)。

前轮`0.20.0`源码／安装版已统一到 `60fe2a5025546098`，可按原句柄读回上述2663 Neon Low音乐、b562 Neon standard的10.026667秒720p视频及更早67b Hydrogen视频／音乐；这些文件均保留原生成构建身份。最新60fe Hydrogen视频实际提交后明确上游技术拒绝、没有文件，已验证清理。旧成功不替代本轮失败，App既有连接是否加载新包仍需另行核实。

## 等待与恢复

`gemini_create_media` 启动后台操作，默认 `wait_seconds=120` 在同一次工具调用中等待原操作。设为 0 立即取回句柄。等待结束后仍未完成时，用原 `operation_id` 调用 `gemini_get_operation_result(wait_seconds=30)`；允许 0–120 秒。状态、等待和恢复都不重新提交生成。

原生提交若暂时只观察到对话 CID，操作保留 `native_media_submitted` 元数据与清理保护，随后读取原对话恢复回复身份；只有精确回复和产物证据齐全后才进入已验证结果。CID、点击发送或等待结束都不代表生成完成。

恢复和清理沿用已有 canonical 合同：精确回复身份与完成证据齐全后，先保存并验证本地产物，再释放操作保护，按原账号、保留设置和租约执行精确删除及独立读回。只有 CID、缺少 RID／candidate 或未知结果时继续保护。试验性的原生 GraphQL 适配和专用清理链已移除；其被拒绝的探测结果不构成产品能力或缺席证据。

## 历史证据

2026-09-26 至 11-06 的授权测试曾验证 Flash／Argon 文本、图片生成与编辑、约 58–64 秒音乐，以及 10 秒音视频产物；也观察到超时和上游拒绝。这些是当时账号的单次结果，不能证明当前模型目录、当前媒体后端或持续可用性。完整记录见[开发状态](development-status.md)与[历史社区和实测材料](model-community-evidence.md)。`gemini_list_models` 是完整兼容目录中的运行时模型查看入口。

当前本地`0.20.6`最终64e8已取得一次无hook Hydrogen standard、10秒横屏中文橘猫视频，独立解码、大小／哈希和抽帧QA通过，清理completed；这条最终视频只有H.264画面、没有音轨，不能承诺AAC。原英文蓝立方体首轮明确上游拒绝；后续私有harness导入意外新增的同Hydrogen／同英文请求取得H.264＋AAC真视频，并在原句柄只读恢复，首轮失败没有回填。实际Neon对照未调用，Google内部原因仍未知。候选6eab生成、最终安装与逐句柄只读恢复分别记录在[0.20.6报告](live-media-0.20.6-2026-11-22.md)。另外两次60fe观察中，实际官方Web出站模型ID哈希与账号Hydrogen注册表一致，不仅是UI标签；这不是最终无hook case的抓包，精确隐藏生成器版本与英文失败的Google内部原因仍未观察。

`0.20.7`仅补有限中文拒绝的严格终态识别，不改变模型／思考等级选择、提示或生成路线。原`0.20.6`文件不重标新版本，原句柄收口与App实际调用路由另见[0.20.7验收](live-media-0.20.7-2026-11-22.md)。
