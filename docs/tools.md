# 工具使用手册

完整的 MCP 工具使用说明。

---

## 📋 工具分类

- [对话工具](#对话工具)
- [研究工具](#研究工具)
- [媒体工具](#媒体工具)
- [文件工具](#文件工具)
- [管理工具](#管理工具)

---

## 对话工具

### gemini_chat

单次对话，适合快速问答。

**参数：**
- `message`: str - 要发送的消息
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "fast")
- `image_paths`: list[str] - 可选图片路径

**示例：**
```
使用 gemini_chat，消息: 什么是机器学习？, model: pro
```

### gemini_start_chat

创建新的多轮对话会话。

**参数：**
- `system_instruction`: str - 可选系统提示
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "fast")

**示例：**
```
gemini_start_chat, model: thinking, system_instruction: 你是一位编程助手
```

### gemini_send_message

在现有会话中发送消息。

**参数：**
- `session_id`: str - 会话 ID
- `message`: str - 要发送的消息
- `image_paths`: list[str] - 可选图片路径

**示例：**
```
gemini_send_message, session_id: abc123, message: 帮我写一个 Python 函数
```

### gemini_list_sessions

列出所有活跃会话。

**参数：** 无

**示例：**
```
gemini_list_sessions
```

### gemini_reset_session

重置并移除指定会话。

**参数：**
- `session_id`: str - 会话 ID

**示例：**
```
gemini_reset_session, session_id: abc123
```

---

## 研究工具

### gemini_deep_research

启动 Deep Research 深度研究任务。

**⚠️ 注意：需要 AI Plus 订阅**

**参数：**
- `query`: str - 研究主题或问题
- `model`: Literal["thinking", "pro"] - 模型选择 (默认: "thinking")
- `timeout_seconds`: int - 超时时间 (默认: 600)

**示例：**
```
gemini_deep_research, query: 2026年人工智能发展趋势, model: pro
```

**工作流程：**
1. 创建研究计划和大纲
2. 执行多轮搜索和信息收集
3. 生成完整报告，包含引用来源
4. 可能需要数分钟完成

---

## 媒体工具

### gemini_generate_media

通用媒体生成工具。

**参数：**
- `prompt`: str - 生成描述
- `media_type`: Literal["image", "video", "music"] - 媒体类型
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "fast")
- `image_path`: str - 可选参考图片路径

**示例：**
```
gemini_generate_media, prompt: 一座未来城市, media_type: image, model: fast
```

### gemini_generate_music

音乐生成便捷工具。

**参数：**
- `prompt`: str - 音乐描述
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "thinking")

**示例：**
```
gemini_generate_music, prompt: 一首轻快的钢琴曲, model: thinking
```

**音乐模型说明：**
- `fast` - Lyria 3 Clip: 30秒片段
- `thinking/pro` - Lyria 3 Pro: 完整歌曲（约3分钟）

---

## 文件工具

### gemini_upload_file

上传文件供 Gemini 分析。

**参数：**
- `file_path`: str - 文件路径
- `analysis_prompt`: str - 可选分析提示
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "fast")

**支持格式：**
- 图片: JPG, PNG, GIF, WebP
- 文档: PDF, TXT, DOCX
- 其他: 多种格式

**示例：**
```
gemini_upload_file, file_path: /path/to/report.pdf
```

### gemini_analyze_url

分析 URL 内容。

**参数：**
- `url`: str - 要分析的 URL
- `analysis_prompt`: str - 可选分析提示
- `model`: Literal["fast", "thinking", "pro"] - 模型选择 (默认: "fast")

**支持类型：**
- 普通网页
- YouTube 视频
- 其他在线内容

**示例：**
```
gemini_analyze_url, url: https://example.com
```

---

## 管理工具

### gemini_list_chats

列出历史聊天记录。

**参数：**
- `limit`: int - 返回数量 (默认: 10)

**示例：**
```
gemini_list_chats, limit: 20
```

### gemini_manage_gems

管理自定义 Gems（AI 助手）。

**参数：**
- `action`: Literal["list", "create", "update", "delete"] - 操作类型
- `gem_id`: str - Gem ID (update/delete 时必填)
- `name`: str - Gem 名称 (create/update 时必填)
- `description`: str - Gem 描述 (可选)
- `instructions`: str - 系统指令 (可选)

**示例：**
```
# 列出所有 Gems
gemini_manage_gems, action: list

# 创建新 Gem
gemini_manage_gems, action: create, name: 翻译助手, instructions: 你是一位专业翻译
```

### gemini_list_models

列出所有可用模型及其说明。

**参数：** 无

**示例：**
```
gemini_list_models
```

### gemini_list_features

列出所有可用功能和特性。

**参数：** 无

**示例：**
```
gemini_list_features
```

### gemini_health_check

检查连接健康状态。

**参数：** 无

**示例：**
```
gemini_health_check
```

### gemini_reset

重置客户端并清除所有会话。

**参数：** 无

**示例：**
```
gemini_reset
```

---

## 💡 使用提示

### 会话管理最佳实践

1. 对于主题相关的多个问题，使用多轮对话（`gemini_start_chat` + `gemini_send_message`）
2. 对于独立问题，直接使用 `gemini_chat`
3. 定期清理不使用的会话

### 模型选择建议

| 场景 | 推荐模型 |
|------|---------|
| 快速问答 | fast (gemini-3-flash) |
| 需要推理 | thinking (gemini-3-flash-thinking) |
| 复杂任务 | pro (gemini-3.1-pro) |
| 音乐生成 | thinking/pro（完整歌曲） |
| Deep Research | thinking/pro |
