# 工具使用手册

完整的 MCP 工具使用说明。

---

## 📋 工具分类

- [对话工具](#对话工具)
- [媒体工具](#媒体工具)
- [图像编辑工具](#图像编辑工具-v20-新增)
- [提示词管理](#提示词管理-高级)
- [Cookie 管理](#cookie-管理)
- [管理工具](#管理工具)

---

## 对话工具

### gemini_chat

单次对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: "fast" | "thinking" | "pro" (默认: "fast")
- `image_paths`: list[str] - 可选图片路径

### gemini_chat_stream

单次流式对话。

**参数：**
- `message`: str - 要发送的消息
- `model`: "fast" | "thinking" | "pro" (默认: "fast")
- `image_paths`: list[str] - 可选图片路径

### gemini_start_chat

创建多轮会话。

**参数：**
- `system_instruction`: str - 可选系统提示
- `model`: "fast" | "thinking" | "pro" (默认: "fast")

**返回：** 会话 ID，用于后续消息

### gemini_send_message

会话消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径

### gemini_send_message_stream

会话流式消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 消息内容
- `image_paths`: list[str] - 可选图片路径

### gemini_list_sessions

列出会话。

**参数：** 无

### gemini_reset_session

重置会话。

**参数：**
- `session_id`: str - 会话 ID

---

## 媒体工具

### gemini_generate_media

通用媒体生成。

**参数：**
- `prompt`: str - 生成描述
- `media_type`: "image" | "video" | "music"
- `model`: "fast" | "thinking" | "pro" (默认: "fast")
- `image_path`: str - 可选参考图片

### gemini_generate_music

音乐生成（便捷工具）。

**参数：**
- `prompt`: str - 音乐描述
- `model`: "fast" | "thinking" | "pro" (默认: "thinking")

**音乐时长：**
- `fast` - Lyria 3 Clip: 30秒
- `thinking/pro` - Lyria 3 Pro: 完整歌曲

---

## 图像编辑工具 (v2.0 新增)

### gemini_edit_image

图像编辑。

**参数：**
- `prompt`: str - 编辑提示词
- `image_path`: str - 原始图像路径
- `model`: "fast" | "thinking" | "pro" (默认: "fast")

### gemini_variations

图像变体生成。

**参数：**
- `prompt`: str - 可选风格描述
- `image_path`: str - 可选参考图像
- `num_variations`: int - 变体数量 (1-4)
- `model`: "fast" | "thinking" | "pro" (默认: "fast")

---

## 提示词管理 (高级)

### gemini_manage_prompts

提示词 CRUD 管理。

**参数：**
- `action`: "list" | "list_categories" | "get" | "create" | "update" | "delete"
- `prompt_id`: str - 提示词 ID (get/update/delete)
- `name`: str - 提示词名称 (create/update)
- `content`: str - 提示词内容 (create/update)
- `category`: str - 分类 (可选)
- `description`: str - 描述 (可选)

---

## Cookie 管理

### gemini_get_cookie_status

查看 Cookie 状态。

**参数：** 无

### gemini_get_cookie_from_browser

从浏览器获取 Cookie。

**参数：**
- `browser`: str - 浏览器类型 (默认: "chrome")

---

## 管理工具

### gemini_reset

重置客户端。

**参数：** 无

### gemini_list_features

列出可用功能。

**参数：** 无

---

## 🛡️ 智能错误处理 (v2.0 新增)

v2.0 新增智能错误处理，遇到问题时会自动提供解决方案。

### 错误响应格式

```
✅ 错误: 未设置 GEMINI_PSID 环境变量
💡 解决方案: 请设置环境变量 export GEMINI_PSID=xxx 或使用 gemini_get_cookie_from_browser
🔧 可使用工具: gemini_get_cookie_from_browser
```

### 支持的错误类型

| 错误类型 | 解决方案 | 建议工具 |
|---------|---------|---------|
| 无 Cookie | 设置 PSID 或从浏览器获取 | gemini_get_cookie_from_browser |
| Cookie 过期 | 更新 Cookie | gemini_get_cookie_from_browser |
| 会话不存在 | 创建会话 | gemini_start_chat |
| 模型不可用 | 切换模型 | - |
| 网络错误 | 检查网络/代理 | - |
| 限流 | 稍后重试 | - |
| 图片加载失败 | 检查路径/安装 pillow | - |

### AI 自主解决

AI 可以根据错误信息自动调用相应工具解决问题，无需人工干预。

例如，遇到无 Cookie 错误时，AI 可以自动：
1. 识别问题（NO_COOKIE）
2. 调用 `gemini_get_cookie_from_browser`
3. 重新执行原任务

---

## 💡 使用提示

### 模型选择建议

| 场景 | 推荐模型 |
|------|---------|
| 快速问答 | fast |
| 需要推理 | thinking |
| 复杂任务 | pro |
| 音乐生成 | thinking/pro |
| 图像编辑 | fast |

### 工具组配置

根据需要通过 `GEMINI_TOOLS` 环境变量配置加载的工具：

```bash
# 仅基础对话
GEMINI_TOOLS=basic

# 基础 + 媒体
GEMINI_TOOLS=basic,media

# 全部功能
GEMINI_TOOLS=all
```
