# Gemini MCP Server 文档中心

本目录说明当前源码的工具、安装与验证合同；已发布版本和分支差异以 Changelog 为准。

---

## 📚 文档目录

### 语言入口
- [English README](../README.md)
- [简体中文 README](../README.zh-CN.md)

### 快速入门
- [快速开始](./quickstart.md) - 5分钟上手指南
- [客户端安装与验证](./client-examples.md) - 一条命令预检、Codex/Claude/VS Code 配置和图像产物验证
- [Cookie 获取指南](./cookie-setup.md) - 获取认证 Cookie
- [环境变量配置](./configuration.md) - 环境变量详解

### 使用指南
- [工具使用手册](./tools.md) - 所有 MCP 工具详细说明（含媒体生成、Deep Research、会话管理）
- [模型选择指南](./models.md) - 选择合适的模型
- [网页版功能实测矩阵](./web-feature-coverage.md) - 当前实现、真实证据与尚未覆盖能力
- [Notebook、Chat 与 Task](./notebook-task-workflows.md) - 模式选择、确认、后台恢复与附件
- [原生 Skills 与 Notebook 管理](./native-account-resources.md) - 准确资源 ID、分页与读回
- [Task 偏好](./task-preferences.md) - 已保存偏好与实际行为的区别
- [历史 UI 覆盖表](./live-ui-coverage.md) - 保留的原生界面观察快照

### 部署与配置
- [Launch Kit](./launch-kit.md) - 分发链接、安装文案和社交媒体发布素材

### 技术文档
- [技术架构](./architecture.md) - 系统设计
- [开发状态与下一步](./development-status.md) - 已实现、部分完成、live 证据和 owner 决策边界
- [MCP SDK 与客户端兼容性](./mcp-sdk-compatibility.md) - v2 运行时、协议路径与 SDK v1 维护截止日期
- [Gemini Web Live Canary](./live-canary.md) - 专用账号 opt-in 探测、脱敏报告与漂移 issue 流程
- [API 参考](./api-reference.md) - 内部 API 文档
- [环境变量配置](./configuration.md) - 环境变量与常量说明
- [MCP Contract Evaluation](../evaluations/gemini_web_mcp_contract.xml) - 只读工具选择与安全元数据评估
- [运行时 Skill](../.agents/skills/gemini-web-mcp/SKILL.md) - 操作已安装 MCP 工具的 agent 使用流程
- [Assistance Skill](../.agents/skills/gemini-assist/SKILL.md) - 专注 assistance 服务器（`gemini-mcp-assist`）的 agent 使用流程
- [开发 Skill](../.agents/skills/gemini-web-mcp-development/SKILL.md) - 仓库架构、测试、打包和发布流程

### 参考资料
- [常见问题 FAQ](./faq.md)
- [故障排除](./troubleshooting.md)
- [实机测试清单](./manual-testing.md) - 哪些内容需要真实账号验证
- [逐工具质量记录](./tool-quality.md) - 34 个业务工具的检查和证据边界
- [源码集成说明](./source-update-integration.md) - 当前分支与已发布 0.2.2 的合同差异
- [Changelog](./changelog.md)
- [贡献指南](./contributing.md)

---

## 🚀 开始使用

如果您是第一次使用，请查看：

1. [快速开始](./quickstart.md) - 基础安装与配置
2. [Cookie 获取指南](./cookie-setup.md) - 获取必要的认证
3. [工具使用手册](./tools.md) - 了解所有可用工具

---

## 📋 项目概览

| 特性 | 说明 |
|------|------|
| 模型 | 旧别名 + 运行时模型发现 |
| 媒体生成 | 图片、编辑、视频和音乐后台返回句柄；完成须有已保存并验证的实际文件 |
| 推荐工具面 | 功能面 `gemini-mcp-function` 与显式账号面 `gemini-mcp-account`；旧 profile、assist、compact 保留兼容 |
| Deep Research | 需要当前账号具备相应权限；完成报告须另行验证 |
| Assistance 专用面 | ✅ `gemini-mcp-assist`：五工具确定性目录（ask/搜索/图像理解/混合理解/异步 Research） |
| Gem 管理 | ✅ 自定义助手管理 |
| MCP 评估 | 维护工具选择、错误、生命周期及安全注解的契约场景；离线通过不等于线上可用 |
| Agent Skills | ✅ 运行时/开发 skill 分离，`.agents/skills` 为唯一仓库来源 |
| History 结果 | ✅ primary/compact 的 list/search/read/export/delete 共用 typed service；delete 区分已验证与仅接受 |
| 浏览器 Cookie | ✅ 不输出 Cookie 值；macOS Keychain 等待有可配置超时 |
| 分发资料 | ✅ `docs/launch-kit.md` |

路线图不是全部完成状态；当前剩余边界见 [开发状态与下一步](./development-status.md)。

---

## ⚠️ 免责声明

本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。使用者需自行承担所有风险。

---

## 📞 支持

有问题？查看 [FAQ](./faq.md) 或 [故障排除](./troubleshooting.md)。
