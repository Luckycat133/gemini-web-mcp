# Runtime Skill 路由与现有图片交接评估

日期：2026-10-03。基线提交：`985e8f89697e0808b10ccc9af3d137d043cafc5b`，评估对象包含当前工作树中的未提交修改。

客户端：Codex 与其 IAB；系统：macOS；四个 Runtime Skills 的声明版本：`0.2.2`。本评估代理的精确模型 ID 与已安装 wheel 版本未独立观察，不作推定。

独立代理先读 `AGENTS.md`、开发 Skill 及其验证/工具设计参考，再读四个 Runtime Skills 的正文和相关示例。本轮没有使用静态路由夹具得出下表；没有调用 Gemini 生成、私聊读取、账户变更或历史扫描。新生成次数、生成重试次数、重复启动次数均为零。下表是调用选择评估，不是这些场景的实机通过记录。

## 11 个真实措辞的判断

| 场景 | 最小路由与动作 | 应交付结果、失败与恢复 |
| --- | --- | --- |
| 1. 给这份方案找漏洞并引用当前资料 | `gemini-assist` → 一次 `gemini_search`，查询携带必要方案内容和查证问题；代理据证据完成批评。单独需要第二意见时才增加 `gemini_ask`。 | 修改建议与支持它们的实际来源。检查 `sources` 与 `grounding_state`；`answer_only` 不能作为当前资料查证通过，来源不足可升级一次 Research，并保留 handle。 |
| 2. 看这张图和 PDF，替我提炼修改建议 | `gemini-assist` → 一次 `gemini_understand`，typed inputs 中图片与 PDF 分别保留唯一 id、kind、path。 | 同时结合两份材料的建议与逐输入结果。任一输入失败要明确缺口；修正该输入后恢复，不把单图分析记为完整任务。 |
| 3. 把这个蓝色木猫变成金色，保持构图 | `gemini-create` → `gemini_edit_image(prompt=…, image_path=…)`。 | 输入、输出身份不同；解码输出，核对金色和构图，再将编辑结果接入目标项目。空回复、参考图、未验证路径不算完成；保留输入与不确定输出的恢复依据。 |
| 4. 做一段六秒宣传片，再做背景音乐，嵌入项目 | `gemini-create` → 视频 start + 同 handle 的 status/result，再音乐 start + 其 handle 的 status/result；两次 start 各先保存独立 opaque idempotency key。 | 验证视频和音频的实际文件与时长，并导入项目。六秒需要测量；较长素材可在项目中裁成六秒并核对。音乐与视频独立验收。排队、部分保存、超时均继续恢复原操作，不能重新生成作为恢复。 |
| 5. 上次视频做了一半代理退出了，现在继续取文件 | `gemini-create` → 对已保存 `operation_id` 调用 `gemini_get_operation_status/result`；完成状态可直接 result。 | 获取并使用同一操作的已验证视频。先找已有交接 handle/本地文件；handle 缺失、认证不匹配或 `OPERATION_EXPIRED` 要保留该限制，请求确切恢复依据，不能借此扫描历史或重复 start。 |
| 6. 查这个已知会话并删掉我明确指定的测试记录 | `gemini-account` → `gemini_history(request={action: read, chat_id: …})`，然后对明确授权的确切会话逐个 delete。已知目标无需先列全部历史。 | 返回所请求的会话信息与删除 read-back。`accepted`、读取失败或有限列表未发现目标均不算已删。若“记录”指部分消息，现有 chat 删除粒度不匹配，不能推定授权删除整个会话。 |
| 7. 只修改本地提示词模板 | 已指定普通项目模板文件时，用本地文件编辑；只有明确指 Gemini 本地 Prompt library 时，才走 `gemini-account` → `gemini_prompts` get/update/read-back。 | 交付修改后的本地模板和差异。Prompt library 不调用 Gemini 生成；“本地提示词”本身不足以推定要改 `prompts.json` 或读账户。 |
| 8. 对比账户模型但别碰历史 | `gemini-account` → 一次 `gemini_account(request={action: models})`。 | 基于观察到的模型数据比较；不读 history。错误或缺失模型数据保持未核实，不能从别名推定额度、媒体后端或营销版本。 |
| 9. 普通项目代码重构 | 常规项目开发；若对象就是本仓库，使用 `gemini-web-mcp-development`。Runtime Gemini 调用并非必需。 | 实际代码改动、相应验证和限制。不能把一次 Gemini 评论当重构交付。 |
| 10. 查天气 | 默认用已有专用天气工具；若上下文没有地点/日期，补足必要输入。明确要 Gemini 查证时才选 `gemini-assist` → `gemini_search`。 | 指定地点和日期的当前天气。通用天气请求不要求 Gemini 账户或历史。来源、地点、日期缺失要说明，不推定用户所在地。 |
| 11. 同时生成封面并复核已有研究报告 | `gemini-web-mcp` 编排两个 focused 步骤：create 的 `gemini_generate_image`；assist 对报告文件用 `gemini_understand`，已给出纯文本用 `gemini_ask`。若报告已有 Research handle，先在原 handle 上 result。 | 封面插入目标项目，报告形成实际修订建议；两项分别报告完成或缺口。复核已有报告不应启动新 Research，缺少报告 locator 不授权历史扫描。 |

已知工作流无需先调用 manifest。缺失工具、模式或 schema 不匹配时，才执行必要连接/发现检查。结构化状态优先于兼容文本；媒体文件成功与后续 cleanup 成功分别判断。

## 实际发现与重读结果

首次阅读 umbrella Skill 时发现两个会影响选择的冲突，已报告给维护代理并在共享树修复后独立重读：

1. `SKILL.md` 的 Long Operations 仍要求使用 Videos 页面，直到存在已验证 MCP 视频路由，和同文件 focused 视频路由及 create Skill 冲突。现已改为 focused native video/music 路由，同时保留“匹配的已验证产物才是 live 成功”的边界。
2. cleanup 说明仍称延迟任务保存在内存，和 SQLite 重启恢复约定冲突。现已明确 registered delayed jobs 使用认证作用域 SQLite，pending cleanup 权限可独立续期。

两项文档 finding 已关闭；本评估代理没有修改技能或源码。当前 host 公开给本代理的 callable catalog 包含 focused assist 和兼容 core，未公开 focused create/account。它们的调用选择可评估，连接与实机可用性不能据此宣布通过。

措辞还需要上下文的三处已在判断中保留：本地模板是否属于 Prompt library、天气地点、测试记录是会话还是部分消息。这些是任务输入边界，不是本轮确证的源码缺陷。

## 已真实完成的本地产物交接

把两张既有真实图片按原始字节复制到一个独立临时产品卡项目。项目包含 `index.html`、`README.md`、`asset-manifest.json` 和 `assets/` 中的两张 JPEG；临时绝对路径单独交接，未写入本报告。页面默认显示金色，提供蓝色切换和“打开当前图片”链接，所有图片引用均为相对路径，无外部图片、字体或脚本依赖。

| 匿名产物 | MIME | 尺寸 | 字节数 | SHA-256 |
| --- | --- | --- | --- | --- |
| 蓝色版本 | `image/jpeg` | 2816 × 1536 | 2,598,203 | `a75c0c30f876f9b5abf885f854c501a4a507d01f8d483ffa355199bb8cc1f5e7` |
| 金色版本 | `image/jpeg` | 2816 × 1536 | 2,218,181 | `cb95c0976f661789039b8de11260cba093fe2ea8f504bd657f6de60a1b839643` |

实际验证：

- `file` 独立确认原文件和复制文件的 JPEG MIME，`sips` 分别读取两组尺寸。
- 每张图的复制前原文件、复制后目标、复制后原文件 SHA-256 全部相等；原产物保留，未重新编码、裁剪或生成新图片。
- 解析 HTML 的 3 个静态 src/href 引用，均为相对路径且指向存在的文件。两张图通过本地 HTTP 返回 200，响应 MIME、长度与 SHA-256 均与复制文件匹配。
- Codex IAB 实际载入页面；两张图 `complete=true`、`naturalWidth=2816`、`naturalHeight=1536`。实际切换蓝色再切回金色，图片、所选按钮状态和相对图片链接都同步变化。
- 查看两种颜色的页面截图；图片完整显示，当前桌面视口无横向溢出。保留金色作为交接预览。交接说明支持直接打开 HTML 或移动整个项目文件夹。

交接任务通过的是“已存在图片 → 原字节复制 → 页面引用 → 实际可用预览”。没有把此结果当成新的 Gemini 编辑、视频/音乐、Research 恢复、账户删除、移动端布局、安装分发或 live entitlement 验证。报告与临时预览为本代理的全部写入；未提交或推送。

本轮离线源码套件、MCP 协议、wheel/Skill 安装门禁均为 `NOT_RUN`；这是文档新增与现有产物交接评估，依据 `docs/agent-verification.md` 无需重复全套源码门禁。它们的独立验证结果应由相应报告提供。
