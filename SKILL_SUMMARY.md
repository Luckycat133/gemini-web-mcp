# Gemini Skill - Optimization Summary

## Created Files

- `src/skill_server.py` - Optimized server (6 tools)
- `prompts_default.json` - 8 preset prompts
- `SKILL_README.md` - Quick reference

## Tool Naming (1-2 words)

| Tool | Purpose |
|------|---------|
| **chat** | Gemini conversation with images, sessions |
| **create** | Generate image/video/music |
| **edit** | Modify existing images |
| **session** | Conversation context management |
| **prompts** | Saved prompt templates |
| **cookie** | Auth management |

## Optimization Results

| Metric | Full Version | Skill Version | Reduction |
|--------|-------------|---------------|-----------|
| Tools | 20+ | 6 | 70% |
| Instructions | 300+ tokens | ~50 tokens | 83% |
| Function names | Long descriptive | 1-2 words | ✓ |

## Features Preserved

✓ Chat with images  
✓ All 3 models (fast/thinking/pro)  
✓ Session management  
✓ Image/video/music generation  
✓ Image editing  
✓ Preset prompts (8 default)  
✓ Cookie management  

## Quick Setup

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "src/skill_server.py"],
      "env": {"GEMINI_PSID": "your_cookie"}
    }
  }
}
```

## Default Prompts (8)
Code Review, Python Optimize, Bug Fix, Summarize, Translate, Image Prompt, Writing Improve, Explain Simply

## Usage Examples

```
chat(message="Hello")
chat(message="Analyze code", model="thinking")
chat(message="Describe this", image_path="/path.jpg")

create(prompt="a cat", type="image")
create(prompt="music", type="music")

edit(image_path="/path/photo.jpg", prompt="make it sunset")

session(action="create")
session(action="send", session_id="sess_1", message="Hello")

prompts(action="list")
prompts(action="get", name="Code Review")

cookie(action="status")
```
