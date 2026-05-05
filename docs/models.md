# 模型选择指南

了解 Gemini 模型家族，选择最适合您需求的模型。

---

## 🤖 可用模型（2026.5）

| 模型 | 显示名称 | 底层模型 | 订阅要求 | 速度 | 质量 |
|------|---------|---------|---------|------|------|
| fast | Gemini 3 Flash | gemini-3-flash | 免费 | ⚡⚡⚡⚡⚡ | ⭐⭐⭐ |
| thinking | Gemini 3 Flash Thinking | gemini-3-flash-thinking | 免费 | ⚡⚡⚡ | ⭐⭐⭐⭐ |
| pro | Gemini 3.1 Pro | gemini-3.1-pro | AI Plus | ⚡⚡ | ⭐⭐⭐⭐⭐ |

---

## 🔍 模型详细说明

### 1. Fast (gemini-3-flash)

**特点：**
- 速度最快
- 适合日常问答
- 免费可用
- 音乐生成：30秒片段（Lyria 3 Clip）

**适用场景：**
- 快速问答
- 简单解释
- 基础代码编写
- 图片生成
- 视频生成

**使用示例：**
```
gemini_chat, message: 什么是 HTML？, model: fast
```

---

### 2. Thinking (gemini-3-flash-thinking)

**特点：**
- 显示推理链
- 更好的逻辑能力
- 免费可用
- 音乐生成：完整歌曲（Lyria 3 Pro）

**适用场景：**
- 需要推理的问题
- 复杂计算
- 代码调试
- 学习与理解
- 音乐创作（完整歌曲）
- Deep Research

**使用示例：**
```
gemini_chat, message: 请推导这个数学问题, model: thinking
```

---

### 3. Pro (gemini-3.1-pro)

**特点：**
- 最高质量
- 最强大的推理能力
- 需要 AI Plus 订阅
- 音乐生成：完整歌曲（Lyria 3 Pro）

**适用场景：**
- 专业任务
- 复杂编程
- 深度分析
- 创意写作
- Deep Research
- 高级媒体生成

**使用示例：**
```
gemini_chat, message: 编写一个完整的 Web 应用架构, model: pro
```

---

## 🎵 媒体模型映射

### 图像生成

所有模型都使用 **Nano Banana 2** 进行图像生成。

### 视频生成

所有模型都使用 **Veo 3.1** 进行视频生成（最长 60 秒）。

### 音乐生成

| 聊天模型 | 音乐模型 | 时长 |
|---------|---------|------|
| fast | Lyria 3 Clip | 30秒 |
| thinking | Lyria 3 Pro | 完整（约3分钟） |
| pro | Lyria 3 Pro | 完整（约3分钟） |

---

## 💡 选择建议

### 场景决策树

```
开始
  │
  ├─ 想要快速回答？ → 选择 fast
  │
  ├─ 需要思考过程？ → 选择 thinking
  │
  ├─ 生成完整音乐？ → 选择 thinking 或 pro
  │
  ├─ 做 Deep Research？ → 选择 thinking 或 pro
  │
  └─ 需要最高质量？ → 选择 pro（需要 AI Plus）
```

### 任务与模型对照

| 任务类型 | 推荐模型 |
|---------|---------|
| 日常问答 | fast |
| 代码完成 | fast / thinking |
| 代码调试 | thinking / pro |
| 图片生成 | fast / thinking / pro |
| 视频生成 | fast / thinking / pro |
| 音乐片段（30秒） | fast |
| 完整歌曲 | thinking / pro |
| Deep Research | thinking / pro |
| 专业内容创作 | pro |

---

## ⚙️ 如何使用

### 在对话中指定模型

```
gemini_chat, message: 你的问题, model: fast
gemini_chat, message: 你的问题, model: thinking
gemini_chat, message: 你的问题, model: pro
```

### 在会话创建时指定模型

```
gemini_start_chat, model: thinking, system_instruction: 你的指令
```

### 查看可用模型

使用工具：
```
gemini_list_models
```

---

## ⚠️ 注意事项

1. **Pro 模型限制**：需要 AI Plus 订阅，免费账户无法使用
2. **速率限制**：所有模型都有请求频率限制
3. **Cookie 有效性**：需要定期更新 Cookie
4. **功能差异**：部分功能可能受地区或账户状态影响
