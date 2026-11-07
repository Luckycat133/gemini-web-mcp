# 架构与共享契约

## 两套产品，一份运行时

目标默认入口：生成套件拟新增 `gemini-mcp-generate` / `src/surfaces/generate.py`，管理套件沿用 `gemini-mcp-account`。这些新名字在实现前不是可运行命令。原有 assist/create、primary/compact 全部保留；新入口通过共享注册函数和服务组合，不复制业务逻辑。管理提供明确 read/admin 策略，生成只暴露本次任务需要的账号资源定位，不默认扫描历史。

新增运行 Skill 拟为 `gemini-generate`；管理继续 `gemini-account`。旧 `gemini-assist`、`gemini-create`、`gemini-web-mcp` 保留兼容指导。不要为了维持某个工具数而塞入无类型万能 `action: str`，也不要一个网页按钮建一个工具。

## Owner

- `src/domain/`：请求联合类型、结果、资源引用、覆盖度和确认契约。
- `src/services/`：生成、读取、变更、任务、批量、备份、能力发现；不 import MCP 类型。
- `src/adapters/mcp_sdk.py`：唯一 MCP SDK/协议 import 边界。其余 adapters 负责旧参数兼容、输出 schema、内容块和权限入口。
- `src/infrastructure/{rpc_contracts,rpc_parsers,web_request_contracts,import_contracts,canvas_contracts}.py`：有来源的协议构造与纯解析。不得散落新 RPC 字符串或数组索引。
- shared client/cookie/session/cleanup managers 保留生命周期所有权。`src/tools/manage.py` 只注册兼容工具，不供服务反向依赖。

## 新增领域契约（设计目标，不是当前 API）

| 类型 | 必须包含/约束 |
| --- | --- |
| Capability | stable capability ID、账号内部作用域、mode/model 组合、输入/输出类型、参数约束、availability（available/unavailable/unknown）、协议版本、证据与观测时间；未观测额度不能填 0 |
| ResourceRef | opaque handle、kind、provider namespace、服务端绑定 account scope、provider ID/version、来源关系；调用者提供的 scope 不授予权限 |
| InputRef | 有序 input ID、local/remote/provider/artifact 类型、媒体类型和用户角色说明；角色说明不伪装成原生控制字段 |
| ConversationRef | 稳定本地句柄、上游续接 metadata、账号与保留策略、最后确认 turn/version、并发版本；禁止存正文来冒充上游续接 |
| Artifact | 原始与派生产物 ID、parent/source refs、mime、size、hash、readiness、verified decoder、origin（provider_native/local_conversion）、location；文件路径仅本机有效 |
| Coverage | sources_requested/scanned、pages、continuation、truncated、missing/rejected、snapshot/observed_at、complete；不声称无上游快照保证的强一致性 |
| MutationPlan | frozen target IDs + versions、动作/参数摘要、影响、expires_at、approval binding、逐项执行状态；版本冲突不扩大选择 |
| BackupManifest | 格式版本、资源/关系、消息顺序和分支、附件 hash、缺失原因、覆盖度、cursor、导出时间；备份不等于原样恢复 |

采用现有 `DomainResult` envelope；新状态先做兼容映射，别让旧客户端把未知状态当 success。账号作用域只在可信服务端派生；日志和公开报告不暴露身份哈希、凭据或真实 resource ID。

## 操作状态与提交语义

复用 OperationService：accepted → running/queued → completed；保留 partial/timed_out/failed/cancel_requested/expired 的区别。需要用户输入的上游任务不得自动判失败或擅自确认外部动作。

持久化先后顺序：reserve handle → 获取 lease → 记录提交意图 → 唯一一次 start → 尽早记录 provider locator → 记录产物定位信息 → 独立验证 → 记录终态 → 按保留策略清理。数据库 commit 与上游请求之间没有分布式事务；无 locator 的中断必须报告 submission_unknown，不得宣称 exactly-once。

幂等按账号、操作类型和 key 隔离。同 key 不同请求应冲突而非返回错误任务；可使用本地私钥 HMAC 的规范化请求指纹，状态库只存指纹，密钥放私有凭据存储。不要保存明文 prompt，也不要用未加盐 prompt hash 泄漏可猜内容。数据库迁移兼容旧无指纹记录，不推测它们的请求内容。

恢复仅查询已有 provider work；read/status 与会导致下载/清理的 collect/recover 在新入口区分。旧 status 的既有副作用保留说明与保守 annotations，不默默改变兼容行为。

## 保留、会话与账号切换

每个资源记录用途与保留关系。一次性工具调用可以采用既有清理策略；创作会话、Notebook 成员、用户明确保留的作品、未交付的长任务默认 hold。关系交接应先取得 hold，再移动/关联并读回，最后释放旧 hold。删除整个来源时检查依赖，明确列出影响。

普通会话复用 provider metadata 续接。每次 send 加跨进程锁/乐观版本检查，避免两客户端写错分支。Cookie 刷新和切账号不能混同：仅有已验证的同账号 lineage 才继承句柄；无法验证时 fail closed，不跨账号接管清理。

## 存储与输入安全

恢复 SQLite 仍只存 metadata/locators，迁移需旧版本 fixture、并发初始化、崩溃与文件权限测试。全文索引、备份、语义摘要分别是 opt-in 私有内容存储，必须有大小预算、保留/清除、账号隔离和索引删除传播；不能默认把账号内容发送给另一个 LLM 做索引。

下载校验 scheme、host、重定向和私网地址；不把 Google 凭据转发到任意 URL。本地输入和输出拒绝路径穿越、符号链接逃逸和非预期覆盖；限文件大小、解压后大小/数量/层数、解码资源和下载时间。导入 Skill 的脚本不运行，Canvas app 不在开发机自动执行。远端文件返回 MCP resource/link，不能让远程客户端误用服务器绝对路径。

浏览器是按能力启用的可选 adapter，不是自动 fallback：只在确认未提交或明确恢复路径时操作，不能接口超时后再点击生成。用户自己完成登录、验证码、账号同意和头像录制。共享本机浏览器不能变成公开无鉴权 HTTP 服务。
