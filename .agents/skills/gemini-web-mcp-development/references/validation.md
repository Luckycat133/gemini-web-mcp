# 分层验证与证据记录

当前 checkout 的 `AGENTS.md`、`docs/agent-verification.md` 与测试代码决定真实命令。先检查环境和版本，不能把下面清单的存在当成执行证据。此 Skill 修改本身只需相关文档/脚本检查；未来 runtime 改动再跑相应完整门禁。

## 可直接运行的 Skill 检查

```bash
SKILL_DIR=.agents/skills/gemini-web-mcp-development
python "$SKILL_DIR/scripts/plan.py" check
python "$SKILL_DIR/scripts/test_plan.py"
skills-ref validate "$SKILL_DIR"
git diff --check
```

`skills-ref` 需按 SK01 的官方工具安装；不可用时明确报告未运行，不用自制格式检查冒充官方结果。内置检查器验证任务 DAG、case/source 映射、链接和进度，不依赖 Gemini/MCP/网络。初始进度：

```bash
python "$SKILL_DIR/scripts/plan.py" init --progress "$PRIVATE_RUN_DIR/progress.json"
python "$SKILL_DIR/scripts/plan.py" next --progress "$PRIVATE_RUN_DIR/progress.json"
python "$SKILL_DIR/scripts/plan.py" check-progress \
  --progress "$PRIVATE_RUN_DIR/progress.json" --evidence-root "$PRIVATE_RUN_DIR"
```

`PRIVATE_RUN_DIR` 由执行者指定仓库外私有目录，不是固定机器路径。init 拒绝覆盖。每包记录 status、revision、implementation、blocker 和 evidence；不要把包含账号信息的进度推到公开仓库。

状态：planned→active→implemented→accepted。blocked 表示不能继续的具体依赖；缺少在线验收但代码和非在线门禁已完成时保持 implemented，并在 evidence 中记录 blocked。implemented 必须有提交 SHA、实现说明和该包要求的 docs/offline 通过证据；accepted 还需全部门禁及已 accepted 依赖。

证据条目格式：

```json
{
  "case": "A01", "gate": "docs", "result": "passed",
  "revision": "<40-character-implementation-commit-SHA>",
  "at": "2026-11-07T12:00:00Z",
  "report": "reports/P00-A01-docs.json", "sha256": "<64-character-report-SHA256>"
}
```

report 是 evidence-root 内的 JSON，包含与条目相同的 case/gate/result/revision/at，以及非空 `scope` 字符串数组、`checks` 数组（每项 `name`、`result`）。另记录 command、environment、客户端/依赖版本、独立 oracle、产物校验及清理摘要。passed report 的每项 check 都必须 passed。敏感原始数据另存，不嵌进此记录。

同 case/gate 可追加多次记录，保留失败历史；以当前 revision 下最新时间结果为准，时间相同的冲突拒绝。修改实现后重新测试，不能把旧 revision 的通过记录搬过来。`--complete` 强制读取报告并校验 hash、全部 required case/gate 与依赖状态。

最后的 A52 先记录独立证据审计，再运行 `--complete`，保存检查器输出作附加收据；不要产生“必须先有本次 checker 成功报告才能运行 checker”的循环。检查器只验证记录与文件一致，不证明测试诚实、账号可用或覆盖了所有参数。人工审核覆盖账本与真实结果仍是硬门。

## Runtime 门禁

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
```

先跑变化涉及的 test_*.py；广泛 runtime/协议变更跑全部门禁。不要凭历史测试总数验收。每个 RPC parser 覆盖成功、合法空、拒绝、变形、身份错配与多 envelope；不把 all-mock suite 当兼容上游。

持久化使用真实 SQLite 临时数据库和两个进程，不只 mock：迁移、并发 reserve、payload 冲突、租约丢失、late callback、账号变更、磁盘满/权限错误、过期、提交未知窗口与 SIGKILL。图片/音视频完整解码；文件用独立格式 reader；远端变更用新读回而非旧缓存或同一 ack。

协议测试走真实 SDK stdio：实际 discover/initialize、list/call、input/outputSchema、structuredContent/text、annotations、pagination、错误与超时。新生成面和现有五面都需适用兼容检查。golden 更新说明语义原因，不能批量覆盖掩盖回归。

## 在线门禁

live 从不在 PR CI 自动跑。先核对既有授权、账号类别、配额预算、资源范围与清理责任。完整 canary 使用 `gemini-live-canary` 专用账号/environment，遵守 `scripts/run_live_canary.py` 的 flag 与两个 opt-in 变量；用脚本实际 --help 确认选项。日常账号有界手测单列，不冒充 full canary。

逐项运行 acceptance.md 并展开 capabilities×actions×被支持 selector/input 类的子项。账号不可用、无权限或上游退役记录原因和当前证据；不能通过扩大权限或制造数百次请求来求通过。只清理由本轮明确拥有且已授权删除的资源，用精确 ID 和新读回对账；不能用标题相似性扫删私人历史。

录制/MFA/登录/付款/共享同意由用户完成。公开源码/帮助不能证明实际账号结果。收据不包含 Cookie、headers、prompt、对话正文、私密 ID、签名媒体 URL 或原始 HAR。

## 包和真实客户端门禁

```bash
python scripts/package_release.py --outdir dist
python scripts/check_version_consistency.py --artifacts-dir dist
```

在干净环境运行仓库的 `scripts/smoke_installed_wheel.py`，用实际 --help 确认参数；在 checkout 外隔离 uvx onboarding。检查所有 runtime Skill 资产和新入口，安装后实际读 package data。客户端配置解析、SDK 协议、真实客户端安装、真实上游调用是四种不同证据；每个宣称支持的 OS/客户端组合单独记录。
