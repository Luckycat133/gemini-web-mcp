"""Fresh, compact NotebookLM and Task metadata over observed Web contracts."""

import json
from typing import Any, cast

from ..infrastructure.rpc_contracts import execute_contract, execute_graphql_read, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


class WebContextUnavailable(RuntimeError):
    code = "UPSTREAM_CHANGED"


async def _batch(client: Any, key: str, **arguments: Any) -> Any:
    contract = get_contract(key)
    response = await execute_contract(client, key, retry_count=0 if contract.mode == "mutation" else None,
                                      **arguments)
    if getattr(response, "status_code", None) != 200:
        raise WebContextUnavailable("The Web metadata HTTP response was not accepted.")
    envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
    # GetRemyGoal returns the observed NOT_FOUND code after deleting a goal.
    # A missing envelope, other rejection or an accompanying body is not absence.
    if key == "tasks.get" and envelope.parsed and envelope.reject_code == 5 and not envelope.bodies:
        return None
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise WebContextUnavailable("The Web metadata envelope was not accepted.")
    result = parse_contract_body(contract, envelope.bodies[0])
    if not result.ok:
        raise WebContextUnavailable("The Web metadata response shape changed.")
    if key == 'tasks.get' and result.value is None:
        raise WebContextUnavailable('An empty Task reply is not verified absence.')
    return result.value


async def fetch_notebooklm_metadata_graphql(client: Any) -> list[dict[str, Any]]:
    """Read picker metadata only; no Notebook source or chat text is requested."""
    async def read() -> list[dict[str, Any]]:
        response = await execute_graphql_read(client, "notebooklm.picker")
        if getattr(response, "status_code", None) != 200:
            raise WebContextUnavailable("The Notebook picker HTTP response was not accepted.")
        raw = getattr(response, "text", "") or ""
        if raw.startswith(")]}'"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            raise WebContextUnavailable("The Notebook picker response was not JSON.") from None
        parsed = parse_contract_body("notebooklm.picker", body)
        if not parsed.ok:
            raise WebContextUnavailable("The Notebook picker returned errors or an unknown shape.")
        return cast(list[dict[str, Any]], parsed.value)
    return cast(list[dict[str, Any]], await await_before_deadline(read(), timeout=30))


async def fetch_notebooklm_metadata(client: Any) -> list[dict[str, Any]]:
    """The same native picker metadata used by the current Web application."""
    return cast(list[dict[str, Any]], await await_before_deadline(_batch(client, "notebooklm.list"), timeout=30))


async def fetch_task_metadata(client: Any, chat_id: str) -> dict[str, Any] | None:
    return cast(dict[str, Any] | None,
                await await_before_deadline(_batch(client, "tasks.get", chat_id=chat_id), timeout=30))


async def fetch_task_metadata_page(client: Any, limit: int, page_token: str | None = None) -> dict[str, Any]:
    return cast(dict[str, Any], await await_before_deadline(
        _batch(client, "tasks.list", page_size=limit, next_page_token=page_token), timeout=30))


async def delete_task_metadata(client: Any, chat_id: str) -> None:
    """One dispatch; the caller must independently verify exact Task absence."""
    await await_before_deadline(_batch(client, "tasks.delete", chat_id=chat_id), timeout=30)


async def refresh_task_schedule_guard(client: Any, chat_id: str, *, store: Any = None) -> list[str]:
    """Read only this Task's schedule references before allowing cascading cleanup."""
    from ..infrastructure.account_identity import current_account_key
    from ..infrastructure.task_schedule_store import TaskScheduleGuardStore

    owner = current_account_key()
    if not owner:
        raise WebContextUnavailable("The Task account is unavailable.")
    value = await await_before_deadline(_batch(client, "tasks.schedule_refs", chat_id=chat_id), timeout=30)
    if current_account_key() != owner:
        raise WebContextUnavailable("The Task account changed while reading schedule references.")
    if not isinstance(value, dict) or value.get("chat_id") != chat_id:
        raise WebContextUnavailable("The exact Task schedule references were not observed.")
    guards = TaskScheduleGuardStore(getattr(store, "path", None))
    guards.replace_chat(owner, chat_id, value['schedule_ids'])
    return cast(list[str], value['schedule_ids'])


async def fetch_task_progress_turn(client: Any, chat_id: str, rid: str | None = None,
                                   candidate_id: str | None = None) -> dict[str, Any] | None:
    """Explicit Task-account activity read, distinct from operation answer recovery."""
    from ..infrastructure.account_identity import current_account_key

    owner = current_account_key()
    if not owner:
        raise WebContextUnavailable("The Task account is unavailable.")
    task = await fetch_task_metadata(client, chat_id)
    if current_account_key() != owner:
        raise WebContextUnavailable("The Task account changed.")
    if not task or task.get('chat_id') != chat_id:
        raise WebContextUnavailable("The exact Task was not observed.")
    if rid is None:
        items = await await_before_deadline(_batch(client, 'tasks.progress_latest', chat_id=chat_id, limit=1), timeout=30)
        if not isinstance(items, list) or len(items) != 1:
            return None
        value = items[0]
    else:
        value = await await_before_deadline(_batch(client, 'tasks.progress_turn', chat_id=chat_id, rid=rid), timeout=30)
    if current_account_key() != owner:
        raise WebContextUnavailable("The Task account changed.")
    if (not isinstance(value, dict) or value.get('chat_id') != chat_id
            or (rid is not None and value.get('response_id') != rid)):
        raise WebContextUnavailable("The Task turn identity did not match.")
    candidates = value['candidates']
    selected = [candidate for candidate in candidates if candidate[0] == candidate_id] if candidate_id else candidates
    if len(selected) != 1:
        raise WebContextUnavailable("The Task candidate is ambiguous or unavailable.")
    return {'chat_id': chat_id, 'response_id': value['response_id'], 'candidate': selected[0],
            'latest_verified': rid is None}


async def fetch_task_browser_turn(client: Any, chat_id: str, rid: str | None = None,
                                  candidate_id: str | None = None) -> dict[str, Any] | None:
    """Refresh VM credentials through the native history path, bound to the exact turn.

    The live viewer uses ListConversationTurns. Its opaque VM credential differs
    from a separate single-turn read even for the same CID/RID/candidate. Refresh
    through the viewer's path, selecting the original RID and candidate exactly.
    """
    from ..infrastructure.account_identity import current_account_key

    owner = current_account_key()
    if not owner:
        raise WebContextUnavailable('The Task account is unavailable.')
    task = await fetch_task_metadata(client, chat_id)
    if current_account_key() != owner or not task or task.get('chat_id') != chat_id:
        raise WebContextUnavailable('The exact Task account could not be verified.')
    items = await await_before_deadline(_batch(client, 'tasks.progress_latest', chat_id=chat_id,
                                              limit=100 if rid else 1), timeout=30)
    if current_account_key() != owner:
        raise WebContextUnavailable('The Task account changed.')
    if not isinstance(items, list):
        raise WebContextUnavailable('The browser turn list was not observed.')
    if rid is None and len(items) != 1:
        raise WebContextUnavailable('The latest browser turn response exceeded its read window.')
    turns = [item for item in items if item.get('chat_id') == chat_id and (rid is None or item.get('response_id') == rid)]
    if len(turns) != 1:
        raise WebContextUnavailable('The exact browser turn was not found in the bounded read window.')
    value = turns[0]
    candidates = [item for item in value['candidates'] if candidate_id is None or item[0] == candidate_id]
    if len(candidates) != 1:
        raise WebContextUnavailable('The exact browser candidate is ambiguous or unavailable.')
    return {'chat_id': chat_id, 'response_id': value['response_id'], 'candidate': candidates[0],
            'candidate_id': candidates[0][0], 'latest_verified': rid is None}


async def fetch_workflow_turn(client: Any, chat_id: str, rid: str, rcid: str | None,
                              *, limit: int = 100) -> dict[str, Any] | None:
    """Observe an exact reply without sending a prompt or picking an unrelated turn."""
    async def read() -> dict[str, Any] | None:
        contract = get_contract("web_workflow.turn")
        response = await execute_contract(client, contract.key, chat_id=chat_id, limit=limit)
        if getattr(response, "status_code", None) != 200:
            raise WebContextUnavailable("Reply read-back HTTP response was not accepted.")
        envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
        if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
            raise WebContextUnavailable("Reply read-back envelope was not accepted.")
        parsed = parse_contract_body(contract, envelope.bodies[0], chat_id=chat_id, rid=rid, rcid=rcid)
        if not parsed.ok:
            raise WebContextUnavailable("Reply read-back identity or shape changed.")
        return cast(dict[str, Any] | None, parsed.value)
    return cast(dict[str, Any] | None, await await_before_deadline(read(), timeout=60))


async def fetch_task_file_cards(client: Any, chat_id: str, rid: str, rcid: str) -> tuple[Any, ...]:
    """Read all file cards on one completed exact Task candidate."""
    from ..infrastructure.task_files import parse_task_files

    async def read() -> tuple[Any, ...]:
        contract = get_contract('web_workflow.turn')
        response = await execute_contract(client, contract.key, chat_id=chat_id, limit=100)
        if getattr(response, 'status_code', None) != 200:
            raise WebContextUnavailable('Task file read-back HTTP response was not accepted.')
        envelope = parse_rpc_envelope(getattr(response, 'text', '') or '', contract.rpc_id)
        if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
            raise WebContextUnavailable('Task file read-back envelope was not accepted.')
        parsed = parse_task_files(envelope.bodies[0], rid=rid, rcid=rcid)
        if not parsed.ok:
            raise WebContextUnavailable('Task file candidate or card shape was not verified.')
        return cast(tuple[Any, ...], parsed.value)
    return cast(tuple[Any, ...], await await_before_deadline(read(), timeout=60))
