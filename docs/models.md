# 模型选择指南

了解 Gemini 模型家族，选择最适合您需求的模型。

---

## 模型契约

本项目保留稳定 MCP 别名，也允许传入当前账户运行时模型名：

| MCP 别名 | 当前 Web UI 模型 |
|------|---------|
| flash-lite / lite | 3.1 Flash-Lite |
| flash / fast | 3.5 Flash (`gemini-3-flash`) |
| pro | 3.1 Pro (`gemini-3-pro`) |

Gemini Web 的可见模型名会变动。2026-05-22 的已登录 Web UI 中可见
`3.1 Flash-Lite`、`3.5 Flash` 和 `3.1 Pro`，并有独立的思考等级菜单。
当前 MCP 通过 `gemini_list_models` 暴露认证账户的运行时模型注册表；
运行时注册表比这份静态文档更接近真实账户状态。

思考等级不是第四个模型。上述三个模型都可用：

| 参数 | Web UI |
|------|--------|
| `thinking_level: standard` | 标准 |
| `thinking_level: extended` | 扩展 |

`thinking` 仍作为旧兼容模型别名保留；新调用应优先选三种 Web UI
模型，再用 `thinking_level` 指定思考等级。

---

## 🔍 模型详细说明

### 1. Flash-Lite

**特点：**
- 速度最快
- 适合日常问答
- 免费可用
- 最低延迟的网页端模型

**适用场景：**
- 快速问答
- 简单解释
- 基础代码编写
- 图片生成
- 视频生成

**使用示例：**
```
gemini_chat, message: 什么是 HTML？, model: flash-lite, thinking_level: standard
```

---

### 2. Flash

**特点：**
- 当前网页端 3.5 Flash
- 标准模式适合大多数问题
- 扩展模式适合更复杂的问题

**适用场景：**
- 通用问答
- 复杂计算
- 代码调试
- 学习与理解
- 音乐创作（Lyria 3）
- Deep Research

**使用示例：**
```
gemini_chat, message: 请推导这个数学问题, model: flash, thinking_level: extended
```

---

### 3. Pro

**特点：**
- 最高质量
- 最强大的推理能力
- 需要 AI Plus 订阅
- 音乐生成：Lyria 3 Pro

**适用场景：**
- 专业任务
- 复杂编程
- 深度分析
- 创意写作
- Deep Research
- 高级媒体生成

**使用示例：**
```
gemini_chat, message: 编写一个完整的 Web 应用架构, model: pro, thinking_level: extended
```

---

## 🎵 媒体模型映射

### 图像生成

所有模型首轮都使用 **Nano Banana 2** 进行图像生成。
如果网页里出现 Pro redo，那是首轮生成完成后的二次操作，不是独立首轮图像模型。

### 视频生成

所有模型都使用 **Veo 3.1** 进行视频生成（最长 60 秒）。

### 音乐生成

音乐生成按当前网页模型分流：

- `flash-lite` / `flash` / `fast` / `thinking` → **Lyria 3**
- `pro` → **Lyria 3 Pro**

不要把 `thinking_level=extended` 当作音乐模型选择器；它只控制思考等级。

---

## 💡 选择建议

### 场景决策树

```
开始
  │
  ├─ 想要最快回答？ → 选择 flash-lite + standard
  │
  ├─ 需要更强思考？ → 加 thinking_level: extended
  │
  ├─ 生成音乐？ → flash 系列走 Lyria 3，pro 走 Lyria 3 Pro
  │
  ├─ 做 Deep Research？ → 选择 flash/pro 并检查当前账户能力
  │
  └─ 需要最高质量？ → 选择 pro（需要 AI Plus）
```

### 任务与模型对照

| 任务类型 | 推荐模型 |
|---------|---------|
| 日常问答 | flash-lite / flash |
| 代码完成 | flash |
| 代码调试 | flash extended / pro extended |
| 图片生成 | flash-lite / flash / pro |
| 视频生成 | flash-lite / flash / pro |
| 音乐 | flash / pro |
| Deep Research | flash / pro，受账户能力约束 |
| 专业内容创作 | pro |

---

## ⚙️ 如何使用

### 在对话中指定模型

```
gemini_chat, message: 你的问题, model: flash-lite, thinking_level: standard
gemini_chat, message: 你的问题, model: flash, thinking_level: extended
gemini_chat, message: 你的问题, model: pro, thinking_level: extended
```

### 在会话创建时指定模型

```
gemini_start_chat, model: pro, thinking_level: extended
```

### 查看可用模型

使用工具：
```
gemini_list_models
```

---

## ⚠️ 注意事项

1. **模型可用性**：以认证账户的运行时模型注册表和 Gemini Web 限额为准
2. **速率限制**：所有模型都有请求频率限制
3. **Cookie 有效性**：需要定期更新 Cookie
4. **功能差异**：部分功能可能受地区或账户状态影响
