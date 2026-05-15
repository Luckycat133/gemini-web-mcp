# Gemini Skill - Optimized Edition

Low-token MCP server for AI use.

## Quick Start

```json
{
  "mcpServers": {
    "gemini": {
      "command": "python",
      "args": ["-m", "uv", "run", "--directory", "/path/to/gemini-mcp-server", "src/skill_server.py"],
      "env": {
        "GEMINI_PSID": "your_cookie"
      }
    }
  }
}
```

## Tools (6 total)

1. **ask** - Chat with Gemini
   - `message`: What to say
   - `model`: fast|thinking|pro (default: fast)
   - `image_path`: Optional image
   - `session_id`: Optional conversation

2. **media** - Generate images/videos/music
   - `prompt`: What to create
   - `type`: image|video|music
   - `model`: fast|thinking|pro
   - `image_path`: Optional reference

3. **edit** - Edit images
   - `image_path`: Image to edit
   - `prompt`: What changes to make
   - `model`: fast|thinking|pro

4. **session** - Multi-turn conversations
   - `action`: create|send|list|reset
   - `session_id`: Session ID
   - `message`: Message to send
   - `model`: Model for create
   - `image_path`: Optional image

5. **prompts** - Manage preset library
   - `action`: list|get|create|delete
   - `name`: Prompt name
   - `content`: Prompt content
   - `category`: Optional category

6. **cookie** - Cookie management
   - `action`: status|get
   - `browser`: chrome|firefox|edge

## Usage Examples

### Basic Chat
```
ask(message="Hello, how are you?")
```

### Code with Thinking Model
```
ask(message="Fix this code", model="thinking")
```

### Generate Image
```
media(prompt="a cute cat", type="image", model="pro")
```

### Edit Image
```
edit(image_path="/path/photo.jpg", prompt="change to sunset")
```

### Conversation
```
session(action="create", model="thinking")
session(action="send", session_id="sess_1", message="Hello")
session(action="list")
session(action="reset")
```

### Preset Prompts
```
prompts(action="list")
prompts(action="get", name="Code Review")
prompts(action="create", name="Custom", content="...", category="coding")
```

## Models

- **fast**: Quick responses (default)
- **thinking**: Chain-of-thought reasoning
- **pro**: Best quality (AI Plus)

## Environment Variables

| Variable | Required | Default |
|----------|----------|---------|
| GEMINI_PSID | Yes | - |
| GEMINI_PSIDTS | No | - |
| GEMINI_SESSIONS_DIR | No | ./.gemini_sessions |

## Comparing to Full Version

| Feature | Full | Skill |
|---------|------|-------|
| Tools | 15+ | 6 |
| Instructions | 300+ tokens | ~100 tokens |
| Docs | Complete | Minimal |
| Tool names | Descriptive | Short |

All core functionality preserved.
