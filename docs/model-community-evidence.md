# 模型选型：社区反馈与实测证据

社区来源核对日期：2026-10-31；本地复测日期：2026-11-01。以下是具体场景中的个人反馈，不是受控基准、社区共识或对本 MCP 路由的可用性证明。版本、平台、账户、提示词和时间会影响结果；网页、API 与 Antigravity 不能混为一谈。

## 本项目使用经验与本地观察

- 项目用户认为这些模型均有较强的看图和推理能力；Argon 尤其适合写作、创意、角色对话和文风，也可用于特别难的思考。这是用户实践偏好，不是对所有任务的排名。长文人设、一致性和约束仍需独立核查。
- 授权 MCP 音乐测试中，Flash 曾产出约 58 秒可验证 MP3；另有 Pro/extended 请求失败。两次结果不证明任一模型的普遍成功率，也不证明音乐必须用聊天 Pro 或 Argon。Lyria 音乐模式选项与聊天模型是不同维度。
- 2026-10-31 网页只读观察：默认 3.8 Flash 下进入“制作音乐”仍选中 Flash，显示时长、人声、流派；时长有“短版”和“标准”，模型菜单列出四个模型，未显示必须 Pro 或 Argon。本次没有提交生成，菜单不是音频成功证据。
- 授权视频测试中，显式 Pro 曾返回可验证视频；另一次 Flash 对同一提示返回文本拒绝。这些只是具体请求的观察，不构成视频默认选择或永久可用性保证。
- 2026-11-01 真实 MCP 复测：Argon/standard 写作约 31 秒返回正文；Flash/standard 联网约 191 秒返回有 2 条已关联来源的回答；默认 Flash 后台图片返回符合提示的 2048×2048 JPEG，音乐返回约 63.95 秒的可验证 MP3 和 MP4。提交媒体的 stdio 连接关闭后，原句柄仍可读取产物。首条 Flash 普通请求也出现过约 305 秒超时，因此这些成功不构成稳定延迟或通用成功率保证。

## 社区个体反馈

| 来源及环境 | 具体观察 | 限制 |
| --- | --- | --- |
| [3.8 Flash 编程讨论](https://www.reddit.com/r/GeminiAI/comments/1wjmz5x/actually_gemini_38_flash_is_a_beast/)；有评论明确提到 Antigravity | 发帖者称其编程表现突出；其他评论称速度、人工介入和验证能力各有取舍。 | 非统一任务或盲测；Antigravity 体验不能直接推及 Gemini Web MCP。 |
| [3.8 Flash 自定义 Gem 反馈](https://www.reddit.com/r/GeminiAI/comments/1wr2elg/gemini_38_flash_feels_a_bit_off/)；Gemini 自定义 Gem / 网页用户反馈 | 发帖者报告忽略指令、答非所问和幻觉；其他用户也描述不同问题。 | 单次及个体报告，不能确定原因、发生频率或当前状态。 |
| [3.8 Flash 联网反馈](https://www.reddit.com/r/GeminiAI/comments/1xeq89i/is_gemini_flash_38_web_search_broken/)；Gemini App，另有 AI Studio 对比 | 发帖者及部分评论者报告 Flash 有时不检索，直接返回旧知识。 | 原因及发生比例没有验证；这支持检查来源证据，不改变 Flash 默认策略。 |
| [3.1 Pro 聊天与编码反馈](https://www.reddit.com/r/Bard/comments/1rc68fg/how_was_your_experience_in_using_gemini_31_pro_in/)；2026-02 起，Gemini App、AI Studio 等混合入口 | 原作者认为上下文衔接改善但过度奉承；评论者报告 AI Studio 游戏编程有帮助，也报告 App 幻觉。 | 较早个体体验，不能把不同入口的差异归因于确定的模型或服务改动。 |
| [3.1 Pro 编码讨论](https://www.reddit.com/r/google_antigravity/comments/1r9xexn/realworld_coding_experience_with_gemini_31_pro/)；Antigravity 社区 | 有人把它用于独立审查或验证；也有人报告高思考模式过度删减计划。 | 原帖是提问而非实测；这里只采用明确实际使用的评论，不推断通用胜率。 |
| [3.5 Flash-Lite 会议助手测试](https://www.reddit.com/r/GeminiAI/comments/1xgb885/i_used_gemini_35_flashlite_as_the_brain_of_a/)；API、自带 Key 的练习问题 | 作者称短回答更相关，也认为系统提示格式改善更大；尚未用于真实会议。 | 自推广且披露流量激励，样本和环境有限；不支持日常 Web 选用 Lite 或转录能力结论。 |
| [4 Argon 一周讨论](https://www.reddit.com/r/GeminiAI/comments/1xcbqfu/1_week_later_thoughts_on_gemini_4_argon/)；评论涉及 Gemini App、API 和 Antigravity，不统一 | 有人赞赏写作、幽默和材料分析；也有人报告多步骤或编程任务不稳定、额度受限。 | 各人平台和任务不一致；不能将某条反馈推广为全部平台表现。 |
| [4 Argon 首次角色写作测试](https://discuss.ai.google.dev/t/first-test-of-gemini-4-argon-much-better-immersion-but-stricter-with-explicit-adult-roleplay-than-gemini-3-x/189556)；2026-10-15，Google AI 开发者论坛，作者个人账户、具体入口未明确 | 作者比较 3.5 Flash-Lite、3.8 Flash、3.1 Pro 与 4 Argon，主观认为 Argon 的叙事沉浸更好，同时报告内容边界更严格。 | 作者明确说是早期个人测试；不能推断所有文体或上下文都如此。 |
| [创作者的一周 Argon 测试](https://theoreticallymedia.com/blog/gemini-4-argon-tested-800-million-tokens/)；2026-10-13，提前体验，文章讨论 Gemini App 和 Antigravity，具体项目入口不完全统一 | 作者称其帮助梳理故事笔记，也指出创作需要多轮迭代；自行披露曾获 Google 提前体验。 | 个人创作工作流；故事整理不等同端到端长篇写作能力或 MCP 结果。 |
| [Lyria 3 Pro 讨论](https://www.reddit.com/r/Bard/comments/1s3lmky/lyria_3_pro_is_amazing/)；2026-03 至 04，旧版 Gemini App 音乐工具 | 一位用户称当时 Pro/Thinking 可生成最长三分钟、Fast 仍为 30 秒；另有用户报告 Pro 只生成封面，没有音轨。 | 历史个体反馈，不能直接推断当前 3.8 Flash、Argon 或 MCP 的时长和成功率。 |
| [Gemini 音乐体验讨论](https://www.reddit.com/r/SunoAI/comments/1xemdur/google_gemini_music_is_getting_close/)；2026-10，Gemini 音乐体验，具体聊天模型未说明 | 原作者赞赏音质但认为细节难以调整；评论者对音质看法相反。 | 主观反馈且版本、模型与后端未充分说明；不能据此判断聊天 Argon 会切换音乐后端。 |

[另一创意写作讨论](https://www.reddit.com/r/WritingWithAI/comments/1wv1rvs/gemini_4_argon_is_expected_to_be_a_powerhouse_in/)的原帖主要是发布前期待，不能作为 Argon 已验证表现的证据；只有明确描述实际使用的评论才适合另行核查。

默认模型、思考参数兼容性和受控 Lite 回退实现及验证状态见[模型选择指南](models.md)。社区反馈不会替代对本项目可验证本地产物、来源或任务状态的检查。
