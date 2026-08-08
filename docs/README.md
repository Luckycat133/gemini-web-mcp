# Gemini MCP Server 文档中心

欢迎使用 Gemini MCP Server v0.2.0 文档！

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
- [现网 UI 覆盖表](./live-ui-coverage.md) - 已登录 Gemini Web UI 与 MCP 覆盖关系

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
- [开发 Skill](../.agents/skills/gemini-web-mcp-development/SKILL.md) - 仓库架构、测试、打包和发布流程

### 参考资料
- [常见问题 FAQ](./faq.md)
- [故障排除](./troubleshooting.md)
- [实机测试清单](./manual-testing.md) - 哪些内容需要真实账号验证
- [Release Notes 2026-05-23](./release-notes-2026-05-23.md)
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
| 媒体生成 | 图像(Nano Banana 2), 视频(Veo 3.1), 音乐(Lyria 3 / Lyria 3 Pro) |
| 推荐工具面 | 文本从 `model` 开始，多模态用 `core`，低 token 用 compact，`all` 仅维护验证 |
| Deep Research | ✅ AI Plus 支持 |
| Gem 管理 | ✅ 自定义助手管理 |
| MCP 评估 | ✅ 17 个只读 contract-level QA |
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
