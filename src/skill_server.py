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
from .tools.annotations import (
    DESTRUCTIVE_LOCAL,
    DESTRUCTIVE_REMOTE,
    MUTATES_LOCAL,
    MUTATES_REMOTE,
    READS_PRIVATE_REMOTE,
)
from .tools.utils import extract_remote_chat_id
from .tools.manage import (
    WEB_FEATURE_PROBES,
    _RawRPCData,
    _chat_export_payload,
    _chat_to_dict,
    _execute_observed_rpc,
    _extract_rpc_bodies,
    _format_chat_export_markdown,
    _format_web_capabilities_markdown,
    _get_chat_id,
    _get_probe,
    _read_chat_turns,
    _turn_matches_query,
    _parse_library_capability,
    _parse_public_link_entry,
    _parse_scheduled_action_entry,
    _parse_tool_mode_entry,
    _parse_usage_entry,
    _format_tool_manifest_markdown,
    _summarize_probe_response,
    _tool_manifest_payload,
    _web_capabilities_payload,
)

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
- **history**: remote Gemini chat history
- **account**: account, models, tool manifest, web capabilities, feature probes, links, usage, library, scheduled actions, modes
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


def _truncate_text(text: Any, max_chars: int = 2000) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


@mcp.tool(annotations=MUTATES_REMOTE)
async def chat(
    message: str,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    image_path: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[TextContent]:
    """Chat with Gemini - supports images and sessions."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)
        model_name = resolve_model_name(model)
        files = [image_path] if image_path else None

        if session_id and session_id in _sessions:
            request_kwargs = {
                "prompt": message,
                "files": files,
                "thinking_level": thinking_level,
            }
            use_learning_mode = learning_mode or _sessions[session_id].get("learning_mode")
            if use_learning_mode:
                request_kwargs["learning_mode"] = use_learning_mode
            response = await _sessions[session_id]["session"].send_message(**request_kwargs)
        else:
            request_kwargs = {
                "prompt": message,
                "files": files,
                "model": model_name,
                "thinking_level": thinking_level,
            }
            if learning_mode:
                request_kwargs["learning_mode"] = learning_mode
            response = await client.generate_content(**request_kwargs)

        return _format_response(response)

    except Exception as e:
        logger.error(f"Chat error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
async def history(
    action: Literal["list", "search", "read", "export", "delete"],
    chat_id: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    scan_turns: bool = False,
) -> list[TextContent]:
    """Manage remote Gemini Web chat history."""
    try:
        client = get_gemini_client()
        await initialize_client()
        safe_limit = min(max(limit, 1), 50)

        if action == "list":
            chats = client.list_chats() if hasattr(client, "list_chats") else []
            chats = chats or []
            page = chats[max(offset, 0) : max(offset, 0) + safe_limit]
            if not page:
                return [TextContent(type="text", text="No chats")]
            lines = []
            for i, item in enumerate(page, max(offset, 0) + 1):
                title = getattr(item, "title", "Untitled")
                cid = getattr(item, "cid", "") or getattr(item, "id", "")
                lines.append(f"{i}. {title} ({cid})")
            if max(offset, 0) + len(page) < len(chats):
                lines.append(f"next_offset={max(offset, 0) + len(page)}")
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "search":
            needle = (query or "").strip()
            if not needle:
                return [TextContent(type="text", text="query required")]
            if scan_turns and not hasattr(client, "read_chat"):
                return [TextContent(type="text", text="read_chat unavailable")]
            chats = client.list_chats() if hasattr(client, "list_chats") else []
            chats = chats or []
            page = chats[max(offset, 0) : max(offset, 0) + safe_limit]
            lowered = needle.lower()
            lines = []
            for item in page:
                chat = _chat_to_dict(item)
                matched = lowered in chat["title"].lower() or lowered in chat["id"].lower()
                snippets = []
                if scan_turns and chat["id"]:
                    _history, turns = await _read_chat_turns(client, chat["id"], min(safe_limit, 20), 1000)
                    for idx, turn in enumerate(turns, 1):
                        if _turn_matches_query(turn, needle):
                            matched = True
                            snippets.append(f"turn {idx} {turn['role']}: {_truncate_text(turn['text'], 240)}")
                if matched:
                    lines.append(f"{chat['title']} ({chat['id']})")
                    lines.extend(f"  {snippet}" for snippet in snippets[:3])
            if max(offset, 0) + len(page) < len(chats):
                lines.append(f"next_offset={max(offset, 0) + len(page)}")
            return [TextContent(type="text", text="\n".join(lines) if lines else "No matches")]

        if action == "read":
            if not chat_id:
                return [TextContent(type="text", text="chat_id required")]
            if not hasattr(client, "read_chat"):
                return [TextContent(type="text", text="read_chat unavailable")]
            chat = await client.read_chat(chat_id, limit=safe_limit)
            turns = getattr(chat, "turns", []) if chat else []
            if not turns:
                return [TextContent(type="text", text="No turns")]
            lines = []
            for turn in turns[:safe_limit]:
                role = getattr(turn, "role", "unknown")
                text = _truncate_text(getattr(turn, "text", ""))
                lines.append(f"{role}: {text}")
            return [TextContent(type="text", text="\n\n".join(lines))]

        if action == "export":
            if not chat_id:
                return [TextContent(type="text", text="chat_id required")]
            if not hasattr(client, "read_chat"):
                return [TextContent(type="text", text="read_chat unavailable")]
            safe_export_limit = min(max(limit, 1), 200)
            chat, turns = await _read_chat_turns(client, chat_id, safe_export_limit, 20000)
            metadata = {"id": chat_id}
            if hasattr(client, "list_chats"):
                for item in client.list_chats() or []:
                    if _get_chat_id(item) == chat_id:
                        metadata = _chat_to_dict(item)
                        break
            payload = _chat_export_payload(chat_id, chat, turns, metadata, safe_export_limit, 20000)
            return [TextContent(type="text", text=_format_chat_export_markdown(payload))]

        if action == "delete":
            if not chat_id:
                return [TextContent(type="text", text="chat_id required")]
            if not hasattr(client, "delete_chat"):
                return [TextContent(type="text", text="delete_chat unavailable")]
            await client.delete_chat(chat_id)
            return [TextContent(type="text", text=f"Deleted: {chat_id}")]

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        logger.error(f"History error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool(annotations=READS_PRIVATE_REMOTE)
async def account(
    action: Literal["status", "models", "manifest", "capabilities", "features", "links", "usage", "library", "scheduled", "modes"] = "status",
) -> list[TextContent]:
    """Inspect Gemini account status and available models."""
    try:
        client = get_gemini_client()
        await initialize_client()

        if action == "models":
            models = client.list_models() if hasattr(client, "list_models") else []
            if not models:
                return [TextContent(type="text", text="No models")]
            lines = []
            for model in models:
                display = getattr(model, "display_name", "") or "Unnamed"
                name = getattr(model, "model_name", "") or "unknown"
                available = "available" if getattr(model, "is_available", True) else "unavailable"
                lines.append(f"{display}: {name} ({available})")
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "capabilities":
            return [
                TextContent(
                    type="text",
                    text=_format_web_capabilities_markdown(_web_capabilities_payload()),
                )
            ]

        if action == "manifest":
            return [
                TextContent(
                    type="text",
                    text=_format_tool_manifest_markdown(_tool_manifest_payload("all")),
                )
            ]

        if action == "features":
            if not hasattr(client, "_batch_execute"):
                return [TextContent(type="text", text="feature probes unavailable")]
            lines = []
            for probe in WEB_FEATURE_PROBES:
                try:
                    response = await client._batch_execute(
                        [_RawRPCData(probe["rpcid"], probe["payload"])],
                        source_path=probe["source_path"],
                        close_on_error=False,
                    )
                    summary = _summarize_probe_response(response.text, probe["rpcid"])
                    ok = response.status_code == 200 and summary.get("reject_code") is None
                    status = "ok" if ok else f"reject={summary.get('reject_code')}"
                    lines.append(f"{probe['surface']}.{probe['name']}: {status}")
                except Exception as e:
                    lines.append(f"{probe['surface']}.{probe['name']}: {type(e).__name__}")
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "links":
            probe = _get_probe("sharing", "public_links_index")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = bodies[0] if bodies and isinstance(bodies[0], list) else []
            links = [_parse_public_link_entry(item) for item in entries[:20]]
            if not links:
                return [TextContent(type="text", text="No public links")]
            lines = [
                f"{item.get('title') or '(untitled)'} ({item.get('id', '')}) {item.get('url', '')}".strip()
                for item in links
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "usage":
            lines = []
            for probe_name in ("usage_quota", "usage_model_state"):
                probe = _get_probe("usage", probe_name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                entries = []
                if bodies and isinstance(bodies[0], list) and bodies[0]:
                    first = bodies[0][0]
                    if isinstance(first, list):
                        entries = [_parse_usage_entry(item) for item in first]
                for item in entries:
                    lines.append(
                        f"{probe_name}: key={item.get('key')} limit={item.get('limit_value')} remaining={item.get('remaining_value')}"
                    )
            return [TextContent(type="text", text="\n".join(lines) or "No usage entries")]

        if action == "library":
            probe = _get_probe("library", "library_locale_capabilities")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            entries = []
            if bodies and isinstance(bodies[0], list) and bodies[0]:
                first = bodies[0][0]
                if isinstance(first, list):
                    entries = [_parse_library_capability(item) for item in first]
            if not entries:
                return [TextContent(type="text", text="No library capabilities")]
            lines = [
                f"{item.get('name') or ', '.join(item.get('aliases', []))}: {item.get('description', '')}".strip()
                for item in entries
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "scheduled":
            lines = []
            for probe_name in ("scheduled_actions_active", "scheduled_actions_inactive"):
                probe = _get_probe("scheduled", probe_name)
                response = await _execute_observed_rpc(client, probe)
                bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
                body = bodies[0] if bodies else []
                entries = []
                if isinstance(body, list) and len(body) > 2 and isinstance(body[2], list):
                    entries = [_parse_scheduled_action_entry(item, 500) for item in body[2][:20]]
                for item in entries:
                    when = f" {item.get('scheduled_time')}" if item.get("scheduled_time") else ""
                    lines.append(f"{probe_name}: {item.get('title') or '(untitled)'} ({item.get('id', '')}){when}".strip())
            return [TextContent(type="text", text="\n".join(lines) or "No scheduled actions")]

        if action == "modes":
            probe = _get_probe("tool_modes", "tool_mode_status")
            response = await _execute_observed_rpc(client, probe)
            bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
            body = bodies[0] if bodies else []
            entries = []
            if isinstance(body, list) and len(body) > 1 and isinstance(body[1], list):
                entries = [_parse_tool_mode_entry(item) for item in body[1]]
            if not entries:
                return [TextContent(type="text", text="No mode status entries")]
            lines = [
                f"mode_id={item.get('mode_id')} available={item.get('available')} quota={item.get('quota_value')} state={item.get('state')}"
                for item in entries
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        if not hasattr(client, "inspect_account_status"):
            return [TextContent(type="text", text="account inspection unavailable")]
        status = await client.inspect_account_status()
        summary = status.get("summary", {}) if isinstance(status, dict) else {}
        if not summary:
            return [TextContent(type="text", text="Account status loaded")]
        lines = [f"{key}: {value}" for key, value in summary.items()]
        return [TextContent(type="text", text="\n".join(lines))]

    except Exception as e:
        logger.error(f"Account error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool(annotations=MUTATES_REMOTE)
async def create(
    prompt: str,
    type: Literal["image", "video", "music"] = "image",
    model: str = "flash",
    thinking_level: str = "standard",
    image_path: Optional[str] = None,
) -> list[TextContent]:
    """Generate image/video/music."""
    try:
        client = get_gemini_client()
        await initialize_client()

        model = _normalize_model(model)
        media_type = _normalize_media_type(type)
        media_request = resolve_media_request(model, media_type)
        model_name = media_request["request_model"]

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


@mcp.tool(annotations=MUTATES_REMOTE)
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


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
async def session(
    action: Literal["create", "send", "list", "reset"],
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
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
                "learning_mode": learning_mode,
            }
            return [TextContent(type="text", text=f"Session created: {sid}")]

        elif action == "send":
            if not session_id or session_id not in _sessions:
                return [
                    TextContent(type="text", text=f"Invalid session: {session_id}")
                ]

            request_kwargs = {
                "prompt": message or "",
                "files": [image_path] if image_path else None,
                "thinking_level": _sessions[session_id].get(
                    "thinking_level",
                    thinking_level,
                ),
            }
            use_learning_mode = learning_mode or _sessions[session_id].get("learning_mode")
            if use_learning_mode:
                request_kwargs["learning_mode"] = use_learning_mode
            response = await _sessions[session_id]["session"].send_message(**request_kwargs)
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


@mcp.tool(annotations=DESTRUCTIVE_LOCAL)
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


@mcp.tool(annotations=MUTATES_LOCAL)
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

    remote_chat_id = extract_remote_chat_id(response)
    if remote_chat_id:
        parts.append(f"\n\nRemote chat ID: {remote_chat_id}")

    return [TextContent(type="text", text="".join(parts))]


def main() -> None:
    """Run the server."""
    _init_default_prompts()
    mcp.run()


if __name__ == "__main__":
    main()
