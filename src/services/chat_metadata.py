"""Metadata-only reads and observed mutations for exact Gemini chat identities."""

from typing import Any, cast

from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


class ChatMetadataUnavailable(RuntimeError):
    code = "UPSTREAM_CHANGED"


async def _execute(client: Any, key: str, **arguments: Any) -> Any:
    contract = get_contract(key)
    response = await execute_contract(client, key, retry_count=0 if contract.mode == "mutation" else None,
                                      **arguments)
    if getattr(response, "status_code", None) != 200:
        raise ChatMetadataUnavailable("Chat metadata RPC did not pass its HTTP status check.")
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise ChatMetadataUnavailable("Chat metadata RPC did not return one accepted envelope.")
    parsed = parse_contract_body(contract, envelope.bodies[0])
    if not parsed.ok:
        raise ChatMetadataUnavailable("Chat metadata RPC response shape changed.")
    return parsed.value


async def read_chat_metadata(client: Any, chat_id: str, *, max_pages: int = 20) -> dict[str, Any] | None:
    """Read fresh recent/pinned metadata without fetching any conversation text."""
    async def exact_bucket(pinned: bool) -> dict[str, Any] | None:
        # Public ListConversations filter field 6 is an exact conversation ID.
        # A matching filtered row proves membership even when the returned
        # proto omits its default-valued pinned field.
        contract = get_contract("history.metadata_page")
        response = await execute_contract(client, contract.key,
                                          filter_payload=[pinned, None, True, None, None, chat_id],
                                          page_size=100, next_page_token=None)
        if getattr(response, "status_code", None) != 200:
            raise ChatMetadataUnavailable("Exact chat metadata HTTP response was unavailable.")
        envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id)
        if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
            raise ChatMetadataUnavailable("Exact chat metadata response was unavailable.")
        body = envelope.bodies[0]
        if body == []:
            return None  # Only a nonempty exact match proves bucket membership.
        parsed = parse_contract_body(contract, body)
        if not parsed.ok or not isinstance(parsed.value, dict) or parsed.value.get("next_page_token"):
            raise ChatMetadataUnavailable("Exact chat metadata response changed.")
        items = parsed.value.get("items")
        if not isinstance(items, list) or len(items) > 1:
            raise ChatMetadataUnavailable("Exact chat metadata identity was ambiguous.")
        if not items:
            return None
        item = items[0]
        if item["id"] != chat_id or item["is_pinned"] not in (None, pinned):
            raise ChatMetadataUnavailable("Exact chat metadata identity did not match.")
        return {**item, "is_pinned": pinned}

    async def scan() -> dict[str, Any] | None:
        incomplete = False
        for pinned in (True, False):
            token = None
            seen: set[str] = set()
            for _ in range(max(1, min(max_pages, 100))):
                page = await _execute(client, "history.metadata_page", filter_payload=[pinned, None, True],
                                      page_size=100, next_page_token=token)
                for item in page["items"]:
                    if item["id"] == chat_id:
                        return dict(item)
                token = page["next_page_token"]
                if not token:
                    break
                if token in seen:
                    raise ChatMetadataUnavailable("Chat metadata pagination did not progress.")
                seen.add(token)
            else:
                incomplete = True
        if incomplete:
            raise ChatMetadataUnavailable("Chat metadata scan reached its page limit.")
        for pinned in (False, True):
            item = await exact_bucket(pinned)
            if item is not None:
                return item
        return None

    return cast(dict[str, Any] | None, await await_before_deadline(scan(), timeout=60))


async def mutate_chat_metadata(client: Any, action: str, chat_id: str, changes: dict[str, Any]) -> None:
    """Send one mutation; only a separate fresh metadata read can verify it."""
    if action == "rename" and set(changes) == {"title"}:
        key, arguments = "history.rename", {"chat_id": chat_id, "title": changes["title"]}
    elif action in {"pin", "unpin"} and set(changes) == {"is_pinned"}:
        key, arguments = "history.pin", {"chat_id": chat_id, "is_pinned": changes["is_pinned"]}
    else:
        raise ValueError("Unsupported chat metadata mutation.")
    await await_before_deadline(_execute(client, key, **arguments), timeout=30)
