#!/usr/bin/env python3
"""
Gemini Skill - Optimized MCP Server (v3.0)
Low-token, production-ready.
"""

import os
import json
import shutil
import logging
from pathlib import Path
from typing import Optional, Literal, Any

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import TextContent
except ImportError:
    print("Error: mcp package required. Install with: pip install mcp fastmcp")
    exit(1)

from .client_wrapper import (
    get_gemini_client,
    initialize_client,
    reset_client,
    get_cookie_status,
    get_cookie_from_browser,
)
from .constants import resolve_media_request, resolve_model_name

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("GEMINI_CONFIG_DIR", ".gemini"))
PROMPTS_FILE = CONFIG_DIR / "prompts.json"
DEFAULT_PROMPTS_FILE = Path(__file__).parent.parent / "prompts_default.json"

MODEL_ALIASES = {
    "l": "flash-lite",
    "f": "flash",
    "t": "thinking",
    "p": "pro",
    "lite": "flash-lite",
    "pro": "pro",
}

MEDIA_TYPES = {"img": "image", "picture": "image", "photo": "image"}

mcp = FastMCP(
    "Gemini",
    instructions="""
# Gemini Skill

## Tools
- **chat**: conversation
- **create**: generate media
- **edit**: modify images
- **session**: conversation history
- **cookie**: authentication helper

## Models
- flash-lite, flash (default), pro
- thinking_level: standard or extended

## Media behavior
- image: always Nano Banana 2 on first generation
- music: flash series -> Lyria 3, pro -> Lyria 3 Pro

## Quick
chat(message="hi")
create(prompt="image", type="image")
""",
)


def _normalize_model(model: str) -> str:
    """Normalize model alias to standard name."""
    return MODEL_ALIASES.get(model.lower(), model)


def _normalize_media_type(media_type: str) -> str:
    """Normalize media type alias."""
    return MEDIA_TYPES.get(media_type.lower(), media_type)


def _ensure_config_dir() -> None:
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(exist_ok=True)


def _init_default_prompts() -> None:
    """Initialize with default prompts if none exist."""
    _ensure_config_dir()
    if not PROMPTS_FILE.exists() and DEFAULT_PROMPTS_FILE.exists():
        shutil.copy(DEFAULT_PROMPTS_FILE, PROMPTS_FILE)
        logger.info("Initialized default prompts")


class PromptManager:
    """Simple prompt storage manager."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load prompts from file."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._data = data.get("prompts", {})
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load prompts: {e}")
                self._data = {}

    def _save(self) -> None:
        """Save prompts to file."""
        _ensure_config_dir()
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": "1.0", "prompts": self._data},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except IOError as e:
            logger.error(f"Failed to save prompts: {e}")

    def list_all(self) -> list[dict]:
        """List all prompts."""
        return sorted(self._data.values(), key=lambda x: x.get("name", "").lower())

    def get_by_name(self, name: str) -> Optional[dict]:
        """Get prompt by name."""
        for p in self._data.values():
            if p.get("name", "").lower() == name.lower():
                return p
        return None

    def create(self, name: str, content: str, category: str = "general") -> str:
        """Create new prompt."""
        prompt_id = name.lower().replace(" ", "_")
        self._data[prompt_id] = {
            "id": prompt_id,
            "name": name,
            "content": content,
            "category": category,
        }
        self._save()
        return prompt_id

    def delete(self, name: str) -> bool:
        """Delete prompt by name."""
        prompt = self.get_by_name(name)
        if prompt:
            del self._data[prompt["id"]]
            self._save()
            return True
        return False


_prompt_manager: Optional[PromptManager] = None


def get_prompts() -> PromptManager:
    """Get singleton prompt manager."""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager(PROMPTS_FILE)
    return _prompt_manager


_sessions: dict[str, dict[str, Any]] = {}


@mcp.tool()
async def chat(
    message: str,
    model: str = "flash",
    thinking_level: str = "standard",
    image_path: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[TextContent]:
    """Chat with Gemini - supports images and sessions."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)
        media_request = resolve_media_request(model, media_type)
        model_name = media_request["request_model"]
        files = [image_path] if image_path else None

        if session_id and session_id in _sessions:
            response = await _sessions[session_id]["session"].send_message(
                prompt=message,
                files=files,
                thinking_level=thinking_level,
            )
        else:
            response = await client.generate_content(
                prompt=message,
                files=files,
                model=model_name,
                thinking_level=thinking_level,
            )

        return _format_response(response)

    except Exception as e:
        logger.error(f"Chat error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool()
async def create(
    prompt: str,
    type: Literal["image", "video", "music"] = "image",
    model: str = "fast",
    thinking_level: str = "standard",
    image_path: Optional[str] = None,
) -> list[TextContent]:
    """Generate image/video/music."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)
        media_type = _normalize_media_type(type)
        model_name = resolve_model_name(model)

        prefixes = {
            "image": "Generate image: ",
            "video": "Generate video: ",
            "music": "Create music: ",
        }
        media_prompt = prefixes.get(media_type, "") + prompt
        files = [image_path] if image_path else None

        response = await client.generate_content(
            prompt=media_prompt,
            files=files,
            model=model_name,
            thinking_level=thinking_level,
        )

        return _format_response(
            response,
            media_type,
            backend_label=media_request["backend_label"],
            backend_note=media_request["note"],
        )

    except Exception as e:
        logger.error(f"Create error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool()
async def edit(
    image_path: str,
    prompt: str,
    model: str = "flash",
    thinking_level: str = "standard",
) -> list[TextContent]:
    """Edit existing image."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)
        model_name = resolve_model_name(model)

        response = await client.generate_content(
            prompt=f"Edit this image: {prompt}",
            files=[image_path],
            model=model_name,
            thinking_level=thinking_level,
        )

        return _format_response(response, "image")

    except Exception as e:
        logger.error(f"Edit error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool()
async def session(
    action: Literal["create", "send", "list", "reset"],
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    model: str = "fast",
    thinking_level: str = "standard",
    image_path: Optional[str] = None,
) -> list[TextContent]:
    """Manage conversation sessions."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)

        if action == "create":
            sess = client.start_chat(model=resolve_model_name(model))
            sid = f"sess_{len(_sessions) + 1}"
            _sessions[sid] = {
                "session": sess,
                "model": model,
                "thinking_level": thinking_level,
            }
            return [TextContent(type="text", text=f"Session created: {sid}")]

        elif action == "send":
            if not session_id or session_id not in _sessions:
                return [
                    TextContent(type="text", text=f"Invalid session: {session_id}")
                ]

            response = await _sessions[session_id]["session"].send_message(
                prompt=message or "",
                files=[image_path] if image_path else None,
                thinking_level=_sessions[session_id].get("thinking_level", thinking_level),
            )
            return _format_response(response)

        elif action == "list":
            if not _sessions:
                return [TextContent(type="text", text="No active sessions")]
            items = [
                f"{i}. {sid} ({data['model']})"
                for i, (sid, data) in enumerate(_sessions.items(), 1)
            ]
            return [TextContent(type="text", text="\n".join(items))]

        elif action == "reset":
            if session_id and session_id in _sessions:
                del _sessions[session_id]
                return [
                    TextContent(type="text", text=f"Session deleted: {session_id}")
                ]
            _sessions.clear()
            reset_client()
            return [TextContent(type="text", text="All sessions reset")]

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        logger.error(f"Session error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool()
async def prompts(
    action: Literal["list", "get", "create", "delete"],
    name: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
) -> list[TextContent]:
    """Manage saved prompts."""
    try:
        mgr = get_prompts()

        if action == "list":
            items = mgr.list_all()
            if not items:
                return [TextContent(type="text", text="No prompts")]
            lines = [f"{i}. {p['name']}" for i, p in enumerate(items, 1)]
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "get":
            if not name:
                return [TextContent(type="text", text="Name required")]
            prompt = mgr.get_by_name(name)
            if prompt:
                return [
                    TextContent(
                        type="text",
                        text=f"{prompt['name']}\n---\n{prompt['content']}",
                    )
                ]
            return [TextContent(type="text", text="Not found")]

        elif action == "create":
            if not name or not content:
                return [
                    TextContent(type="text", text="Name and content required")
                ]
            mgr.create(name, content, category or "general")
            return [TextContent(type="text", text=f"Created: {name}")]

        elif action == "delete":
            if not name:
                return [TextContent(type="text", text="Name required")]
            if mgr.delete(name):
                return [TextContent(type="text", text=f"Deleted: {name}")]
            return [TextContent(type="text", text="Not found")]

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        logger.error(f"Prompts error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool()
async def cookie(
    action: Literal["status", "get"],
    browser: Literal["chrome", "firefox", "edge"] = "chrome",
) -> list[TextContent]:
    """Manage authentication cookies."""
    try:
        if action == "status":
            status = get_cookie_status()
            return [
                TextContent(
                    type="text",
                    text=f"Cookie: {'OK' if status.get('has_cookie') else 'Missing'}",
                )
            ]

        elif action == "get":
            success = get_cookie_from_browser(browser)
            return [
                TextContent(
                    type="text",
                    text=f"Cookie: {'Loaded' if success else 'Failed'}",
                )
            ]

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        logger.error(f"Cookie error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


def _format_response(
    response: Any,
    media_type: str = "",
    backend_label: str | None = None,
    backend_note: str | None = None,
) -> list[TextContent]:
    """Format Gemini response to TextContent."""
    parts = []

    if response.text:
        parts.append(response.text)

    if hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            if hasattr(img, "url") and img.url:
                parts.append(f"[Image {i}]: {img.url}")

    if hasattr(response, "videos") and response.videos:
        for i, vid in enumerate(response.videos, 1):
            if hasattr(vid, "url") and vid.url:
                parts.append(f"[Video {i}]: {vid.url}")

    if hasattr(response, "audio_url") and response.audio_url:
        parts.append(f"[Audio]: {response.audio_url}")

    if backend_label:
        prefix = f"Backend: {backend_label}"
        if backend_note:
            prefix += f"\n{backend_note}"
        parts.insert(0, prefix + "\n")

    return [TextContent(type="text", text="".join(parts))]


def main() -> None:
    """Run the server."""
    _init_default_prompts()
    mcp.run()


if __name__ == "__main__":
    main()
