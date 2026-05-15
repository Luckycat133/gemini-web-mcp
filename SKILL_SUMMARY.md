# Gemini Skill - Best Practices Edition

## Architecture

### Design Principles

1. **Single Responsibility** - Each tool does one thing well
2. **Error Handling** - All functions have try/except
3. **Type Hints** - Full typing for better IDE support
4. **DRY** - Shared logic in helper functions
5. **Singleton Pattern** - PromptManager as single instance

### Code Structure

```python
# Imports
# Constants (MODEL_ALIASES, MEDIA_TYPES)
# MCP Server initialization
# Helper functions (_normalize_*)
# PromptManager class
# Tool functions (chat, create, edit, session, prompts, cookie)
# Response formatter (_format_response)
# Main entry point
```

### Key Patterns

**1. Alias Normalization**
```python
MODEL_ALIASES = {"f": "fast", "t": "thinking", "p": "pro"}
```

**2. Singleton PromptManager**
```python
_prompt_manager: Optional[PromptManager] = None
def get_prompts() -> PromptManager:
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager(PROMPTS_FILE)
    return _prompt_manager
```

**3. Centralized Error Handling**
```python
try:
    # operation
except Exception as e:
    logger.error(f"Tool error: {e}")
    return [TextContent(type="text", text=f"Error: {e}")]
```

**4. Response Formatting**
```python
def _format_response(response: Any, media_type: str = "") -> list[TextContent]:
    parts = []
    if response.text:
        parts.append(response.text)
    # ... media handling
    return [TextContent(type="text", text="".join(parts))]
```

## Best Practices Applied

| Practice | Implementation |
|----------|---------------|
| Type hints | All functions typed |
| Docstrings | Short, clear descriptions |
| Error handling | Try/except with logging |
| Logging | Structured, configurable |
| Path handling | pathlib.Path |
| Config | Environment variables |
| Constants | Typed dictionaries |
| Imports | Explicit, grouped |
| Code style | Black-compatible |

## Metrics

| Metric | Value |
|--------|-------|
| Tools | 6 |
| Instructions | ~40 tokens |
| Lines | ~450 |
| Functions | 20+ |
| Classes | 1 |
| Testable | ✓ |
| Type-safe | ✓ |

## Files

- `src/skill_server.py` - Main server
- `prompts_default.json` - Default prompts
- `SKILL_README.md` - Usage docs
- `SKILL_SUMMARY.md` - This file
