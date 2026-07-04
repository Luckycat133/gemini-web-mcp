# Gemini MCP Server 文档中心

欢迎使用 Gemini MCP Server v2.0 文档！

---

## 📚 文档目录

### 快速入门
- [快速开始](./quickstart.md) - 5分钟上手指南
- [Cookie 获取指南](./cookie-setup.md) - 获取认证 Cookie
- [环境变量配置](./configuration.md) - 环境变量详解

### 使用指南
- [工具使用手册](./tools.md) - 所有 MCP 工具详细说明
- [模型选择指南](./models.md) - 选择合适的模型
- [现网 UI 覆盖表](./live-ui-coverage.md) - 已登录 Gemini Web UI 与 MCP 覆盖关系
- [媒体生成教程](./media-generation.md) - 图像/视频/音乐生成
- [Deep Research 指南](./deep-research.md) - 深度研究功能
- [会话管理](./session-management.md) - 多轮对话使用

### 部署与配置
- [Claude Desktop 部署](./claude-desktop.md)
- [MCP Inspector 测试](./testing.md)
- [生产部署](./production.md)

### 技术文档
- [技术架构](./architecture.md) - 系统设计
- [API 参考](./api-reference.md) - 内部 API 文档
- [常量与配置](./constants-reference.md) - 配置说明
- [MCP Contract Evaluation](../evaluations/gemini_web_mcp_contract.xml) - 只读工具选择与安全元数据评估
- [Codex Skill](../.agents/skills/gemini-web-mcp/SKILL.md) - 可公开安装的 agent 使用流程

### 参考资料
- [常见问题 FAQ](./faq.md)
- [Release Notes 2026-05-23](./release-notes-2026-05-23.md)
- [故障排除](./troubleshooting.md)
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
| 推荐工具面 | `core` 默认，`manage` / `prompts` 按需附加 |
| Deep Research | ✅ AI Plus 支持 |
| Gem 管理 | ✅ 自定义助手管理 |
| MCP 评估 | ✅ 13 个只读 contract-level QA |
| Codex Skill | ✅ 公开 `.agents/skills/gemini-web-mcp` + 本地 `.codex/skills/gemini-web-mcp` |

---

## ⚠️ 免责声明

本项目仅供技术研究与教育用途。使用逆向工程方式访问 Gemini Web 可能违反 Google 服务条款，并存在账户被限制的风险。使用者需自行承担所有风险。

---

## 📞 支持

有问题？查看 [FAQ](./faq.md) 或 [故障排除](./troubleshooting.md)。
