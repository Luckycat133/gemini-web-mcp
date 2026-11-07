# 基线与缺口（2026-11-07）

## 读取边界

审查基线：`d5dbb406357ef21df3a612aa51bc9888efe07879`，tree `5d8050ec40abb33bf5104e3778e664025198d340`，包版本 `0.2.2`。v0.2.2 的 release 已存在，旧 README 的“发布准备”文字不再代表发布状态。此计划基于源码、测试定义、仓库记录和公开官方资料；编写计划期间没有登录 Google、重跑全仓运行时测试或进行在线生成/删除。

未来执行者必须重新核对 HEAD。下表的“已有”表示代码或仓库记录，不是 2026-11-07 的新在线验收。`docs/live-ui-coverage.md` 的有界账号记录不能被改写成全账号全功能成功。

## 已有 owner，优先复用

| 领域 | 已读源码/契约 | 基线与需要完成的部分 |
| --- | --- | --- |
| 服务入口 | `src/server.py`, `src/skill_server.py`, `src/surfaces/{assist,create,account}.py` | 五种既有入口；新的两套产品是整合呈现，不删除旧接口 |
| 模型与模式 | `src/constants.py`, `src/infrastructure/web_request_contracts.py`, `docs/models.md` | 运行时模型目录、思考等级、原生 selector 已有；完整能力约束和生成侧发现仍需统一 |
| 创作 | `src/services/{creation,media_generation,artifacts}.py` | 真实保存和验证已有；单 `image_path`，专用 image/edit 没有和 music/video 相同的持久 operation 启动 |
| 长任务 | `src/services/operations.py`, `src/infrastructure/state_store.py` | 已有凭据作用域、幂等、租约、定位信息恢复；扩展类型，不另造调度系统 |
| 会话 | `src/session_manager.py`, `src/services/chat.py` | 普通本地 session 在内存；持久续接与跨进程防并发写待补 |
| Research | `src/services/research.py` | 计划/启动/恢复代码已有；仓库记载的报告完成实测仍未通过，先修实际报告交付 |
| 历史 | `src/services/history.py`, `history_mutations.py` | 新鲜分页和可选正文扫描已有；现有轮数/字符上限不能充当全量读取。复核 `read_chat_result` 的 `None`、CID 匹配和截断语义 |
| 管理 | `src/services/{account_facade,notebooks,native_skills,library,sharing,scheduled,gems,canvas,canvas_edits}.py` | 多种 CRUD 已有；不要当成全新功能重建。重点补全资源关联、读写细节、导出和批量流程 |
| 读权限 | `src/server.py`, `src/tools/__init__.py` | 分组存在；`gemini_reset` 注册不随只读分组裁剪，且可能删远端来源，需要回归测试与策略修复 |
| 安装 | `src/onboarding.py`, `src/services/doctor.py`, `pyproject.toml` | auth-free preflight 已有；缺完整交互 setup，thinking CLI 仍保留较窄 choices |
| 验证 | `docs/agent-verification.md`, `tests/test_development_skill.py`, `tests/test_creation_service.py` | 有离线、协议、包和 Skill 门禁；测试总数不是线上成功率 |

## 不要继承的错误假设

- 传 `model=pro` 不等于首次图片请求已经走 Pro redo。
- 文本响应包含 URL 不等于已下载或已校验文件。
- Gemini 网页 Notebook、NotebookLM/新 Gemini Notebook 连接引用不能仅凭名称互换 ID；以当前账号协议与命名空间判定。
- 当前公开资料出现 Gems 向 Skills 的迁移，必须保留旧资源的读回/备份/兼容映射；不能假定所有账号同时切换。
- 官方 Deep Think 页面已经提示替代迁移；不要为了“全模型”强行调用退役 enum。
- Canvas plain-text 替换会丢格式，不是格式保真局部编辑，也不自动创建历史版本。
- Library 某类资源不支持单项删除时，不能偷偷删除其整条来源会话。
- 完成的媒体重启可取回、受控中断恢复和任意硬崩溃恢复是三个不同验收条件。

## 基线建档输出

P00 产出当前 checkout 的工具目录/schema 摘要、依赖版本、工作树状态、能力覆盖清单、权限/账号测试范围和变更差异。仅提交脱敏记录。对每个能力分别记录：源码是否实现、协议是否有证据、当前账号是否可用、最近在线验收时间；不要压成一个 supported 布尔值。
