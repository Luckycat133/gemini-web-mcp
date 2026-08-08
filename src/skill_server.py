#!/usr/bin/env python3
"""
Gemini Skill - Optimized MCP Server
Low-token, production-ready.
"""

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional

try:
    from src.adapters.mcp_sdk import MCPServer, TextContent
except ImportError:
    print('Error: MCP SDK v2 required. Install with: pip install "mcp>=2,<3"')
    exit(1)

from . import __version__
from .adapters import append_artifact_block, attach_domain_result, domain_text, exception_text
from .client_wrapper import (
    cleanup_due_remote_chats,
    create_session,
    get_cookie_from_browser,
    get_cookie_status,
    get_gemini_client,
    initialize_client,
    list_browser_cookie_profiles,
    list_sessions,
    lookup_session,
    reset_client_async,
    reset_session,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
    send_session_message,
    send_session_message_stream,
)
from .constants import resolve_media_request, resolve_model_name
from .domain import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    CleanupObservation,
    ConversationLifecycleMetadata,
    DomainErrorCode,
    DomainResult,
    SessionLifecycleState,
)
from .services import (
    ChatRequest,
    ChatService,
    ChatServiceDependencies,
    CleanupStrategy,
    SessionMessageRequest,
    StartSessionRequest,
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    classify_artifact_state,
    extract_response_artifacts,
    observed_backend_from_response,
    response_chat_id,
)
from .tools.annotations import (
    DESTRUCTIVE_LOCAL,
    DESTRUCTIVE_REMOTE,
    MUTATES_LOCAL,
    MUTATES_REMOTE,
    READ_ONLY_LOCAL,
    READS_PRIVATE_REMOTE,
)
from .infrastructure.rpc_contracts import WEB_FEATURE_PROBES, RawRPCData as _RawRPCData
from .infrastructure.rpc_parsers import extract_rpc_bodies as _extract_rpc_bodies
from .resources import default_prompts_resource
from .services.account import (
    execute_observed_rpc as _execute_observed_rpc,
    get_probe as _get_probe,
    parse_library_capability as _parse_library_capability,
    parse_public_link_entry as _parse_public_link_entry,
    parse_tool_mode_entry as _parse_tool_mode_entry,
    parse_usage_entry as _parse_usage_entry,
    summarize_rpc_response as _summarize_probe_response,
)
from .services.cleanup import (
    cleanup_test_artifacts_payload as _cleanup_test_artifacts_payload,
    format_cleanup_markdown as _format_cleanup_markdown,
)
from .services.doctor import (
    doctor_payload as _doctor_payload,
    format_doctor_markdown as _format_doctor_markdown,
)
from .services.history import (
    _format_chat_export_markdown,
    delete_chat_result as _delete_chat_result,
    export_chat_result as _export_chat_result,
    list_chats_result as _list_chats_result,
    read_chat_result as _read_chat_result,
    search_chats_result as _search_chats_result,
)
from .services.manifest import (
    format_tool_manifest_markdown as _format_tool_manifest_markdown,
    format_web_capabilities_markdown as _format_web_capabilities_markdown,
    tool_manifest_payload as _tool_manifest_payload,
    web_capabilities_payload as _web_capabilities_payload,
)
from .services.notebooks import fetch_native_notebooks as _fetch_native_notebooks
from .services.scheduled import (
    create_daily_action as _create_daily_action_service,
    delete_action as _delete_scheduled_action_service,
    fetch_scheduled_registry as _fetch_scheduled_registry,
    fetch_scheduled_task_by_id as _fetch_scheduled_task_by_id,
    parse_scheduled_action_create_body as _parse_scheduled_action_create_body,
    parse_scheduled_action_task_entry as _parse_scheduled_action_task_entry,
    scheduled_daily_payload as _scheduled_daily_payload,
)
from .tools.utils import extract_remote_chat_id, validate_optional_image_path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.environ.get("GEMINI_CONFIG_DIR", ".gemini"))
PROMPTS_FILE = CONFIG_DIR / "prompts.json"
DEFAULT_PROMPTS_FILE = default_prompts_resource()

MODEL_ALIASES = {
    "l": "flash-lite",
    "f": "flash",
    "t": "thinking",
    "p": "pro",
    "lite": "flash-lite",
    "pro": "pro",
}

MEDIA_TYPES = {"img": "image", "picture": "image", "photo": "image"}

mcp = MCPServer(
    name="Gemini",
    version=__version__,
    instructions=f"""
# Gemini Skill (v{__version__})

## Tools
- **chat**: conversation
- **create**: generate media
- **edit**: modify images
- **session**: conversation history
- **history**: remote Gemini chat history
- **account**: account, models, manifest, capabilities, feature probes, links,
  usage, library, native notebooks, scheduled actions, modes
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
    if not PROMPTS_FILE.exists() and DEFAULT_PROMPTS_FILE.is_file():
        PROMPTS_FILE.write_text(DEFAULT_PROMPTS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
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
_prompt_manager_lock = threading.Lock()


def get_prompts() -> PromptManager:
    """Get singleton prompt manager."""
    global _prompt_manager
    with _prompt_manager_lock:
        if _prompt_manager is None:
            _prompt_manager = PromptManager(PROMPTS_FILE)
        return _prompt_manager


def _truncate_text(text: Any, max_chars: int = 2000) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"


def _error_text(e: Exception, tool_name: str) -> list[TextContent]:
    """统一的工具错误返回：记录日志并返回给 agent 简短错误文本。"""
    logger.error(f"{tool_name} error: {e}")
    return [TextContent(type="text", text=f"Error: {e}")]


def _invalid_argument_result(message: str) -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
    )


def _session_not_found_result() -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.SESSION_NOT_FOUND,
        "The requested session does not exist.",
        suggested_action="Create a session and use the returned ID.",
        verification_status="local_state_absent",
        details={
            "lifecycle": ConversationLifecycleMetadata(
                session_state=SessionLifecycleState.ABSENT,
            )
        },
    )


def _schedule_skill_response_cleanup(response: Any, source: str, session: Any = None) -> None:
    """Mirror the primary MCP server's default remote-chat cleanup behavior."""
    cid = schedule_remote_chat_cleanup_from_response(response, source=source)
    if not cid and session is not None:
        schedule_remote_chat_cleanup(getattr(session, "cid", None), source=source)


def _schedule_compact_response_cleanup(
    response: Any,
    *,
    retain_chat: bool,
    delete_after_seconds: int | None,
    source: str,
) -> str | None:
    """Apply the same retention policy as the primary adapter."""
    return schedule_remote_chat_cleanup_from_response(
        response,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )


def _schedule_compact_chat_cleanup(
    chat_id: str | None,
    *,
    retain_chat: bool,
    delete_after_seconds: int | None,
    source: str,
) -> CleanupObservation | None:
    """Apply the same retention policy as the primary adapter."""
    return schedule_remote_chat_cleanup(
        chat_id,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )


def _build_chat_service() -> ChatService:
    """Bind compact presentation behavior to the shared chat service."""
    return ChatService(
        ChatServiceDependencies(
            client_provider=lambda: get_gemini_client(),
            client_initializer=lambda: initialize_client(),
            cleanup_due_remote_chats=lambda client: cleanup_due_remote_chats(client),
            create_session=lambda *args, **kwargs: create_session(*args, **kwargs),
            lookup_session=lambda session_id: lookup_session(session_id),
            send_session_message=lambda *args, **kwargs: send_session_message(
                *args,
                **kwargs,
            ),
            send_session_message_stream=lambda *args, **kwargs: send_session_message_stream(
                *args,
                **kwargs,
            ),
            schedule_response_cleanup=_schedule_compact_response_cleanup,
            schedule_chat_cleanup=_schedule_compact_chat_cleanup,
            normalize_model=lambda model: _normalize_model(model),
            resolve_model=lambda model: resolve_model_name(model),
        )
    )


_chat_service = _build_chat_service()


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
            return domain_text(
                _invalid_argument_result(image_error or "Invalid image path."),
                f"Error: {image_error}",
            )

        files = (safe_image_path,) if safe_image_path else ()

        if session_id:
            result = await _chat_service.send_session(
                SessionMessageRequest(
                    session_id=session_id,
                    message=message,
                    files=files,
                    learning_mode=learning_mode,
                    thinking_level=thinking_level,
                    prepare_client=True,
                    include_temporary=False,
                    fallback_empty_thinking_level=True,
                    cleanup_strategy=CleanupStrategy.RESPONSE_THEN_SESSION,
                    cleanup_source="skill_chat:session",
                )
            )
            if not result.ok or result.data is None:
                return domain_text(
                    result,
                    f"SESSION_NOT_FOUND: Invalid session: {session_id}",
                )
        else:
            result = await _chat_service.generate(
                ChatRequest(
                    message=message,
                    model=model,
                    thinking_level=thinking_level,
                    learning_mode=learning_mode,
                    files=files,
                    cleanup_source="skill_chat",
                    include_gem_argument=False,
                    include_temporary_argument=False,
                )
            )

        assert result.data is not None

        return attach_domain_result(
            _format_response(result.data.response),
            result,
            data=(
                {
                    "session_id": session_id,
                    "model": result.data.requested_model,
                    "lifecycle": result.data.lifecycle,
                }
                if session_id
                else {
                    "model": result.data.normalized_model,
                    "resolved_model": result.data.effective_model,
                    "lifecycle": result.data.lifecycle,
                }
            ),
        )

    except Exception as e:
        return exception_text(
            e,
            logger=logger,
            operation="chat",
            preserve_message=True,
        )


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
            result = _list_chats_result(chats, limit, offset, max_limit=50)
            assert result.data is not None
            page = result.data["items"]
            if not page:
                return domain_text(result, "No chats", use_result_data=True)
            lines = []
            for i, item in enumerate(page, result.data["offset"] + 1):
                lines.append(f"{i}. {item['title']} ({item['id']})")
            if result.data["has_more"]:
                lines.append(f"next_offset={result.data['next_offset']}")
            return domain_text(result, "\n".join(lines), use_result_data=True)

        if action == "search":
            needle = (query or "").strip()
            if not needle or (scan_turns and not hasattr(client, "read_chat")):
                result = await _search_chats_result(
                    client,
                    [],
                    needle,
                    limit,
                    offset,
                    scan_turns=scan_turns,
                )
                fallback = "query required" if not needle else "read_chat unavailable"
                return domain_text(result, fallback)
            chats = client.list_chats() if hasattr(client, "list_chats") else []
            chats = chats or []
            result = await _search_chats_result(
                client,
                chats,
                needle,
                limit,
                offset,
                scan_turns=scan_turns,
                turns_per_chat=min(max(limit, 1), 20),
                max_chars_per_turn=1000,
                max_limit=50,
            )
            assert result.data is not None
            lines = []
            for match in result.data["matches"]:
                lines.append(f"{match['title']} ({match['id']})")
                for snippet in match.get("snippets", [])[:3]:
                    if snippet.get("error"):
                        lines.append(f"  read error: {snippet['error']}")
                    else:
                        lines.append(
                            f"  turn {snippet.get('turn_index')} {snippet.get('role')}: "
                            f"{_truncate_text(snippet.get('text', ''), 240)}"
                        )
            if result.data["has_more"]:
                lines.append(f"next_offset={result.data['next_offset']}")
            return domain_text(result, "\n".join(lines) if lines else "No matches", use_result_data=True)

        if action == "read":
            result = await _read_chat_result(client, chat_id or "", limit, 2000, max_limit=50)
            if not result.ok:
                if result.error_code == DomainErrorCode.INVALID_ARGUMENT.value:
                    return domain_text(result, "chat_id required")
                return domain_text(result, "read_chat unavailable")
            assert result.data is not None
            if not result.data["turns"]:
                return domain_text(result, "No turns", use_result_data=True)
            lines = [f"{turn['role']}: {turn['text']}" for turn in result.data["turns"]]
            return domain_text(result, "\n\n".join(lines), use_result_data=True)

        if action == "export":
            async def _load_export_metadata() -> list[object]:
                if not hasattr(client, "list_chats"):
                    return []
                return list(client.list_chats() or [])

            result = await _export_chat_result(
                client,
                chat_id or "",
                limit,
                20000,
                metadata_loader=_load_export_metadata,
                max_limit=200,
            )
            if not result.ok:
                fallback = (
                    "chat_id required"
                    if result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
                    else "read_chat unavailable"
                )
                return domain_text(result, fallback)
            assert result.data is not None
            if not result.meta.details.get("found"):
                return domain_text(result, f"No chat: {chat_id}", use_result_data=True)
            return domain_text(
                result,
                _format_chat_export_markdown(result.data),
                use_result_data=True,
            )

        if action == "delete":
            result = await _delete_chat_result(client, chat_id or "")
            if not result.ok:
                if result.error_code == DomainErrorCode.INVALID_ARGUMENT.value:
                    return domain_text(result, "chat_id required")
                if result.error_code == DomainErrorCode.CAPABILITY_UNAVAILABLE.value:
                    return domain_text(result, "delete_chat unavailable")
                assert result.data is not None
                status = result.data["verification"]["status"]
                if status == "still_present":
                    return domain_text(result, f"Delete not verified: {result.data['chat_id']} is still present", use_result_data=True)
                return domain_text(
                    result,
                    f"Delete requested: {result.data['chat_id']} (read-back verification failed)",
                    use_result_data=True,
                )
            assert result.data is not None
            if result.data["deleted"] is True:
                text = f"Deleted and verified absent: {result.data['chat_id']}"
            else:
                text = f"Delete requested: {result.data['chat_id']} (not independently verified)"
            return domain_text(result, text, use_result_data=True)

        return [TextContent(type="text", text="Invalid action")]

    except Exception as e:
        return _error_text(e, "History")


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
            f"{probe_name}: key={item.get('key')} limit={item.get('limit_value')} "
            f"remaining={item.get('remaining_value')}"
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
        f"mode_id={item.get('mode_id')} available={item.get('available')} "
        f"quota={item.get('quota_value')} state={item.get('state')}"
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
    action: Literal[
        "status",
        "models",
        "manifest",
        "capabilities",
        "features",
        "links",
        "usage",
        "library",
        "notebooks",
        "scheduled",
        "modes",
    ] = "status",
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
        return _error_text(e, "Account")


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
    result = await _create_daily_action_service(
        client,
        title=clean_title,
        instructions=clean_instructions,
        hour=hour,
        timezone_name=clean_timezone or "Asia/Shanghai",
        locale="zh-CN",
        max_chars=200,
        fetch_registry=_fetch_scheduled_registry,
        fetch_by_id=_fetch_scheduled_task_by_id,
        extract_bodies=_extract_rpc_bodies,
        parse_create=_parse_scheduled_action_create_body,
        payload_builder=_scheduled_daily_payload,
    )
    created_id = result.get("id", "")
    visible = bool(result.get("visible_in_registry"))
    verification_status = result.get("verification_status", "not_attempted")
    suffix = "" if visible else f" ({verification_status}; verify account context)"
    return [TextContent(type="text", text=f"Created: {created_id or clean_title}{suffix}")]


async def _scheduled_delete(client: Any, action_id: str) -> list[TextContent]:
    clean_id = action_id.strip()
    if not clean_id:
        return [TextContent(type="text", text="action_id required")]
    result = await _delete_scheduled_action_service(
        client,
        action_id=clean_id,
        max_chars=200,
        fetch_registry=_fetch_scheduled_registry,
        fetch_by_id=_fetch_scheduled_task_by_id,
        extract_bodies=_extract_rpc_bodies,
    )
    verification_status = result.get("verification_status", "not_attempted")
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
        return _error_text(e, "Scheduled action")


@mcp.tool(annotations=MUTATES_REMOTE)
async def create(
    prompt: str,
    type: Literal["image", "video", "music"] = "image",
    model: str = "flash",
    thinking_level: str = "standard",
    image_path: Optional[str] = None,
) -> list[TextContent]:
    """Generate image/video/music."""
    requested_model = model
    media_type = _normalize_media_type(type)
    request_model: str | None = None
    effective_backend: str | None = None
    input_artifacts: tuple[Artifact, ...] = ()
    try:
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return domain_text(
                _invalid_argument_result(image_error or "Invalid image path."),
                f"Error: {image_error}",
            )

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)

        model = _normalize_model(model)
        media_request = resolve_media_request(model, media_type, thinking_level)
        request_model = media_request["request_model"]
        effective_backend = media_request["backend_label"]

        prefixes = {
            "image": "Generate image: ",
            "video": "Generate video: ",
            "music": "Create music: ",
        }
        media_prompt = prefixes.get(media_type, "") + prompt
        files = [safe_image_path] if safe_image_path else None
        if safe_image_path:
            input_artifacts = (
                artifact_from_local_path(
                    ArtifactKind.IMAGE,
                    safe_image_path,
                    title=Path(safe_image_path).name,
                    requested_backend=requested_model,
                    request_model=request_model,
                    effective_backend=effective_backend,
                ),
            )

        response = await client.generate_content(
            prompt=media_prompt,
            files=files,
            model=request_model,
            thinking_level=thinking_level,
        )
        _schedule_skill_response_cleanup(response, f"skill_create:{media_type}")
        observed_backend = observed_backend_from_response(response)
        artifacts = extract_response_artifacts(
            response,
            media_type=media_type,
            requested_backend=requested_model,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
        )
        if safe_image_path:
            input_artifacts = (
                artifact_from_local_path(
                    ArtifactKind.IMAGE,
                    safe_image_path,
                    title=Path(safe_image_path).name,
                    requested_backend=requested_model,
                    request_model=request_model,
                    effective_backend=effective_backend,
                    observed_backend=observed_backend,
                    source_chat_id=response_chat_id(response),
                ),
            )
        data = ArtifactResultData(
            state=classify_artifact_state(response, artifacts),
            artifacts=artifacts,
            input_artifacts=input_artifacts,
            requested_model=requested_model,
            request_model=request_model,
            effective_backend=effective_backend,
            observed_backend=observed_backend,
            source_chat_id=response_chat_id(response),
            media_type=media_type,
        )
        result = artifact_result(data)
        content = _format_response(
            response,
            media_type,
            backend_label=effective_backend,
            backend_note=media_request["note"],
        )
        if data.state == ArtifactState.EMPTY:
            content[0].text += "\n\nArtifact state: empty (no usable media URI was returned)."
        elif data.state == ArtifactState.QUEUED:
            content[0].text += "\n\nArtifact state: queued (no completed media is available yet)."
        content = append_artifact_block(content, data.artifacts)
        return attach_domain_result(content, result, use_result_data=True)

    except Exception as e:
        data = ArtifactResultData(
            state=ArtifactState.FAILED,
            requested_model=requested_model,
            request_model=request_model,
            effective_backend=effective_backend,
            input_artifacts=input_artifacts,
            media_type=media_type,
        )
        result = artifact_exception_result(
            e,
            data,
            logger=logger,
            operation=f"skill_create:{media_type}",
        )
        return attach_domain_result(
            _error_text(e, "Create"),
            result,
            use_result_data=True,
        )


@mcp.tool(annotations=MUTATES_REMOTE)
async def edit(
    image_path: str,
    prompt: str,
    model: str = "flash",
    thinking_level: str = "standard",
) -> list[TextContent]:
    """Edit existing image."""
    requested_model = model
    request_model: str | None = None
    input_artifacts: tuple[Artifact, ...] = ()
    try:
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return domain_text(
                _invalid_argument_result(image_error or "Invalid image path."),
                f"Error: {image_error}",
            )

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)

        model = _normalize_model(model)
        request_model = resolve_model_name(model)
        input_artifacts = (
            artifact_from_local_path(
                ArtifactKind.IMAGE,
                safe_image_path or image_path,
                title=Path(safe_image_path or image_path).name,
                requested_backend=requested_model,
                request_model=request_model,
                effective_backend=request_model,
            ),
        )

        response = await client.generate_content(
            prompt=f"Edit this image: {prompt}",
            files=[safe_image_path],
            model=request_model,
            thinking_level=thinking_level,
        )
        _schedule_skill_response_cleanup(response, "skill_edit")
        observed_backend = observed_backend_from_response(response)
        artifacts = extract_response_artifacts(
            response,
            media_type="image",
            requested_backend=requested_model,
            request_model=request_model,
            effective_backend=request_model,
            observed_backend=observed_backend,
        )
        input_artifacts = (
            artifact_from_local_path(
                ArtifactKind.IMAGE,
                safe_image_path or image_path,
                title=Path(safe_image_path or image_path).name,
                requested_backend=requested_model,
                request_model=request_model,
                effective_backend=request_model,
                observed_backend=observed_backend,
                source_chat_id=response_chat_id(response),
            ),
        )
        data = ArtifactResultData(
            state=classify_artifact_state(response, artifacts),
            artifacts=artifacts,
            input_artifacts=input_artifacts,
            requested_model=requested_model,
            request_model=request_model,
            effective_backend=request_model,
            observed_backend=observed_backend,
            source_chat_id=response_chat_id(response),
            media_type="image_edit",
        )
        result = artifact_result(data)
        content = _format_response(response, "image")
        content = append_artifact_block(content, data.artifacts)
        return attach_domain_result(content, result, use_result_data=True)

    except Exception as e:
        data = ArtifactResultData(
            state=ArtifactState.FAILED,
            requested_model=requested_model,
            request_model=request_model,
            effective_backend=request_model,
            input_artifacts=input_artifacts,
            media_type="image_edit",
        )
        result = artifact_exception_result(
            e,
            data,
            logger=logger,
            operation="skill_edit",
        )
        return attach_domain_result(
            _error_text(e, "Edit"),
            result,
            use_result_data=True,
        )


async def _session_create(
    model: str,
    thinking_level: str,
    learning_mode: Optional[str],
) -> list[TextContent]:
    result = await _chat_service.start_session(
        StartSessionRequest(
            model=model,
            thinking_level=thinking_level,
            learning_mode=learning_mode,
            include_gem_argument=False,
        )
    )
    sid = result.data.session_id if result.data is not None else ""
    normalized_model = result.data.normalized_model if result.data is not None else _normalize_model(model)
    effective_model = (
        result.data.effective_model if result.data is not None else resolve_model_name(normalized_model)
    )
    return domain_text(
        result,
        f"Session created: {sid}",
        data={
            "session_id": sid,
            "model": normalized_model,
            "resolved_model": effective_model,
            "lifecycle": (
                result.data.lifecycle if result.data is not None else None
            ),
        },
    )


async def _session_send(
    session_id: Optional[str],
    message: Optional[str],
    thinking_level: str,
    learning_mode: Optional[str],
    safe_image_path: Optional[str],
    model: str,
) -> list[TextContent]:
    if not session_id:
        return domain_text(
            _session_not_found_result(),
            f"SESSION_NOT_FOUND: Invalid session: {session_id}",
        )
    result = await _chat_service.send_session(
        SessionMessageRequest(
            session_id=session_id,
            message=message or "",
            files=(safe_image_path,) if safe_image_path else (),
            learning_mode=learning_mode,
            thinking_level=thinking_level,
            prepare_client=True,
            include_temporary=False,
            fallback_empty_thinking_level=True,
            cleanup_strategy=CleanupStrategy.RESPONSE_THEN_SESSION,
            cleanup_source="skill_session:send",
        )
    )
    if not result.ok or result.data is None:
        return domain_text(
            result,
            f"SESSION_NOT_FOUND: Invalid session: {session_id}",
        )
    return attach_domain_result(
        _format_response(result.data.response),
        result,
        data={
            "session_id": session_id,
            "model": result.data.requested_model,
            "lifecycle": result.data.lifecycle,
        },
    )


def _session_list() -> list[TextContent]:
    sessions = list_sessions()
    items = [f"{i}. {sid} ({data['model']})" for i, (sid, data) in enumerate(sessions.items(), 1)]
    result = DomainResult.success(
        {
            "count": len(sessions),
            "sessions": [
                {
                    "session_id": sid,
                    "model": data["model"],
                    "retain_chat": data.get("retain_chat", False),
                    "lifecycle_state": data.get("lifecycle_state", "active"),
                }
                for sid, data in sessions.items()
            ],
        }
    )
    if not items:
        return domain_text(result, "No active sessions", use_result_data=True)
    return domain_text(result, "\n".join(items), use_result_data=True)


async def _session_reset(session_id: Optional[str], *, reset_all: bool = False) -> list[TextContent]:
    if reset_all:
        reset_result = await reset_client_async()
        if isinstance(reset_result, DomainResult):
            return domain_text(
                reset_result,
                "All sessions reset",
                use_result_data=True,
            )
        return domain_text(
            DomainResult.success({"scope": "all"}),
            "All sessions reset",
            use_result_data=True,
        )
    if not session_id:
        message = "session_id is required; use action='reset_all'"
        return domain_text(
            _invalid_argument_result(message),
            f"INVALID_ARGUMENT: {message}",
        )
    result = await reset_session(session_id)
    if not result.ok:
        return domain_text(
            result,
            f"SESSION_NOT_FOUND: Invalid session: {session_id}",
        )
    return domain_text(
        result,
        f"Session deleted: {session_id}",
        data={
            "session_id": session_id,
            "lifecycle": result.meta.details.get("lifecycle"),
        },
    )


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
async def session(
    action: Literal["create", "send", "list", "reset", "reset_one", "reset_all"],
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    image_path: Optional[str] = None,
) -> list[TextContent]:
    """Manage shared sessions; reset/reset_one need an ID and only reset_all clears all."""
    try:
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return domain_text(
                _invalid_argument_result(image_error or "Invalid image path."),
                f"Error: {image_error}",
            )

        if action == "create":
            return await _session_create(model, thinking_level, learning_mode)
        if action == "send":
            return await _session_send(session_id, message, thinking_level, learning_mode, safe_image_path, model)
        if action == "list":
            return _session_list()
        if action in {"reset", "reset_one"}:
            return await _session_reset(session_id)
        if action == "reset_all":
            return await _session_reset(None, reset_all=True)
        return domain_text(
            _invalid_argument_result(f"Unsupported session action: {action}"),
            "Invalid action",
        )

    except Exception as e:
        return exception_text(
            e,
            logger=logger,
            operation="session",
            preserve_message=True,
        )


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
        return _error_text(e, "Prompts")


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
                    error_code = f" [{item['error_code']}]" if item.get("error_code") else ""
                    lines.append(f"error: {item['error']}{error_code}")
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
        return _error_text(e, "Cookie")


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
        return _error_text(e, "Doctor")


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
        return _error_text(e, "Cleanup")


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
