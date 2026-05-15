# Gemini Skill (v3.0)

Low-token MCP server for AI use.

## Tools (6)

| Tool | Purpose |
|------|---------|
| **chat** | Gemini conversation with images, sessions |
| **create** | Generate image/video/music |
| **edit** | Modify existing images |
| **session** | Conversation context management |
| **prompts** | Saved prompt templates |
| **cookie** | Auth management |

## Models
- **fast**: quick
- **thinking**: reasoning
- **pro**: best quality

## Usage

```
chat(message="Hello", model="fast")
chat(message="Fix code", model="thinking", image_path="/path.jpg")

create(prompt="a cat", type="image", model="pro")
create(prompt="music", type="music", model="thinking")

edit(image_path="/path/photo.jpg", prompt="make it sunset")

session(action="create", model="thinking")
session(action="send", session_id="sess_1", message="Hello")

prompts(action="list")
prompts(action="get", name="Code Review")

cookie(action="status")
cookie(action="get", browser="chrome")
```

## Default Prompts
Code Review, Python Optimize, Bug Fix, Summarize, Translate

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

## Compare
| Metric | Full | Skill |
|--------|------|-------|
| Tools | 20+ | 6 |
| Instructions | 300+ | ~50 |
