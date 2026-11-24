# 0.20.7 精确拒绝收口验收（2026-11-22）

本地`0.20.7`仅修一个新实证缺口：Default Hydrogen视频原句柄的中文“程序代码局限”类拒绝，已独立证明精确回复完成、strict completed-empty成功且SDK媒体为空，但`0.20.6`源码的已知拒绝识别仍为false，导致继续pending。补丁采用有限精确识别，保留全部原身份、账号、租约、完成状态和无媒体证据要求；不扩大成关键字拒绝，不改提示或重新生成。

最终wheel SHA-256为`bc4c05c65a7bb17c8fec792ab1e355320901e2e03ff58155cd0d99df8d8c0ad0`，安装／共享`current`为`bc4c05c65a7bb17c`。144源码／data文件与wheel／安装精确一致，47distributions／46固定依赖零差异。Ruff、Mypy（153文件）、5,109项离线（233.25秒）、2,904项合同（152.90秒）、modern／legacy installer、profile及clean-wheel实际六入口／版本`0.20.7`检查全部通过。

## 原句柄拒绝处理验收

旧observer退租后，另一个旧客户端自动取得该句柄lease。再次独立核实同一原CID／RID／candidate、completed-empty和无媒体后，使用原子token CAS交给新`0.20.7`的`media_resume`，全过程保留原身份和清理保护。交接阶段没有prompt、generate、remote cancel或远端删除；不因worker／客户端切换重新生成。

新版本真实MCP `gemini_get_operation_result`以**原句柄**收口为`failed`／`UPSTREAM_REJECTED`／`upstream_media_error_card_observed`，清理completed、worker inactive、零产物。**这是拒绝处理验收通过，不是视频生成成功**。已完成无产物回复不再永久pending，未知或缺少严格证据的状态仍继续保护。

新`0.20.7` fresh MCP只读取原[0.20.6两个cohort](live-media-0.20.6-2026-11-22.md)的四个句柄：三completed、一原已知failed，六个媒体文件大小／哈希全部匹配，原身份全部保留，create／generate为零。原build、版本、hash与成功／失败不重标为`0.20.7`生成；只读恢复不算新生成。

## 独立清理复核

两个本轮App实际调用case均来自旧60fe，最终均failed、cleanup completed和inactive，第二个由新补丁原句柄恢复收口；此前Default数学probe也已清理。新`0.20.7`独立只读复核这三个已知目标3／3精确缺席，mutation／generation为零。此次三个实际observer均退出，两个操作记录已释放保护、无worker，范围内无未收口自有工作；不扩大为账号全域清理。

## App连接的实际边界

用户已实际Restart两次，新的64e8 Appchild也已启动（包括固定不可变command）。但本轮真实工具调用仍进入旧60fe worker；源码及当前观察不足以证明原因，不能用“同identity复用”作已确认解释，也不能据新child启动声称完成加载。

全局Gemini的原Default／dsh／开关已恢复，command现固定到新`0.20.7`不可变运行时，其他配置语义未变。独立新stdio此前完成版本`0.20.6`／六工具协议核验，Default Hydrogen数学调用答43并完成清理，附随FollowUp widget不判为答错；新`0.20.7`拒绝处理亦已验证。这些stdio证据不替代App真实工具路由。App首个英文case失败并清理，第二个中文拒绝已按原句柄由补丁收口，均不写成视频成功。

当前同一App轮次的实际调用仍落旧60fe，64e8新child启动不证明已选中新连接；是否在普通新turn解决也尚未验证。后续以一次真实普通调用核对，避免在当前轮次反复Restart或媒体提交。原因未知，不能把配置、child启动、stdio通过当成App路由已完成。

[schema报告](live-media-0.20.7-2026-11-22.json)分别列原媒体拒绝与既有结果读回两个验证工作流；前者保留`unavailable`表示实际无视频产物，并非补丁验收失败。[脱敏sidecar](live-media-0.20.7-2026-11-22-evidence.json)明确记录拒绝处理断言通过、原回复身份、四句柄／六文件和3项缺席范围。未采集RPC／HTTP字段用sentinel／null／零计数，不解释为没有请求。

[补丁范围与依据](changelog.md#0207---2026-11-22) · [当前来源／安装说明](source-update-integration.md)
