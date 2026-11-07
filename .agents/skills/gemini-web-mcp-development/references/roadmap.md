# 执行路线与工作包

权威任务依赖和验收映射在 [plan.json](../assets/plan.json)。所有包初始 planned；已有基础由 baseline 记录，不把旧实现算成本计划验收通过。工作包可拆成多个小 PR，保持 ID 与 case 映射。

## 顺序

| 批次 | 工作包 | 可交付结果 |
| --- | --- | --- |
| 0 | P00 → P01 | 刷新覆盖、修真实边界问题，建立不掩盖未知/截断的基线 |
| 1 | P02、P03、P04；P05 可在基础策略明确后并行 | 能力/素材/持久任务与交互安装的共同底座 |
| 2A | G01 → G02、G03、G04、G05、G06、G07、G08 | 生成线所有模型及原生模式逐项接通 |
| 2B | M01 → M02、M03、M04、M05、M06、M07、M08 | 管理线全资源发现、批量整理、导出和管理 |
| 3 | X01、Q01、D01、R01 | 两套互通、真实 agent 验收、发布验证与交付 |

不用等待所有在线条件才能继续离线工作。依赖包达到 implemented 且其接口/离线测试可审查时可启动下游；accepted 必须满足全部门禁。最先串通两条最小垂直流程：一次多模态生成→产物→继续编辑；跨页查找→完整读→计划→批量整理→导出。之后按完整矩阵补齐，不把垂直样例当全量支持。

## 共同工作包

### P00 — 刷新基线与能力覆盖

输入：当前 git 状态、代码与测试、官方资料。检查发布状态、两套目标和现有兼容面；生成实际工具/schema/依赖摘要。列每项功能、账号可用性、协议证据、最近验收，明确新能力发现流程。建立 source ID 到工作包映射。输出脱敏 baseline/coverage 差异和待实现清单；禁止删除已发现但难实现的条目。

### P01 — 可信读取与权限边界

复核 `read_chat_result`、分页、导出：None、错误 CID、畸形返回、截断不能成为 observed complete。为只读 profile 的 reset/cleanup 副作用写回归，服务层实施 policy。保持旧工具名称与保守 annotations；必要时新增纯本地 reset，并说明旧 destructive reset 的权限拒绝。统一 thinking choices 来源，修 release 文档过时文字。不得一次扩大成全仓格式化。

### P02 — 能力目录

扩展 manifest/account/runtime model owners。按账号读取模型、mode、输入限制和原生参数；static manifest 与 live availability 分开。缓存有 TTL/账号隔离，未知字段/退役模型/限额降级产生明确结果。生成套件不必启用私密历史管理即可查能力。alias 只能映射经观察的能力，不硬编码新模型 hex ID。

### P03 — 素材、产物与引用

复用 inputs/artifacts/understanding/import contracts，统一有序多输入、native reference 和生成物引用，提供客户端可获取的交付方式。识别真实 provider 文件格式，保存 provenance；不静默跨 Google 服务互换 ID。持久定位与安全下载/解压、权限/空间不足、远程客户端路径差异都纳入测试。

### P04 — 持久任务与续接

扩展现有 OperationService、state_store、SessionService：图片纳入 durable handles，普通会话 metadata 续接，幂等 payload 冲突、跨进程租约、账号 lineage、迁移/过期、提交未知窗口与取消证据。同步 fast path 有等待预算，超时返回原 handle。新只读 status 不触发清理；collect 明示副作用。保留旧兼容行为。

### P05 — 两套交互安装与诊断

在人用 setup 中选择客户端、生成/管理/read-admin、浏览器 profile、产物目录和授权范围；配置 preview、确认、原子写、备份及 rollback。无 TTY 提供明确非交互参数，不挂住 MCP stdin。默认 preflight 不读 Cookie、不网络生成；MCP stdout 只有协议，日志 stderr。复用 doctor/onboarding，不做独立聊天 UI。权限不足/多账号/FFmpeg/Pillow 缺失给可操作诊断。

## 生成与管理细节

G01–G08 见 [generation.md](generation.md)，M01–M08 见 [management.md](management.md)。每个包都关联明确验收 case，不能只完成清单里的第一个例子。

## 集成与交付

### X01 — 跨套件交接与保留策略

同账号下支持查旧素材→继续生成→整理新产物、研究→报告→Notebook、批量导出期间保留来源。显式资源授权与 hold 转移可崩溃恢复。来源删除与资源 move 并发时不误删。跨账号的共享 handle 拒绝，不做自动复制私密内容。

### Q01 — 全覆盖 agent 与兼容验收

建立每个公开 action 和能力变体的正/负 case、调用预算、独立 oracle；模拟变更、响应丢失与进程 kill。用实际 MCP 客户端和有授权测试账号跑矩阵，记录首轮成功、恢复成功、重复提交、误删、工具选择和下游产物可用性。新发现模型/操作加入矩阵而非忽略。零误删、零未授权访问、零隐式重新生成是硬门；随机生成质量另设人工 rubric，不伪造 100% 内容质量。

### D01 — 分发与平台支持

更新新入口、运行 Skill、package data、版本与 schema fixtures、客户端样例。现有 macOS/Linux 路径先保住；Windows 为单独能力子项，只有安全状态文件/凭据权限实现与真实 OS 测试后才宣称支持。无条件宣称跨平台不是默认目标。清洁 wheel/uvx、独立 Skill 安装和实际客户端验收都必须保留。

### R01 — 最终验收与发布候选

完整覆盖账本逐项对账，运行 completion checker，人工抽核证据。不以 blocked/skipped 充数。按真实改动评估下一版本；本计划不预先发布 0.3.0，不改已发布 tag。交付 review PR、升级/回滚说明、按账号能力的限制和完成报告；合并/发版遵守用户与仓库门禁。

## 并行所有权与变更控制

先稳定 P02/P03/P04 的 contract；generation 与 management 可各自负责独立服务。domain、state_store、RPC registry、package metadata 由一个整合 owner 串行合并。小任务不强行开多 agent。跨文件协作先约定接口，禁止两人同时改同一注册/迁移文件而覆盖对方。

一次 PR 尽量完成一个可验收结果；需要破坏性兼容改动先给迁移方案。上游新变化要更新 sources/coverage 和受影响包，仍保留原失败记录。只有用户决定改变产品范围时才缩减目标。
