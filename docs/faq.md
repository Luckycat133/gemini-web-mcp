# 常见问题 FAQ

解答您在使用 Gemini MCP Server 时最常遇到的问题。

---

## 🚀 安装与配置

### Q: 支持哪些 Python 版本？
**A:** Python 3.10 或更高版本。

### Q: pip 安装失败怎么办？
**A:** 
1. 尝试更新 pip：`pip install --upgrade pip`
2. 使用国内镜像源
3. 检查网络连接
4. 使用 uv：`uv pip install`

### Q: 如何获取 Cookie？
**A:** 
1. 访问 gemini.google.com
2. 登录 Google 账户
3. F12 → Application → Cookies
4. 复制 `__Secure-1PSID`
详细步骤请查看 [Cookie获取指南](./cookie-setup.md)。

### Q: Cookie 会过期吗？
**A:** 是的，Cookie 会定期过期。如果遇到认证错误，请重新获取 Cookie。

---

## 🤖 模型与功能

### Q: Pro 模型需要付费吗？
**A:** 以当前账户运行时模型注册表和 Gemini Web 限额为准。先用 `gemini_list_models` 查看当前认证账户可见的模型。

### Q: 哪些功能是免费的？
**A:**
- 对话：以网页端可见的 Flash-Lite、Flash、Pro 及账户限额为准
- 图像生成：所有模型
- 视频生成：所有模型
- 音乐：flash 系列对应 Lyria 3，pro 对应 Lyria 3 Pro
- Deep Research：需要 AI Plus

### Q: 音乐生成选哪个模型？
**A:**
- `flash-lite` / `flash` / `fast` / `thinking`：Lyria 3
- `pro`：Lyria 3 Pro
- `extended` 思考等级不等于音乐模型

### Q: 图像生成时 `pro` 为什么没有直接换模型？
**A:** 当前网页端首轮图像生成统一走 Nano Banana 2。`pro` 相关的图像增强属于网页生成后的 Pro redo，不是独立首轮模型。

### Q: 视频生成最长多长？
**A:** 60秒（Veo 3.1）。

---

## 🔧 工具与使用

### Q: 怎么选择适合的模型？
**A:** 参考 [模型选择指南](./models.md)，或使用工具 `gemini_list_models`。

### Q: 如何在 Claude 中调用工具？
**A:** 在对话中直接说，例如：
```
请用 gemini 生成一张猫咪图片
```

### Q: 多轮会话如何使用？
**A:**
1. 使用 `gemini_start_chat` 创建会话
2. 记住返回的 session_id
3. 使用 `gemini_send_message` 发送后续消息
4. 使用 `gemini_reset_session` 重置会话

### Q: Deep Research 总是超时怎么办？
**A:**
1. 增加超时时间：`timeout_seconds: 1200`（20分钟）
2. 检查网络连接
3. 使用更快的模型
4. 尝试缩小研究主题范围

### Q: 文件上传支持哪些格式？
**A:** 支持多种格式：
- 图像：JPG, PNG, GIF, WebP
- 文档：PDF, TXT, DOCX
- 其他常见格式

---

## 🐛 常见错误

### Q: 错误 "Failed to perform, curl: (35)" 怎么办？
**A:** 这是 OpenSSL 配置问题。
1. 检查 Python OpenSSL 版本
2. 尝试重新安装依赖
3. 在其他环境中测试

### Q: 认证错误怎么办？
**A:**
1. 检查 Cookie 是否正确复制
2. 确认 Cookie 没有过期
3. 尝试重新获取 Cookie
4. 检查网络连接

### Q: Claude 无法连接到 MCP 服务器怎么办？
**A:**
1. 检查 Claude Desktop 配置
2. 确认路径是否正确
3. 检查环境变量
4. 查看 Claude 日志
5. 使用 MCP Inspector 测试

### Q: 图片生成总是失败怎么办？
**A:**
1. 检查网络连接
2. 尝试不同的模型
3. 检查是否有地区限制
4. 查看错误信息

---

## 💡 进阶问题

### Q: 如何自定义会话系统提示？
**A:** 主服务当前没有独立的 `system_instruction` 参数。需要稳定复用指令时，使用 Gem 或提示词管理工具。

### Q: 支持多个 Cookie 切换吗？
**A:** 当前版本不支持，但您可以手动更改环境变量重启服务器。

### Q: 可以在服务器运行时更新 Cookie 吗？
**A:** 不可以，需要重启服务器。可以使用 `gemini_reset` 重置连接。

### Q: 支持流式响应吗？
**A:** 支持。使用 `gemini_chat_stream` 或 `gemini_send_message_stream`。

---

## ⚖️ 合规与安全

### Q: 使用这个工具违反 Google 服务条款吗？
**A:** 本工具仅供研究和教育目的。使用时请了解潜在风险，并承担相应责任。

### Q: Cookie 安全吗？
**A:** Cookie 有访问权限。请：
1. 不要分享 Cookie
2. 不要将 Cookie 提交到 Git
3. 使用专门的账户用于研究
4. 定期更新 Cookie

### Q: 我应该用主要 Google 账户吗？
**A:** 不建议。最好使用一个专门的研究账户。

---

## 📚 更多帮助

如果您的问题不在上述列表中，请：
1. 查看 [故障排除](./troubleshooting.md)
2. 阅读 [工具使用手册](./tools.md)
3. 查看 [Changelog](./changelog.md)
