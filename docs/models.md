# 模型选择指南

了解 Gemini 模型家族，选择最适合您需求的模型。

---

## 模型契约

本项目保留稳定 MCP 别名，也允许传入当前账户运行时模型名：

| MCP 别名 | 2026-09-26 Web UI | MCP 请求名 |
|------|---------|---------|
| flash-lite / lite | 3.5 Flash-Lite | `Flash-Lite` |
| flash / fast | 3.8 Flash | `gemini-3-flash` |
| pro | 3.1 Pro | `gemini-3-pro` |

这三个显示名已在登录的 Chrome Gemini Web 模型菜单中核对。
`gemini_list_models` 读取运行时注册表，但当前上游库在本次实机运行中只返回
`Flash-Lite`、`Flash`、`Pro` 的通用显示名，Flash-Lite 的内部模型名还是
`unknown`。因此显示版本以网页菜单为准，请求名以实际成功的 MCP 调用为准。
[Google 的 3.8 Flash 公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/)
和 [3.5 Flash-Lite 公告](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-6-flash-3-5-flash-lite-3-5-flash-cyber/)
与本次网页观察一致。

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
- 当前网页菜单显示为 3.5 Flash-Lite

**适用场景：**
- 快速问答
- 简单解释
- 基础代码编写
- 图片生成
- Nano Banana 2 Lite 图片生成

**使用示例：**
```
gemini_chat, message: 什么是 HTML？, model: flash-lite, thinking_level: standard
```

---

### 2. Flash

**特点：**
- 当前网页端 3.8 Flash
- 标准模式适合大多数问题
- 扩展模式适合更复杂的问题

**适用场景：**
- 通用问答
- 复杂计算
- 代码调试
- 学习与理解
- 音乐创作（Lyria；实际版本需看上游证据）
- Deep Research

**使用示例：**
```
gemini_chat, message: 请推导这个数学问题, model: flash, thinking_level: extended
```

---

### 3. Pro

**特点：**
- 当前网页菜单显示为 3.1 Pro
- 本次测试账号可用，其他账号是否可用取决于订阅和限额
- 网页音乐模式可选完整曲目

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

Flash-Lite 首轮使用 **Nano Banana 2 Lite**；Flash 和 Pro 首轮使用
**Nano Banana 2**。本次 MCP 实测 Flash 与 Flash-Lite 均保存了有效的本地图片，
但上游响应没有标出精确图片后端，`observed_backend` 为 `null`。
如果网页里出现 Pro redo，那是首轮生成完成后的二次操作，不是独立首轮图像模型。
[Gemini 图片帮助](https://support.google.com/gemini/answer/14286560)说明了这三种图片路线。

### 视频生成

当前网页专用 [视频页面](https://gemini.google.com/videos)显示 Gemini Omni。
本次账号在该页面生成并下载了 10 秒 MP4。当前 MCP 的通用聊天视频请求只返回
文本和 `ARTIFACT_NOT_RETURNED`，因此不要把 `gemini_generate_media(media_type="video")`
当作已验证的视频入口。Google 的[视频帮助](https://support.google.com/gemini/answer/16126339)
也将现行生成模型称为 Gemini Omni。

### 音乐生成

当前 Gemini App 公告介绍 **Lyria 3.5**；网页帮助说明 Pro 模型可选完整曲目，
也提供短曲目和完整曲目的长度选项。当前 MCP 返回音频和封面视频文件，
但没有可验证的版本标识，因此结构化结果只报告 `Lyria`，并把
`observed_backend` 留空。`thinking_level=extended` 是思考等级，不是音乐版本选择器。
参见 [Lyria 3.5 公告](https://blog.google/innovation-and-ai/products/gemini-app/better-tracks-lyria-gemini/)
和 [Gemini 音乐帮助](https://support.google.com/gemini/answer/16901237)。

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
  ├─ 生成音乐？ → 使用 Lyria；检查产物与实际曲长
  │
  ├─ 做 Deep Research？ → 选择 flash/pro 并检查当前账户能力
  │
  └─ 需要 Pro？ → 检查当前账户权限和限额
```

### 任务与模型对照

| 任务类型 | 推荐模型 |
|---------|---------|
| 日常问答 | flash-lite / flash |
| 代码完成 | flash |
| 代码调试 | flash extended / pro extended |
| 图片生成 | flash-lite / flash / pro |
| 视频生成 | Gemini Web 专用视频模式；MCP 路线仍待修复 |
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
