# Gemini Skill (v3.0)

Production-ready, low-token MCP server.

## Setup

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

## Tools

| Tool | Purpose |
|------|---------|
| **chat** | Conversation + images + sessions |
| **create** | Generate image/video/music |
| **edit** | Modify existing images |
| **session** | Conversation history |
| **prompts** | Saved templates |
| **cookie** | Authentication |

## Models

`fast` (default) / `thinking` / `pro`

## Usage

```
chat(message="hi")
chat(message="fix", model="thinking")
chat(message="describe", image_path="/img.jpg")
chat(message="continue", session_id="sess_1")

create(prompt="cat", type="image")
create(prompt="video", type="video")
create(prompt="song", type="music")

edit(image_path="/photo.jpg", prompt="sunset")

session(action="create")
session(action="send", session_id="sess_1", message="hi")
session(action="list")
session(action="reset")

prompts(action="list")
prompts(action="get", name="Code Review")
prompts(action="create", name="Custom", content="...")

cookie(action="status")
cookie(action="get")
```

## Aliases

```
Model: f/t/p or flash/thinking/pro
Media: img/picture/photo → image
```

## Config

| Variable | Default |
|----------|---------|
| GEMINI_PSID | required |
| GEMINI_CONFIG_DIR | .gemini |

## Defaults (8)

Code Review, Python Optimize, Bug Fix, Summarize, Translate, Image Prompt, Writing Improve, Explain Simply
