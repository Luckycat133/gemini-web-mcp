# 原生媒体提示与入口诊断（2026-11-22）

本页保留原始`0.20.0`构建与生成／读回证据。当前源码已按修复范围更新为`0.20.6`，最终64e8已构建安装且门禁通过，候选无hook Hydrogen文件与最终包／App验收见[独立0.20.6报告](live-media-0.20.6-2026-11-22.md)，不修改下面各旧build的产品版本、文件或历史结果。[版本范围与依据](changelog.md)

本页按来源分开记录：最初八项是 **instrumented workspace/source 诊断**；67b 的四项是独立无 hook 安装版批次；Cookie bridge／NoSend 是源码检查；93／ca64／2663／b562／60fe 的尝试与原句柄恢复另列。源码与共享安装版已统一到60fe，门禁及旧结果读回通过；2663 Neon Low 音乐、b562 Neon standard 视频有真实文件和独立 QA，最新60fe Hydrogen 视频明确上游拒绝。不同构建、参数和失败不合算为“最新安装版四项全通过”。产品版本仍为 `0.20.0`，没有新发布。

最初源码诊断使用 fcdf 解释器／依赖，但通过 workspace `PYTHONPATH` 和私有 `sitecustomize` trace 导入源码，个别 case 还做提示对照。日志中的 `runtime=fcdf…` 仅表示解释器来源，不能当 fcdf 安装版生成证明。后续 93／ca64 的真实 High 尝试仍在提交前失败；两个旧音乐句柄已由 ca64 收口为明确失败并完成清理。High 状态机和 typed-429 分支修正已进入 2663，首轮失败、后续 High 乐谱与 Low 真实音频分别保留，不能由离线门禁推断全部实测通过。

[ba8e 原 19 项](live-model-matrix-2026-11-22.md)、[7c 补测／fcdf 原句柄恢复](live-model-followup-2026-11-22.md)保留各自记录。本轮诊断不覆盖旧失败、round／attempt 或构建身份，App 旧连接的限制也不替代本页实际 CLI/source 证据。

## 已记录的对照

| 诊断 case | 输入／选项差异 | 本地结果与证据 |
| --- | --- | --- |
| Hydrogen 视频，旧包装、无参考图 | 沿用 SDK 能力／提示包装，10 秒、16:9 | 真实 trusted-click 提交后精确失败读回；276.14 秒记录 `UPSTREAM_REJECTED`，无视频 |
| Hydrogen 视频，旧包装、有参考图 | 相同包装并附一张图 | 37.06 秒精确失败，`UPSTREAM_REJECTED`；不据此判参考图能力不可用 |
| Hydrogen 视频，原用户提示、无参考图 | 诊断中移除旧包装，10 秒、16:9 | 99.11 秒保存 10.026667 秒、1280×720 H.264＋AAC 视频；第 4／8 秒帧实际查看为蓝纸立方体转动 |
| Neon 视频，原用户提示 | 15 秒、9:16 | 33.02 秒精确拒绝；单次结果不证明 Neon 不支持视频 |
| Neon 视频，原用户提示 | 10 秒、16:9 | 107.12 秒保存 10.026667 秒、1280×720 H.264＋AAC 视频；帧视觉 QA 待回收，不写最终安装版通过 |
| Carbon 音乐 | 同一音乐提示，显式 short／instrumental | 206.15 秒保存 61.048125 秒 MP3、61.067333 秒含音轨 MP4；非静音信号验证通过 |
| Hydrogen 音乐 | 同一音乐提示，显式 short／instrumental | 130.09 秒保存 62.093 秒 MP3、62.100333 秒含音轨 MP4；非静音信号验证通过 |
| Neon 音乐，旧包装 | 明确 NoTool 回复 | 精确 completed-empty 证明后退休原本地 observer，再由诊断分类器恢复为 `ARTIFACT_NOT_RETURNED`，清理完成；没有新生成或取消 |
| Neon 音乐，原用户提示 | 显式 short／instrumental | 初期 UI 显示生成控制但无音频；后续为文本／乐谱替代读回，状态仍待严格恢复，不计成功 |

上述八条已有结果的操作记录均报告清理 `completed`，本快照未另行采集 fresh 独立缺席复核。十个保存文件的大小和 SHA-256 已独立匹配。H／C 音乐均为 44.1 kHz 双声道，平均音量约 −14.3 dB，验证为非静音；没有声称人工试听。

## 可以证实与仍需验证的解释

旧包装视频失败、原用户提示视频成功，说明 native 已选媒体入口仍复用 SDK 能力／提示前缀存在可检验的适配问题。不能把这个对照扩大成“所有旧失败均只由包装造成”；参考图、时长／画幅和模型的变化分别保留，Neon 原提示 15 秒竖屏仍拒绝。

实际 WebChannel 请求观察到的 endpoint 是 `https://geminiweb-pa.clients6.google.com/v1/processSession`，外层表单字段 `req0___data__` 承载 base64 protobuf。已观察音乐 feature 21、视频 feature 11；Hydrogen 音乐显式 short／instrumental 的真实 context 中，时长代码为 4、人声代码为 1。回答出现 NoTool 不能反推请求没带音乐模式，不能由模型自述代替出站证据。

严格终态证据已说明部分空媒体拒绝在本地浏览器观察超时前就能确认。应仅在精确账号／原回复、完成状态和空媒体／真实拒绝全部成立后提前结束本地观察，不根据普通文字、引用示例或缺少 URL 就判拒绝，也不把停本地 observer 写成取消远端生成。新的 Neon NoTool 首句识别同样保留该 gate。

拟交付的源码修正限于 native：保留原 user prompt，避免复用旧 SDK 能力／提示包装；已证明的精确空媒体拒绝提前结束本地观察，并补齐该拒绝句识别。SDK、图片和 API 原行为保留。最终因果与交付结论以新包、无追踪 hook 的实际复测为准。

## 独立无 hook 安装版批次（67b）

此批次不使用 workspace `PYTHONPATH` 或诊断 hook，绑定 `67b1f735a3240a88`。结果为三项通过、一项 High 提交前失败，不回填最初源码对照。

| 安装版 case | 结果 | 文件／边界 |
| --- | --- | --- |
| Hydrogen 10 秒横屏视频 | 通过，96.20 秒 | H.264 1280×720@24fps；视频流 10 秒、容器 10.026667 秒，AAC 48 kHz 双声道 |
| Neon 10 秒横屏视频 | 通过，124.07 秒 | 同上，实际保存视频；不是只改标签或 DOM 探测 |
| Hydrogen 音乐 | 通过，88.14 秒 | MP3 61.675042 秒，MP4 61.700333 秒、1024×1024＋AAC，44.1 kHz 双声道 |
| Neon High 音乐 | 未提交，64.08 秒 | `CAPABILITY_UNAVAILABLE`／`native_media_thinking_level_not_submitted`；保留失败轮次 |

独立 QA 确认七文件大小／哈希匹配，四个音视频文件全片解码无错，三个 JPEG 可解码。H／N 视频第 4／8 秒拼图可见蓝纸立方体面向和折痕变化，符合转动；只称抽帧视觉证据，不声称逐帧检查。H 音乐非静音，平均约 −14.3 dB、峰值 0 dB，没有人工试听声明。[67b 独立 schema](live-media-diagnosis-installed-67b-2026-11-22.json)单列三成功／一失败。

## 人工验证 Cookie、限流与源码 NoSend

用户已完成 Google 人工验证。随后 preflight 仍出现 429，实际原因之一是既有 26 项 Cookie allowlist 漏了 Default 中的 `GOOGLE_ABUSE_EXEMPTION`，使 SDK／空白 native context 丢失人工通过状态。源码 bridge 仅转交 `.google.com`、根路径、未过期且原 Default PSID owner 匹配的 Cookie，不伪造值、不自动解验证码；本报告不含 Cookie 值。02:47 UTC 源码 preflight 已验证同 owner、`verified=true`、零生成，不应再记为用户未登录或未完成验证。

Neon High 的源码 NoSend 在 24.159 秒到达已验证 Send guard：实际同一菜单的七个 radio 与描述文本已适配，thinking／model checked 均完成，首次 editor focused 为 true。该轮没有触发诊断 refocus，当时也没有额外 focus 重试实现。该次 `CAPABILITY_UNAVAILABLE` 是故意停在 Send 前的诊断 abort，不能记为业务能力失败。它没开诊断 SETTLE，不能据此因果断言某个 settle 处理已解决生产问题；一次先前 focus 超时保留为瞬态事实。后续 2663 的有界 refocus 修正另记，不能改写此轮来源。

HTTP 429 改为中央 `RATE_LIMITED`，不再误报 `AUTH_REQUIRED`。media-only SDK scope 浮出被 `read_chat` 吞掉的 429，并禁止该 scope 内 batch 自动两次重试；其他 SDK 行为保留。正常 canonical 读回保持 15 秒间隔，本地 DOM 观察继续；限流恢复退避为 300／600／1200／1800 秒，保留 tuple／保护，不取消或重发生成。

## 93 候选与仍未解决的真实尝试

93 候选 SHA-256 为 `93f423a3e53b75d998a60c21a2609a41a9cb13f23a7fec5ccbbb7c99a443df6c`，版本 `0.20.0`。143 个源码／data 文件零字节差异、零额外文件，47 个 distributions 中 46 个固定依赖零差异；Ruff、Mypy（152 文件）、4,840 项离线、2,637 项 retained contract、clean-wheel 与 profile 检查通过。这些门禁不等于新四项实测全通过。

- 93 的真实无 hook Neon High 在 64.13 秒返回 `CAPABILITY_UNAVAILABLE`／`native_media_thinking_level_not_submitted`，未派发。生产模型选中菜单的稳定状态 barrier 仍有缺口，源码已补，需新包验证；不能用 NoSend 成功回填这一失败。
- 原 source raw-score 音乐句柄在 93 恢复 124.06 秒仍为 partial／`READBACK_UNVERIFIED`。独立原 CID 读回已完成，含明确 NoTool 和已观察技术错误卡，但 strict empty proof 为 `changed_shape`；修正必须要求已完成精确回复与已知拒绝／错误卡证据，不能把普通未知形状直接判拒绝。
- 标准中文自然提示的另一原句柄在 93 恢复 124.04 秒仍为 partial／`media_artifact_not_observed`。独立读回证明是完整 ABC 乐谱而非音频、exact completed-empty proof 成功；拟以该严格 gate 下的有限乐谱替代识别返回 `ARTIFACT_NOT_RETURNED`，不记为音乐成功。

这些本地读回 observer 已按精确 owner／进程身份退休，没有 cancel 或新生成，原身份与保护保留。另一次旧 worker 占句柄也只退休经核对的本地 observer 后由新 reader 继续。以上是 93 的原记录，后续恢复见下节，不改写成 93 当时已终态或已清理。

## ca64 的原句柄收口与后续缺口

ca64 候选 SHA-256 为 `ca64fc6b3975fb06aba434a5444478e9ee074179e4fdf6f3193b32b2d3a6e7b3`，143 文件／47 distributions／46 固定依赖核对均无差异，Ruff／Mypy（152 文件）、4,922 项离线／2,719 项合同、六入口及 profile 检查通过。它的新无 hook Neon High 仍在 66.11 秒提交前返回 thinking-level `CAPABILITY_UNAVAILABLE`；这一失败保留，不能用源码 NoSend 成功替代。

两个旧句柄在 ca64 **仅恢复读回，没有重发**：source raw-score 技术错误卡在 14.06 秒分类为 `UPSTREAM_REJECTED`，标准自然提示的 ABC 乐谱在 14.03 秒分类为 `ARTIFACT_NOT_RETURNED`／`completed_music_notation_without_artifact_observed`，清理均完成。独立只读复核确认这两组范围内 13／13 个已知目标缺席，零次修改；不把 already absent 计为新删除，也不扩大到未知或其他用户目标。

另一次审查发现 typed 429 被当作 unknown 后多发一次读回，修正四个分支并通过四项新回归、225 项相关检查，随后纳入下述 2663。ca64 不是“最终全部通过”，其 High 提交前失败仍保留。

## 2663 批次：门禁通过，首轮四项没有文件

候选 wheel SHA-256 为 `2663f859aa4d56f4e05985def66a76c5b69c1cb7d00a66711c7943a5f1a944e8`，已安装并切换 `current`。143 文件／47 distributions／46 固定依赖核对全匹配；Ruff、Mypy（152 文件）、4,935 项离线（79.02 秒）、2,732 项合同（46.00 秒）、clean-wheel 六入口及 profile 检查通过，产品版本仍为 `0.20.0`。

此包修正正式 `open_model_menu`：关闭过渡中持续判断 exact-open 或 proven-closed，只有后者允许点击，每次开窗最多两次总点击。编辑框首次点击失焦时，最多一次经 Cookie／唯一空 composer 校验的 refocus，JS 也做前置原子 unique-empty 检查；不重发生成。相关源码通过 63 项 browser 检查和 225 项 typed-429 相关检查，但这些不替代真实 High 结果。

首轮真实无 hook 四项已返回失败，分别保留 attempt 1，不与旧 67b 三份文件互相覆盖。

| 2663 首轮 case | 实际结果 | 边界 |
| --- | --- | --- |
| Neon High 音乐 attempt 1 | 60.15 秒 `TIMED_OUT`，`native_media_media_tool_not_submitted` | 已点击 media_tool，但 `tool_entry` 确认超时；不是定位菜单超时，明确 not_dispatched |
| Hydrogen 视频 attempt 1 | 44.12 秒 `UPSTREAM_REJECTED` | 已派发，精确完成回复为通用技术失败；清理 completed，无视频 |
| Neon 视频 attempt 1 | 46.08 秒 `UPSTREAM_REJECTED` | 已派发，同类精确技术失败；清理 completed，无视频 |
| Hydrogen 音乐 attempt 1 | 60.09 秒 `TIMED_OUT`，`native_media_media_tool_not_submitted` | media_tool 点击后的入口确认超时，明确 not_dispatched |

[2663 分轮 schema](live-media-diagnosis-installed-2663-2026-11-22.json)保留以上首轮零文件通过，以及后续各次独立结果。不能因构建门禁通过、NoSend 通过或 67b 曾成功，就把首轮写绿。

Neon High attempt 2 只因前次明确未派发才可另行启动。它曾处于 created／缺 CID／heartbeat 新鲜状态，不能据此断言没点击 Send，因此没有重发或取消这一个句柄。其原句柄后续在 318.12 秒取得精确已完成的完整乐谱，返回 `ARTIFACT_NOT_RETURNED`／`completed_music_notation_without_artifact_observed`，清理 completed，不计音乐成功。

随后同提示、同 short／instrumental 选项仅明确改用 **Low** 的独立无 hook 安装版请求，在 248.17 秒成功保存三文件并完成清理：MP3 65.149333 秒、44.1 kHz 双声道；MP4 65.167333 秒、H.264 1024×1024＋AAC；512×512 JPEG。独立大小／SHA-256 和全片音视频解码核对通过，MP3 非静音（平均 −14.3 dB、峰值 0 dB）；没有人工试听声明。High 两轮和 Low 的请求等级已按精确原句柄只读核对 operation 记录，不只依靠 runner 参数。

这一成功配置为 `model="neon"`、`thinking_level="low"`、`music_duration="short"`、`music_vocals="instrumental"`，调用示例见[模型指南](models.md#音乐生成)。`standard` 继续保留原生网页默认，不能说它等于 Low；不改变服务器默认，也不自动从 High 降档重发。本次 High 乐谱结果与更早提交前菜单状态失效／确认失败，均不能证明 High 全局不支持音乐。

其余三个独立 Low 请求没有通过：Hydrogen 视频在 68.11 秒因思考等级核验失败而明确未派发；Neon 视频在 74.08 秒因模型选择超时而明确未派发。Hydrogen 音乐在原句柄观察 802.19 秒仍为 pending；随后独立精确读回确认已完成、无媒体且有新的中文 NoTool 回复。该 pending 是原快照，后续 b562 原句柄恢复另见下节，不改写为该轮当时已收口。Neon Low 三文件成功不被这些失败覆盖。

一次精简 NoSend 使用 installed2663 代码、私有 logging hook 和 before-Send abort，没有 workspace 源码替换；28.275 秒所有 pre-Send guards 通过，首次 focus 为 true、未 refocus，本地诊断在 guard 后结束且没有截图。这仅证明入口 guard，不是无 hook 文件验收。

另有不同提示的独立吉他音乐 control 使用 installed2663 加 observation-only `sitecustomize`，允许真实提交以观察后续确认／登录／modal／URI 形状；它不是原 High 句柄重试，也不是 hook-free 验收。当前不把未观察到的 modal 或 URI 变化当事实。源码保持2663，不因没有失败 DOM 就盲加其他菜单重试。

<!-- MEDIA_NATIVE_UNHOOKED_DELIVERY_20261122: Final current60fe gates/entrypoints/profiles and 13 saved/cached reads passed. New H video rejected after submission; earlier Neon music/video files retain their original builds. Preserve source/67b/93/ca64/2663 round identities; no new release/version. -->
<!-- MEDIA_NATIVE_UNHOOKED_CASES_20261122: Preserve four failed 2663 attempt1 cases and High attempt2 notation-only failure. Explicit Neon Low music has verified files; Low H/N videos failed before dispatch, Low H music resolved by same-handle b562 readback without new generation. No automatic downgrade or replay. -->

## 后续修正与 b562 安装／真实结果

旧 67b 的七个本地观察器仍轮询已由同账号 canonical 清理完成的资源：操作记录处于 pending、没有清理保护、没有产物元数据。此次只在正向清理证据与原 owner 匹配后 CAS 更新清理状态／释放租约，并按精确 PID／argv／SID 退出这七个本地进程，远端生成、删除和 cancel 请求均为零。原操作身份与历史结果保留；对已清理且没有产物的未决操作标为 unavailable，不编造成功或新产物。新的共享 `media_cleanup` 在 dispatch／worker／recover 前核对该正证及原快照 CAS，保护中、retain、其他账号及 Omni API 操作均不适用。

独立无 Send 对照证实一种高等级菜单 stale 状态：22.007 秒出现精确 stale 证据，一次 trusted trigger reset 后 22.071 秒确认关闭，23.555 秒所有 pre-Send guards 通过；Escape 对照未解除该状态，不新增 Escape 路径。正式修正仅允许在已证明的 stale 状态做一次 reset，两次正常开窗加一次 reset 至多三次点击；未知形状、modal 或仍可见 panel 不触发 reset。保留既有 OpenTools／60 秒入口等待，只有明确 `tool_unselected` 才重选工具，选中 chip 的控件延迟、未知／dialog／disabled chip 不触发重选，防止把已选工具误取消。73 项 browser 检查覆盖该合同，不替代真实生成验收。

另补 Hydrogen Low 音乐新中文 NoTool 的有限前缀，仍要求精确原回复、完成状态和严格无媒体证据。

上述修正已冻结并安装为 `b5625030a8bf48bf`，wheel SHA-256 为 `b5625030a8bf48bfe4e4dff98015f6afaef6809544f98be0f40b8ca758b310fa`，共享 `current` 已切换。144 个源码／data 文件逐字节一致、无额外文件；47 个 distributions 中 46 个固定依赖零差异。Ruff、Mypy（153 文件）、4,988 项离线（91.63 秒）、2,783 项合同（52.93 秒）检查通过，产品版本仍为 `0.20.0`。

b562 fresh stdio 首次仅以 `get_result` 回读 11 个 case：67b 三个文件 case、2663 Neon Low，以及七个已清理无产物操作。四个文件 case 为 completed，七个旧记录为 unavailable，全部身份保留，create／generate 工具调用为零。

Hydrogen 中文 NoTool 音乐的 b562 首次无 hook 原句柄恢复在 124.07 秒仍为 partial／`READBACK_UNVERIFIED`，保留该历史。随后同安装版加 **observation-only** trace，在同句柄 14.07 秒取得 `music_cards=0`、strict empty proof success，分类为 `ARTIFACT_NOT_RETURNED`／`model_media_refusal_observed`，清理 completed；没有行为 hook 或重新生成，但此诊断本身不冒称无 hook。最后独立无 hook cached-result 读回已确认该失败，累计 13 个 case：五个完成文件 case、七个 unavailable、一个已确认拒绝，身份均保留，create／generate 工具调用仍为零。读到旧文件不算 b562 新生成。

新的 b562 无 hook 视频独立按参数记录，不覆盖前轮：

| case | 结果 | 证据 |
| --- | --- | --- |
| Hydrogen Low | 84.15 秒失败 | 思考等级核验失败，明确 not_dispatched |
| Neon Low | 126.03 秒失败 | 精确 completed 技术错误卡，`UPSTREAM_REJECTED`，清理 completed |
| Neon standard（网页默认） | 157.10 秒通过 | 两文件保存并清理 completed；H.264 1280×720@24fps，视频流10秒／容器10.026667秒，AAC48kHz双声道 |
| Hydrogen standard（网页默认） | 235.10 秒失败 | 原句柄精确归属已核实，`TIMED_OUT`／`native_media_media_tool_not_submitted`，明确 not_dispatched |

Neon standard 视频的独立大小／SHA-256、全片 ffmpeg 解码及 512×288 JPEG 检查通过。第4／8秒接触图可见蓝纸立方体从斜侧到近正面、面向和折痕改变，符合转动；这是抽帧视觉证据。Hydrogen standard 原句柄归属已核实，计为失败。`standard` 保持网页默认，不因同名参数推断等于 Low，也不将一次拒绝外推为模型不支持。

## 60fe 最终交付：同源安装与实际限制

发送前恢复已安装：仅首次 native 返回结构化 `state="unverified"`＋`TIMED_OUT`、`submitted is False`、`chat_id is None`、无媒体、stage 不是 send／observe、`on_chat` 从未调用、无已观察 CID、没有 429、原 owner 恒定，且扣除原 readback reserve＋2 秒后仍有至少60秒浏览器预算，才允许一次原参数控件重开。第二次只使用原 deadline 剩余量；任何已尝试 Send／未知提交、429、上游拒绝或 `CAPABILITY_UNAVAILABLE` 均不触发，不切 SDK、不改变参数、不重置 timeout，第二次失败仍写原操作。274 项针对性检查包含真正 ffmpeg 第二次 ready 视频／音乐保存与完整负例，不能替代真实账号生成验收。

当前 runtime 为 `60fe2a5025546098`，wheel SHA-256 `60fe2a5025546098e0e4735ef9d3be1273b5107c2d9fcf206961072613859cc2`，共享 `current` 已切换，产品版本仍为 `0.20.0`。144 个源码／data 文件精确一致、无额外文件，47 distributions／46 固定依赖零差异；Ruff、Mypy（153 文件）、5,009 项离线（89.75 秒）与2,804项合同（52.76秒）通过。六入口及 profile smoke 全部通过。

最终 fresh stdio 仅调用结果读取，13 个 case 中五个完成文件 case、七个已清理 unavailable 和一个已确认 Hydrogen 音乐拒绝均保持原身份，create／generate 调用为零。五个文件 case 包括67b Hydrogen／Neon 视频和 Hydrogen 音乐、2663 Neon Low 音乐及 b562 Neon standard 视频；它们的文件仍可由最终60fe恢复读取，**不改写为60fe新生成**。

60fe 新无 hook Hydrogen standard 视频在44.09秒观测到实际 `gemini-4-hydrogen` 和完整原回复身份，精确 completed 读回为网页技术失败：`UPSTREAM_REJECTED`／`upstream_media_error_card_observed`，清理 completed，没有文件。这是明确提交后的上游拒绝，不能重试生成，也不算 Hydrogen 新视频成功。另一个 b562 Hydrogen NoSend 对照在25.161秒通过全部 pre-Send guards、没有发送且未复现入口故障；它只证明该次入口检查，不能替代这次真实失败。

b562 的四次视频、旧文件及13项读回记录继续保留。已完成的独立 fresh 检查确认本轮20／20个已知对话缺席、零次 mutation；旧七个观察器只依据 canonical 正证与本地 CAS 处理，不能加算为 fresh 27个，也不扩大到未知目标。最终相关自有 worker 为零。

随后新增的最终 fresh snapshot 为21／21精确缺席：source诊断9／9、67b批次4／4、后续已知目标8／8，mutation为零，不包含旧global七项。私有账本 active-owned-workers为零，源码／安装版再次核对144文件精确一致、无extras，47／46依赖无漂移。这个21项快照独立追加，不改写上面的20项旧快照。最终四个主要交付文件的SHA均匹配：Hydrogen视频／音乐生成于67b、Neon音乐生成于2663 Low（65.149333秒）、Neon视频生成于b562 standard（10.026667秒）；60fe只回读这些文件，不回填为新生成。

## 脱敏证据与限制

### 早期0.20.6候选与Hydrogen对照（原快照）

本节保留当时尚待安装／实测的状态；随后候选6eab及最终64e8已取得独立无hook文件，最终门禁、旧英文仍失败和App待Restart状态见[0.20.6最终报告](live-media-0.20.6-2026-11-22.md)。

本地`0.20.6`新增严格 generic-failure gate：只有精确原回复、已完成状态和严格无媒体证明成立，已知通用错误才能收口为`UPSTREAM_REJECTED`并释放保护；未知／pending／含URI数据、身份／账号／租约变化或429不能被错误文字覆盖，已识别媒体优先。该源码通过Ruff、Mypy（153文件）、5,063项离线（81.09秒）、2,858项合同（47.48秒）及9项CI合同检查；新包已构建并开始安装，最终hash、无hook产物和App重载结果另待实际证据。

App以原`0.20.0`的60fe执行的两条英文Hydrogen视频（standard／Low）均明确上游拒绝；其中保留的standard原对话独立strict completed-empty成功，没有媒体且为真实通用错误。另一次**installed60fe＋observation-only hook**的中文猫视频对照，standard、10秒、16:9，在200.13秒保存JPEG与MP4：容器10.026667秒、1280×720、H.264＋AAC。该次trace观察到工具入口约83秒超时、约92秒重新开窗、约104秒Send，实际触发一次发送前恢复后成功；这不是workspace替换，也不是新`0.20.6`无hook生成或全模型通过。不同提示、参数和观察方式不混为同一个对照结论。

[源码结构报告](live-media-diagnosis-2026-11-22.json)只纳入八条原 source 结果；[67b 安装版报告](live-media-diagnosis-installed-67b-2026-11-22.json)列四项，[2663 分轮报告](live-media-diagnosis-installed-2663-2026-11-22.json)列八项，[b562 新视频报告](live-media-diagnosis-installed-b562-2026-11-22.json)列四项，[60fe 最后新视频报告](live-media-diagnosis-installed-60fe-2026-11-22.json)列一项明确上游拒绝；[safe evidence](live-media-diagnosis-2026-11-22-evidence.json)分别保留来源、NoSend／preflight、限流、失败与原句柄读回事件。不能合算成最新安装包支持矩阵。RPC 未采集值使用明确 sentinel，未导出真实请求体、响应、headers、Cookie 值、远端标识或私有路径。

App已观察到60fe实际调用，但重载至新`0.20.6`尚未核实；它不否定已核验的原版本文件，也不否定用户已完成Google验证或bridge后preflight成功。不同提示／参数仍有上游拒绝、入口失败或无音频结果，不能由安装或离线通过推断全组合可靠成功，也不应要求用户重复登录／验证来掩盖这些结果。
