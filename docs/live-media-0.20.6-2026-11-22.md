# 0.20.6 媒体验收（2026-11-22）

本页是`0.20.6`原build验收快照；后续唯一新增的中文拒绝识别补丁和更新的App真实路由状态见[0.20.7报告](live-media-0.20.7-2026-11-22.md)。下述文件、版本、hash和逐次结果保持原证据。

本页追加本地`0.20.6`证据，不覆盖[原0.20.0逐包记录](live-media-diagnosis-2026-11-22.md)。源码与安装已统一到最终64e8，完整门禁、无hook中文橘猫与后续同原英文提示的蓝立方体视频、独立文件QA和原句柄恢复通过；最早英文请求仍保留明确上游拒绝。候选与每次提交按实际身份分别记录，成功不覆盖旧失败，Google内部原因仍未知。产品版本为`0.20.6`，没有公开发布。

## 候选6eab真实文件

Default账号通过`gemini_create_media`调用发现的Hydrogen，standard、10秒、16:9，中文橘猫场景。standard保留网页默认，不等同Low。实际调用使用安装版，无workspace替换或诊断hook；190.14秒完成，清理completed。

| 文件 | 独立核验 |
| --- | --- |
| MP4 | 6,187,767字节；10.026667秒，1280×720 H.264＋AAC 48kHz双声道，全片ffmpeg解码无错 |
| JPEG | 62,239字节，512×288，可解码 |

两个文件大小和SHA-256匹配。第4／8秒画面已独立查看，橘猫实际行走；这是抽帧视觉证据，不声称逐帧检查。生成期间原本地worker消失，原因未明；stale后自动在**同句柄**恢复读回并完成，没有第二次create。不能将后续发现的finally缺口、OOM或其他代码故障指定为此次退出原因。

该候选wheel SHA-256为`6eab07ea41769a601ece0d61b39cf6e7bfbadc3054d94212b8e6fbdc027b839b`。144源码／data文件与wheel／安装逐字节一致、无额外文件；47distributions／46固定依赖零差异。Ruff、Mypy（153文件）、5,063离线／2,858合同、六入口、profile、modern／legacy安装协议及两份Skill文件集（7／2）通过。**这不是最终交付hash**，不把候选证据重标为随后新包生成。

## 实际网页模型选择证据

另外两条原60fe观察请求分别为英文失败、中文成功：实际官方ProcessSession出站public model-id的SHA-256都与同Default注册表精确解析Hydrogen的值相同，视频feature字段50为11。这证明网页确实派发了所选Hydrogen，而非仅改UI标签；该观察不属于6eab无hook抓包，也没有揭示隐藏的精确Veo／Omni生成器版本。英文失败原因仍未查明，不能仅凭中文成功作因果推断。原始model-id、请求、headers及账号标识不进入公开报告。

## 独立清理与最终64e8安装

候选的新已知对话1／1精确缺席，独立检查发送零mutation／generation；此前保留诊断目标App两条、wire两条也分别2／2确认缺席，不加进原21项快照。新run与wire范围内active-owned-workers为零，不声称账号全域清理。

worker finally修正要求：已失败heartbeat的异常或最终cleanup-proof读取异常都不能跳过原owner租约退役。12项针对性和119项相关检查通过，已进入同`0.20.6`最终新包；不据此倒推原worker消失原因。

最终wheel SHA-256为`64e8d37b3b548f21b16f5f6b4c55d7747fac6f17edc417cb9f33a5381feb4af4`，安装／共享`current`为`64e8d37b3b548f21`。144源码／data文件与wheel／安装精确一致、无extras，47distributions／46固定依赖零差异。Ruff、Mypy（153文件）、5,075项全离线（81.09秒）、2,870项合同（45.83秒）、清洁wheel六入口、profile与modern／legacy安装协议均通过。

最后的源码包检查发现`MANIFEST.in`遗漏文档JSON sidecar，已补入`docs/*.json`并重建sdist，逐字节核对随包源码、报告及Changelog；wheel和已安装运行时代码没有变化。补充27项打包／Skill／CI合同检查通过。

最终64e8无hook原英文蓝纸立方体native视频请求在60.12秒精确完成，上游返回`UPSTREAM_REJECTED`，没有文件，清理completed。该次有native路径、模型UI选择核验、记录中的Hydrogen及精确回复身份；服务strict completed-empty gate确认candidate完成且rich媒体为空，属于真实通用技术错误。视频模式11的独立抓包证据来自前述另一次60fe观察，不能回填为该次无hook抓包。Google没有给出更深内部原因，客户端的选择、证据及恢复bug修复不意味着这条提示的上游错误已解决。

只有该英文请求明确终态并清理后，harness才开始独立中文橘猫case；它不是工具自动改提示或重新发送同一操作。中文case在250.10秒完成并清理completed，保存两文件：

| 最终64e8文件 | 独立核验 |
| --- | --- |
| MP4 | 5,981,149字节；精确10.000秒、1280×720 H.264，**仅视频、没有音轨**，不能标为AAC音视频 |
| JPEG | 58,337字节，512×288 |

最终两文件大小／SHA-256匹配，MP4全片ffmpeg解码通过，第4／8秒抽帧已查看橘猫行走。该worker正常结束并退休token。候选6eab视频有AAC音轨，最终这条没有音轨，两者不能混写。最终包两个case为一成功、一明确上游拒绝，仍不保证所有提示生成成功。

先前fresh MCP stdio仅以结果工具读取三个原句柄：6eab中文成功、64e8英文失败、64e8中文成功；原回复身份全部保留，两视频／两JPEG大小和哈希独立核对，create／generate调用为零。该三句柄快照保留，没有为读取结果再次生成。

该时点64e8两个新已知目标独立2／2精确缺席，mutation／generation均为零，active-owned-workers为零。候选1／1、保留诊断App2／2与wire2／2及原21项各保留独立范围，不合算成一个更大的fresh清理数字。

上述三case和读回／2项缺席为已完成快照。后续本来拟做`same_prompt_neon_control`，但私有harness导入脚本触发顶层`asyncio.run(main())`，实际新增提交的是第三条Hydrogen请求，使用与最早失败完全相同的英文蓝立方体提示及standard、10秒／16:9参数，**没有发出Neon请求**。该本地脚本错误仍记录为`control_import_side_effect(H)`，不归为产品bug或Neon尝试。已精确停止所属driver以阻止其后续自动case，detached worker和原句柄保留；新fresh stdio只允许`get_result`，同句柄恢复取得真实视频并清理completed，没有remote cancel或第二次create。

该同英文提示case实际通过：MP4为2,862,020字节、10.026667秒、1280×720 H.264＋AAC48kHz双声道，JPEG为12,652字节、512×288，大小／SHA-256匹配，全片ffmpeg解码及第4／8秒蓝纸立方体转动抽帧QA通过。恢复调用耗时8.07秒；数据库created至最后terminal-update时间戳间隔为167.83秒，**二者都不冒称总生成walltime**。这是实际新增提交后原句柄恢复的成功，不是纯只读生成，也不是Neon。最早同英文请求60.12秒拒绝保持原结果；同模型同提示后续成功证明流程可工作，不能由此宣称worker修复根治了Google内部错误。不再扩大Neon对照，实际Neon调用仍为零。

最终新快照由64e8 fresh stdio读取四个原句柄，三completed／一failed，六个文件（视频／JPEG各三）大小和哈希全部验证，原身份全部保留，create／generate为零。最终包三个已知目标独立3／3精确缺席，mutation／generation为零；最终私有账本三个自有操作active worker为零、Neon请求为零。wire、候选6eab及App保留诊断范围也分别核实无active worker。此四句柄／3项目标快照追加在旧三句柄／2项快照之后，不把各历史范围加成全账号清理或新生成。

07:03:32 UTC及末次07:17:29 UTC的App工具调用，对synthetic未知句柄均正确返回`INVALID_ARGUMENT`／`operation_not_found`；末次进程核对仍是旧60fe，没有最终64e8 App子进程。这只证明旧工具响应正常，不证明新版本加载或账号鉴权。官方Restart动作已提出，用户尚未响应；没有受支持的自动App控制路径，**未验证App加载新hash或新版本真实调用**。共享入口更新、fresh stdio与候选生成都不替代该项验收。

[分轮schema报告](live-media-0.20.6-2026-11-22.json)共四项：候选6eab中文成功、最终64e8英文拒绝、中文成功及harness副作用提交的同英文成功；三成功／一失败，状态degraded保留真实失败。[脱敏sidecar](live-media-0.20.6-2026-11-22-evidence.json)另记build、文件QA、selector、恢复及清理来源。未采集RPC／HTTP字段使用`not_observed`／null／零计数，不能解释为没有请求。
