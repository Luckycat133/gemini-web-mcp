# 安装、兼容迁移与交付

## 不把计划当实现

本计划基于 0.2.2，新增入口、schema、索引和批量功能均需实际编码验收。此文档修订不修改包版本或公开 tag。完成相关工作包后再依真实兼容性选择版本，统一 pyproject、src metadata、runtime/development Skills 和发布资产。

## 开发分支和提交

从最新 main 检查工作区、远端更新和相关未合并 PR。每个小 PR 完成一个实际工作流，正文列 source evidence、改动 owner、case/gate、未验证项与兼容影响。保护用户未提交工作；不 force push，不静默重置数据库，不改历史 release。

只有用户明确要求或当前任务包含时才 push；合并、公开链接、账号写入及发版仍各自有权限边界。此 Skill 不授予它们。agent 已获得同范围授权时不每步重复询问；遇到阻塞记录证据并继续其他依赖已满足工作。

## 升级与迁移

- 旧五入口保持，新增生成入口与管理 read/admin 作为推荐安装；路由说明同时覆盖新旧。不要把两个推荐套件强行理解成删除兼容 adapter。
- 数据库 schema 版本迁移须事务化、可检测失败，并在旧 DB fixture 上验证；rollback 能否兼容新 schema 必须说明。恢复库内容边界不变。
- 旧 idempotency 记录没有 payload fingerprint 时不可猜请求一致；返回可解释冲突/不可验证，不偷偷重新生成。
- Session metadata、artifact refs 和 cleanup holds 关联认证 lineage，凭据刷新不等于账号变更，也不能仅凭 Cookie 哈希自动合并账号。
- 内容索引/备份单独 opt-in、单独存储和清除；不在升级中默认读取全部私密历史。
- Gems→Skills 依据账号实际迁移状态兼容，保留备份/映射证据；不重建同名条目冒充原生迁移。

## 交互 setup 与可复现安装

配置写前展示目标客户端、套件/权限、浏览器 profile 与变更 diff；确认后原子更新、备份原文件。日志走 stderr，不在 MCP stdin 做交互。提供无需 TTY 的明确参数及 dry-run；不能为了简化配置把 Cookie 写进仓库配置。

默认执行无账号 preflight。真实文本/媒体 smoke 单独 opt-in、有限请求且说明清理。检查 Pillow、ffmpeg/ffprobe、可写目录、空间、浏览器私有状态权限及超时诊断；禁止未经授权静默安装系统服务/变更 keychain/浏览器策略。

wheel/sdist 应包含用到的资源；运行 Skill 的引用在独立安装后也能找到。生成和管理两个主要安装选项都需实际客户端证明，旧安装方式继续验证。macOS/Linux 当前支持范围不应被未经测试的 Windows 样例扩大。

## 最终交付包

交付审查者一个可复现摘要，至少包括：

| 项目 | 必需证据 |
| --- | --- |
| 范围 | capability/action/selector 覆盖、当前账号限制、上游退役与新增项 |
| 实现 | 每个 P/G/M/X/Q/D/R 工作包的提交、共享 owner、兼容变化 |
| 测试 | offline/protocol/package/agent/live 分列，命令、版本、时间、结果 |
| 安全 | 权限、错误读取、重复提交、账号隔离、清理对账的零事故硬门 |
| 使用 | 两套真实安装、运行 Skill、典型完整任务、恢复和回滚说明 |
| 限制 | 未执行/失败/blocked 不计完成；是否支持恢复导入与哪些类型独立声明 |

用 plan.py check-progress --complete 校验私有进度/证据，再人工审查实际报告。`implemented` 可作为代码候选交付，不可改写为 live accepted。若用户范围在实施中扩展，更新 plan_revision、依赖与 cases，迁移进度时保留历史证据而非覆盖。

最终输出链接到审查 PR/commit、实际测试和限制。不得自动 merge/tag/release 以制造“全部完成”；也不要只交长文档而漏掉已授权的代码实现任务。本次 Skill 制作交付的是执行计划与工具，不是这些未来功能已经实现。
