#!/usr/bin/env python3
"""
Gemini Skill - Optimized MCP Server (v3.0)
Low-token, production-ready.
"""

import os
import json
import asyncio
import shutil
import logging
import threading
from pathlib import Path
from typing import Optional, Literal, Any, Callable, Awaitable

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import TextContent
except ImportError:
    print("Error: mcp package required. Install with: pip install mcp fastmcp")
    exit(1)

from .client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    reset_client,
    get_cookie_status,
    get_cookie_from_browser,
    list_browser_cookie_profiles,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
)
from .constants import resolve_media_request, resolve_model_name
from .tools.annotations import (
    DESTRUCTIVE_LOCAL,
    DESTRUCTIVE_REMOTE,
    MUTATES_LOCAL,
    MUTATES_REMOTE,
    READ_ONLY_LOCAL,
    READS_PRIVATE_REMOTE,
)
from .tools.utils import extract_remote_chat_id, validate_optional_image_path
from .tools.manage import (
    WEB_FEATURE_PROBES,
    _RawRPCData,
    _chat_export_payload,
    _chat_to_dict,
    _execute_observed_rpc,
    _fetch_native_notebooks,
    _extract_rpc_bodies,
    _fetch_scheduled_registry,
    _fetch_scheduled_task_by_id,
    _cleanup_test_artifacts_payload,
    _doctor_payload,
    _format_cleanup_markdown,
    _format_chat_export_markdown,
    _format_doctor_markdown,
    _format_web_capabilities_markdown,
    _get_chat_id,
    _get_probe,
    _read_chat_turns,
    _turn_matches_query,
    _parse_library_capability,
    _parse_public_link_entry,
    _parse_scheduled_action_entry,
    _parse_scheduled_action_create_body,
    _parse_scheduled_action_task_entry,
    _parse_tool_mode_entry,
    _parse_usage_entry,
    _paginate_items,
    _scheduled_daily_payload,
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
- **account**: account, models, tool manifest, web capabilities, feature probes, links, usage, library, native notebooks, scheduled actions, modes
- **scheduled**: list, get by id, create daily, or delete scheduled actions
- **cookie**: authentication helper
- **doctor**: local preflight diagnostics
- **cleanup**: dry-run or delete test artifacts by marker

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
_sessions_lock = threading.Lock()


def _truncate_text(text: Any, max_chars: int = 2000) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


def _schedule_skill_response_cleanup(response: Any, source: str, session: Any = None) -> None:
    """Mirror the primary MCP server's default remote-chat cleanup behavior."""
    cid = schedule_remote_chat_cleanup_from_response(response, source=source)
    if not cid and session is not None:
        schedule_remote_chat_cleanup(getattr(session, "cid", None), source=source)


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
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return [TextContent(type="text", text=f"Error: {image_error}")]

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)

        model = _normalize_model(model)
        model_name = resolve_model_name(model)
        files = [safe_image_path] if safe_image_path else None

        if session_id:
            with _sessions_lock:
                session_entry = _sessions.get(session_id)
        else:
            session_entry = None

        if session_id and session_entry:
            request_kwargs = {
                "prompt": message,
                "files": files,
                "thinking_level": thinking_level,
            }
            use_learning_mode = learning_mode or session_entry.get("learning_mode")
            if use_learning_mode:
                request_kwargs["learning_mode"] = use_learning_mode
            response = await session_entry["session"].send_message(**request_kwargs)
            _schedule_skill_response_cleanup(response, "skill_chat:session", session_entry["session"])
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
            _schedule_skill_response_cleanup(response, "skill_chat")

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

        if action == "list":
            chats = client.list_chats() if hasattr(client, "list_chats") else []
            chats = chats or []
            page, pagination = _paginate_items(chats, limit, offset, max_limit=50)
            if not page:
                return [TextContent(type="text", text="No chats")]
            lines = []
            for i, item in enumerate(page, pagination["offset"] + 1):
                title = getattr(item, "title", "Untitled")
                cid = getattr(item, "cid", "") or getattr(item, "id", "")
                lines.append(f"{i}. {title} ({cid})")
            if pagination["has_more"]:
                lines.append(f"next_offset={pagination['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines))]

        if action == "search":
            needle = (query or "").strip()
            if not needle:
                return [TextContent(type="text", text="query required")]
            if scan_turns and not hasattr(client, "read_chat"):
                return [TextContent(type="text", text="read_chat unavailable")]
            chats = client.list_chats() if hasattr(client, "list_chats") else []
            chats = chats or []
            page, pagination = _paginate_items(chats, limit, offset, max_limit=50)
            lowered = needle.lower()
            lines = []
            for item in page:
                chat = _chat_to_dict(item)
                matched = lowered in chat["title"].lower() or lowered in chat["id"].lower()
                snippets = []
                if scan_turns and chat["id"]:
                    _history, turns = await _read_chat_turns(client, chat["id"], min(pagination["limit"], 20), 1000)
                    for idx, turn in enumerate(turns, 1):
                        if _turn_matches_query(turn, needle):
                            matched = True
                            snippets.append(f"turn {idx} {turn['role']}: {_truncate_text(turn['text'], 240)}")
                if matched:
                    lines.append(f"{chat['title']} ({chat['id']})")
                    lines.extend(f"  {snippet}" for snippet in snippets[:3])
            if pagination["has_more"]:
                lines.append(f"next_offset={pagination['next_offset']}")
            return [TextContent(type="text", text="\n".join(lines) if lines else "No matches")]

        if action == "read":
            if not chat_id:
                return [TextContent(type="text", text="chat_id required")]
            if not hasattr(client, "read_chat"):
                return [TextContent(type="text", text="read_chat unavailable")]
            read_limit = _paginate_items([], limit, 0, max_limit=50)[1]["limit"]
            chat = await client.read_chat(chat_id, limit=read_limit)
            turns = getattr(chat, "turns", []) if chat else []
            if not turns:
                return [TextContent(type="text", text="No turns")]
            lines = []
            for turn in turns[:read_limit]:
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


async def _account_capabilities() -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=_format_web_capabilities_markdown(_web_capabilities_payload()),
        )
    ]


async def _account_manifest() -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=_format_tool_manifest_markdown(_tool_manifest_payload("all")),
        )
    ]


async def _account_models(client: Any) -> list[TextContent]:
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


async def _account_features(client: Any) -> list[TextContent]:
    if not hasattr(client, "_batch_execute"):
        return [TextContent(type="text", text="feature probes unavailable")]

    async def _probe_one(probe: dict[str, str]) -> str:
        try:
            response = await client._batch_execute(
                [_RawRPCData(probe["rpcid"], probe["payload"])],
                source_path=probe["source_path"],
                close_on_error=False,
            )
            summary = _summarize_probe_response(response.text, probe["rpcid"])
            ok = response.status_code == 200 and summary.get("reject_code") is None
            status = "ok" if ok else f"reject={summary.get('reject_code')}"
            return f"{probe['surface']}.{probe['name']}: {status}"
        except Exception as e:
            return f"{probe['surface']}.{probe['name']}: {type(e).__name__}"

    # Probe concurrently; gather preserves the WEB_FEATURE_PROBES order.
    lines = await asyncio.gather(*(_probe_one(probe) for probe in WEB_FEATURE_PROBES))
    return [TextContent(type="text", text="\n".join(lines))]


async def _account_links(client: Any) -> list[TextContent]:
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


async def _account_usage(client: Any) -> list[TextContent]:
    async def _probe_one(probe_name: str) -> list[str]:
        probe = _get_probe("usage", probe_name)
        response = await _execute_observed_rpc(client, probe)
        bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
        entries: list[dict[str, Any]] = []
        if bodies and isinstance(bodies[0], list) and bodies[0]:
            first = bodies[0][0]
            if isinstance(first, list):
                entries = [_parse_usage_entry(item) for item in first]
        return [
            f"{probe_name}: key={item.get('key')} limit={item.get('limit_value')} remaining={item.get('remaining_value')}"
            for item in entries
        ]

    # Probe concurrently; gather preserves the ("usage_quota", "usage_model_state") order.
    per_probe_lines = await asyncio.gather(
        *(_probe_one(name) for name in ("usage_quota", "usage_model_state"))
    )
    lines: list[str] = []
    for chunk in per_probe_lines:
        lines.extend(chunk)
    return [TextContent(type="text", text="\n".join(lines) or "No usage entries")]


async def _account_library(client: Any) -> list[TextContent]:
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


async def _account_notebooks(client: Any) -> list[TextContent]:
    notebooks, _diagnostic = await _fetch_native_notebooks(client)
    if not notebooks:
        return [TextContent(type="text", text="No native Gemini notebooks")]
    lines = [
        f"{item.get('title') or '(untitled)'} ({item.get('id', '')}) sources={item.get('source_count', 0)}".strip()
        for item in notebooks[:30]
    ]
    return [TextContent(type="text", text="\n".join(lines))]


async def _account_scheduled(client: Any) -> list[TextContent]:
    probe = _get_probe("scheduled", "scheduled_actions_registry")
    response = await _execute_observed_rpc(client, probe)
    bodies = _extract_rpc_bodies(response.text, probe["rpcid"])
    body = bodies[0] if bodies else []
    raw_entries = body[0] if isinstance(body, list) and body and isinstance(body[0], list) else []
    entries = [_parse_scheduled_action_task_entry(item, 500) for item in raw_entries[:20]]
    lines = []
    for item in entries:
        label = f" {item.get('schedule_label')}" if item.get("schedule_label") else ""
        lines.append(f"{item.get('title') or '(untitled)'} ({item.get('id', '')}){label}".strip())
    return [TextContent(type="text", text="\n".join(lines) or "No scheduled actions")]


async def _account_modes(client: Any) -> list[TextContent]:
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


async def _account_status(client: Any) -> list[TextContent]:
    if not hasattr(client, "inspect_account_status"):
        return [TextContent(type="text", text="account inspection unavailable")]
    status = await client.inspect_account_status()
    summary = status.get("summary", {}) if isinstance(status, dict) else {}
    if not summary:
        return [TextContent(type="text", text="Account status loaded")]
    lines = [f"{key}: {value}" for key, value in summary.items()]
    return [TextContent(type="text", text="\n".join(lines))]


# Auth-free actions (no client initialization needed).
_ACCOUNT_AUTH_FREE_ACTIONS: dict[str, Callable[[], Awaitable[list[TextContent]]]] = {
    "capabilities": _account_capabilities,
    "manifest": _account_manifest,
}

# Client-based actions; unknown action falls back to status.
_ACCOUNT_CLIENT_ACTIONS: dict[str, Callable[[Any], Awaitable[list[TextContent]]]] = {
    "models": _account_models,
    "features": _account_features,
    "links": _account_links,
    "usage": _account_usage,
    "library": _account_library,
    "notebooks": _account_notebooks,
    "scheduled": _account_scheduled,
    "modes": _account_modes,
    "status": _account_status,
}


@mcp.tool(annotations=READS_PRIVATE_REMOTE)
async def account(
    action: Literal["status", "models", "manifest", "capabilities", "features", "links", "usage", "library", "notebooks", "scheduled", "modes"] = "status",
) -> list[TextContent]:
    """Inspect Gemini account status and available models."""
    try:
        auth_free_handler = _ACCOUNT_AUTH_FREE_ACTIONS.get(action)
        if auth_free_handler is not None:
            return await auth_free_handler()

        client = get_gemini_client()
        await initialize_client()
        handler = _ACCOUNT_CLIENT_ACTIONS.get(action, _account_status)
        return await handler(client)
    except Exception as e:
        logger.error(f"Account error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _scheduled_list(client: Any) -> list[TextContent]:
    entries, diagnostic = await _fetch_scheduled_registry(client, 500)
    lines: list[str] = []
    for item in entries[:20]:
        label = f" {item.get('schedule_label')}" if item.get("schedule_label") else ""
        lines.append(f"{item.get('title') or '(untitled)'} ({item.get('id', '')}){label}".strip())
    if not lines and diagnostic.get("empty_hint"):
        lines.append("No scheduled actions")
        lines.append(f"Diagnostic: {diagnostic['empty_hint']}")
    return [TextContent(type="text", text="\n".join(lines) or "No scheduled actions")]


async def _scheduled_get(client: Any, action_id: str) -> list[TextContent]:
    clean_id = action_id.strip()
    if not clean_id:
        return [TextContent(type="text", text="action_id required")]
    item, diagnostic = await _fetch_scheduled_task_by_id(client, clean_id, 500)
    if not item:
        status = "not_found_or_wrong_account"
        if diagnostic.get("matched_task") is False:
            status = "not_readable_by_id"
        return [TextContent(type="text", text=f"Get: {clean_id} ({status})")]
    enabled = item.get("enabled")
    enabled_text = "enabled" if enabled is True else "disabled" if enabled is False else "unknown"
    state = f" state={item.get('task_state')}" if item.get("task_state") else ""
    title = item.get("title") or "(untitled)"
    label = f" {item.get('schedule_label')}" if item.get("schedule_label") else ""
    return [TextContent(type="text", text=f"Get: {title} ({item.get('id', clean_id)}) [{enabled_text}{state}]{label}")]


async def _scheduled_create(
    client: Any,
    title: str,
    instructions: str,
    hour: int,
    timezone_name: str,
) -> list[TextContent]:
    clean_title = title.strip()
    clean_instructions = instructions.strip()
    clean_timezone = timezone_name.strip()
    if not clean_title:
        return [TextContent(type="text", text="title required")]
    if not clean_instructions:
        return [TextContent(type="text", text="instructions required")]
    if hour < 0 or hour > 23:
        return [TextContent(type="text", text="hour must be 0..23")]
    payload = _scheduled_daily_payload(clean_title, clean_instructions, hour, clean_timezone or "Asia/Shanghai", "zh-CN")
    response = await client._batch_execute(
        [_RawRPCData("Jba3ib", payload)],
        source_path="/scheduled",
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, "Jba3ib")
    body = bodies[0] if bodies else []
    if isinstance(body, list) and body and isinstance(body[0], list):
        body = body[0]
    created = _parse_scheduled_action_create_body(body)
    created_id = created.get("id", "")
    visible = False
    readable_by_id = None
    verification_status = "not_attempted"
    if created_id:
        registry_entries, _ = await _fetch_scheduled_registry(client, 200)
        visible = any(item.get("id") == created_id for item in registry_entries)
        if visible:
            verification_status = "visible_in_registry"
        elif registry_entries:
            verification_status = "not_visible_in_nonempty_registry"
        else:
            verification_status = "registry_empty_unverified"
        task_by_id, _ = await _fetch_scheduled_task_by_id(client, created_id, 200)
        readable_by_id = task_by_id is not None
        if readable_by_id and verification_status == "registry_empty_unverified":
            verification_status = "readable_by_id_registry_empty"
        elif readable_by_id and verification_status == "not_visible_in_nonempty_registry":
            verification_status = "readable_by_id_not_visible_in_registry"
    suffix = "" if visible else f" ({verification_status}; verify account context)"
    return [TextContent(type="text", text=f"Created: {created_id or clean_title}{suffix}")]


async def _scheduled_delete(client: Any, action_id: str) -> list[TextContent]:
    clean_id = action_id.strip()
    if not clean_id:
        return [TextContent(type="text", text="action_id required")]
    payload = json.dumps([None, [clean_id]], ensure_ascii=False, separators=(",", ":"))
    response = await client._batch_execute(
        [_RawRPCData("Q4Gw3c", payload)],
        source_path="/scheduled",
        close_on_error=False,
    )
    bodies = _extract_rpc_bodies(response.text, "Q4Gw3c")
    verification_status = "rpc_accepted" if bodies else "rpc_unconfirmed"
    readable_by_id = None
    deleted_by_id = None
    if bodies:
        registry_entries, _ = await _fetch_scheduled_registry(client, 200)
        visible = any(item.get("id") == clean_id for item in registry_entries)
        if visible:
            verification_status = "still_visible_in_registry"
        elif registry_entries:
            verification_status = "not_visible_in_nonempty_registry"
        else:
            verification_status = "registry_empty_unverified"
        task_after_delete, _ = await _fetch_scheduled_task_by_id(client, clean_id, 200)
        readable_by_id = task_after_delete is not None
        deleted_by_id = bool(task_after_delete and task_after_delete.get("task_state_id") == 6)
        if deleted_by_id:
            verification_status = "deleted_state_by_id"
        elif readable_by_id:
            if verification_status == "registry_empty_unverified":
                verification_status = "registry_empty_active_or_unknown_by_id"
            elif verification_status == "not_visible_in_nonempty_registry":
                verification_status = "not_visible_active_or_unknown_by_id"
        elif verification_status == "registry_empty_unverified":
            verification_status = "registry_empty_not_readable_by_id"
        elif verification_status == "not_visible_in_nonempty_registry":
            verification_status = "not_visible_not_readable_by_id"
    return [TextContent(type="text", text=f"Delete requested: {clean_id} ({verification_status})")]


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
async def scheduled(
    action: Literal["list", "get", "create", "delete"] = "list",
    title: str = "",
    instructions: str = "",
    action_id: str = "",
    hour: int = 9,
    timezone_name: str = "Asia/Shanghai",
) -> list[TextContent]:
    """List, get by id, create daily, or delete Gemini Web scheduled actions."""
    try:
        client = get_gemini_client()
        await initialize_client()
        if not hasattr(client, "_batch_execute"):
            return [TextContent(type="text", text="scheduled actions unavailable")]

        if action == "list":
            return await _scheduled_list(client)
        if action == "get":
            return await _scheduled_get(client, action_id)
        if action == "create":
            return await _scheduled_create(client, title, instructions, hour, timezone_name)
        if action == "delete":
            return await _scheduled_delete(client, action_id)
        return [TextContent(type="text", text="Invalid action")]
    except Exception as e:
        logger.error(f"Scheduled action error: {e}")
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
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return [TextContent(type="text", text=f"Error: {image_error}")]

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)

        model = _normalize_model(model)
        media_type = _normalize_media_type(type)
        media_request = resolve_media_request(model, media_type, thinking_level)
        model_name = media_request["request_model"]

        prefixes = {
            "image": "Generate image: ",
            "video": "Generate video: ",
            "music": "Create music: ",
        }
        media_prompt = prefixes.get(media_type, "") + prompt
        files = [safe_image_path] if safe_image_path else None

        response = await client.generate_content(
            prompt=media_prompt,
            files=files,
            model=model_name,
            thinking_level=thinking_level,
        )
        _schedule_skill_response_cleanup(response, f"skill_create:{media_type}")

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
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return [TextContent(type="text", text=f"Error: {image_error}")]

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)

        model = _normalize_model(model)
        model_name = resolve_model_name(model)

        response = await client.generate_content(
            prompt=f"Edit this image: {prompt}",
            files=[safe_image_path],
            model=model_name,
            thinking_level=thinking_level,
        )
        _schedule_skill_response_cleanup(response, "skill_edit")

        return _format_response(response, "image")

    except Exception as e:
        logger.error(f"Edit error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


async def _session_create(
    model: str,
    thinking_level: str,
    learning_mode: Optional[str],
) -> list[TextContent]:
    client = get_gemini_client()
    await initialize_client()
    await cleanup_due_remote_chats(client)
    normalized = _normalize_model(model)
    sess = client.start_chat(model=resolve_model_name(normalized))
    with _sessions_lock:
        sid = f"sess_{len(_sessions) + 1}"
        _sessions[sid] = {
            "session": sess,
            "model": normalized,
            "thinking_level": thinking_level,
            "learning_mode": learning_mode,
        }
    return [TextContent(type="text", text=f"Session created: {sid}")]


async def _session_send(
    session_id: Optional[str],
    message: Optional[str],
    thinking_level: str,
    learning_mode: Optional[str],
    safe_image_path: Optional[str],
    model: str,
) -> list[TextContent]:
    with _sessions_lock:
        session_entry = _sessions.get(session_id) if session_id else None
    if not session_id or not session_entry:
        return [TextContent(type="text", text=f"Invalid session: {session_id}")]

    client = get_gemini_client()
    await initialize_client()
    await cleanup_due_remote_chats(client)
    _normalize_model(model)  # normalize for side-effect consistency

    request_kwargs = {
        "prompt": message or "",
        "files": [safe_image_path] if safe_image_path else None,
        "thinking_level": session_entry.get("thinking_level", thinking_level),
    }
    use_learning_mode = learning_mode or session_entry.get("learning_mode")
    if use_learning_mode:
        request_kwargs["learning_mode"] = use_learning_mode
    response = await session_entry["session"].send_message(**request_kwargs)
    _schedule_skill_response_cleanup(response, "skill_session:send", session_entry["session"])
    return _format_response(response)


def _session_list() -> list[TextContent]:
    with _sessions_lock:
        items = [
            f"{i}. {sid} ({data['model']})"
            for i, (sid, data) in enumerate(_sessions.items(), 1)
        ]
    if not items:
        return [TextContent(type="text", text="No active sessions")]
    return [TextContent(type="text", text="\n".join(items))]


def _session_reset(session_id: Optional[str]) -> list[TextContent]:
    with _sessions_lock:
        if session_id and session_id in _sessions:
            del _sessions[session_id]
            cleared_all = False
        else:
            _sessions.clear()
            cleared_all = True
    if cleared_all:
        reset_client()
        return [TextContent(type="text", text="All sessions reset")]
    return [TextContent(type="text", text=f"Session deleted: {session_id}")]


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
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return [TextContent(type="text", text=f"Error: {image_error}")]

        if action == "create":
            return await _session_create(model, thinking_level, learning_mode)
        if action == "send":
            return await _session_send(session_id, message, thinking_level, learning_mode, safe_image_path, model)
        if action == "list":
            return _session_list()
        if action == "reset":
            return _session_reset(session_id)
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
    action: Literal["status", "get", "profiles"],
    browser: Literal["chrome", "firefox", "edge"] = "chrome",
    profile: str = "",
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

        elif action == "profiles":
            profiles = list_browser_cookie_profiles(browser, validate=True)
            lines = []
            for item in profiles:
                if item.get("error"):
                    lines.append(f"error: {item['error']}")
                    continue
                available = "yes" if item.get("account_available") is True else "no"
                selected = "yes" if item.get("chrome_selected_profile") else "no"
                lines.append(
                    f"{item.get('profile', 'unknown')}: "
                    f"psid={'yes' if item.get('has_psid') else 'no'}, "
                    f"chrome_selected={selected}, "
                    f"account_available={available}, "
                    f"scheduled_registry_count={item.get('scheduled_registry_count', 'unvalidated')}"
                )
            return [TextContent(type="text", text="\n".join(lines) or "No profiles")]

        elif action == "get":
            success = get_cookie_from_browser(browser, profile=profile)
            suffix = f" {profile}" if profile else ""
            return [
                TextContent(
                    type="text",
                    text=f"Cookie{suffix}: {'Loaded' if success else 'Failed'}",
                )
            ]

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        logger.error(f"Cookie error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool(annotations=READ_ONLY_LOCAL)
async def doctor(
    browser: Literal["chrome", "firefox", "edge"] = "chrome",
    validate_browser: bool = False,
) -> list[TextContent]:
    """Run local preflight diagnostics without exposing cookie values."""
    try:
        payload = _doctor_payload(browser=browser, validate_browser=validate_browser)
        return [TextContent(type="text", text=_format_doctor_markdown(payload))]
    except Exception as e:
        logger.error(f"Doctor error: {e}")
        return [TextContent(type="text", text=f"Error: {e}")]


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
async def cleanup(
    markers: str = "codex-,Cleanup Verification Marker",
    target: Literal["all", "chats", "scheduled"] = "all",
    dry_run: bool = True,
    max_chats: int = 25,
    scan_turns: bool = False,
) -> list[TextContent]:
    """Find or delete test artifacts by explicit marker. Defaults to dry-run."""
    try:
        client = get_gemini_client()
        await initialize_client()
        payload = await _cleanup_test_artifacts_payload(
            client,
            markers=markers,
            target=target,
            dry_run=dry_run,
            max_chats=max_chats,
            scan_turns=scan_turns,
        )
        return [TextContent(type="text", text=_format_cleanup_markdown(payload))]
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
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
