"""Evidence-backed native Notebook project request builders and parsers."""

from __future__ import annotations

import json
from typing import Any

from .rpc_parsers import RPCParseResult


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Invalid notebook resource identity.")
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def create_payload(*, title: str, description: str = "") -> str:
    if not isinstance(title, str) or not title.strip() or len(title) > 256 or not isinstance(description, str) or len(description) > 800:
        raise ValueError("Invalid notebook metadata.")
    metadata: list[Any] = [None] * 17
    for field, value in {1: title, 2: description, 5: [], 8: [], 9: False, 10: False,
                         11: 1, 12: [], 17: [2, None, None, None, 1, []]}.items():
        metadata[field - 1] = value
    return _dump([metadata])


def get_payload(*, notebook_id: str) -> str:
    return _dump([_identity(notebook_id)])


def delete_payload(*, notebook_id: str) -> str:
    return get_payload(notebook_id=notebook_id)


def source_create_payload(*, notebook_id: str, title: str, text: str, mime: str = "text/plain") -> str:
    _identity(notebook_id)
    if (not isinstance(title, str) or not title.strip() or len(title) > 256 or not isinstance(text, str)
            or not text or len(text) > 100000 or mime not in {"text/plain", "text/markdown"}):
        raise ValueError("Invalid source.")
    source: list[Any] = [None] * 9
    source[1], source[5], source[8] = title, [text, 2 if mime == "text/markdown" else 1], mime
    return _dump([notebook_id, source, [1, 3]])


def source_delete_payload(*, source_id: str) -> str:
    return _dump([_identity(source_id), [1, 3]])


def parse_created(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    identifier = body[0]
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=identifier)


def parse_project(body: Any, *, notebook_id: str = "", **_: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    project = body[0]
    if (not isinstance(project, list) or len(project) < 2 or not isinstance(project[0], str)
            or not project[0] or (notebook_id and project[0] != notebook_id) or not isinstance(project[1], list)
            or not project[1] or not isinstance(project[1][0], str)):
        return RPCParseResult("changed_shape")
    metadata = project[1]
    source_group = metadata[10] if len(metadata) > 10 else None
    sources = []
    if source_group is not None:
        if (not isinstance(source_group, list) or len(source_group) < 2
                or not isinstance(source_group[1], list)):
            return RPCParseResult("changed_shape")
        for entry in source_group[1]:
            if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                    or not entry[0] or not isinstance(entry[1], list) or len(entry[1]) < 7
                    or entry[1][0] != entry[0] or not isinstance(entry[1][1], str)
                    or type(entry[1][6]) is not int):
                return RPCParseResult("changed_shape")
            sources.append({"source_id": entry[0], "title": entry[1][1], "state": entry[1][6],
                            "mime": entry[1][8] if len(entry[1]) > 8 and isinstance(entry[1][8], str) else None})
        if len({item["source_id"] for item in sources}) != len(sources):
            return RPCParseResult("changed_shape")
    return RPCParseResult("success", value={"notebook_id": project[0], "title": metadata[0], "sources": sources})


async def request(client: Any, key: str, **arguments: Any) -> RPCParseResult:
    """Send one request, validate envelope, and fence account identity."""
    from .account_identity import current_account_key
    from .rpc_contracts import execute_contract, get_contract
    from .rpc_parsers import parse_contract_body, parse_rpc_envelope
    from ..services.deadlines import await_before_deadline
    owner = current_account_key()
    if not owner:
        raise ValueError("Account unavailable.")
    contract = get_contract(key)
    contract.build_payload(**arguments)
    if current_account_key() != owner:
        raise ValueError("Account changed.")
    response = await await_before_deadline(execute_contract(client, key, retry_count=0, **arguments), timeout=30)
    if current_account_key() != owner:
        raise ValueError("Account changed.")
    if getattr(response, "status_code", None) != 200:
        raise ValueError("Notebook HTTP response unavailable.")
    envelope = parse_rpc_envelope(getattr(response, "text", ""), contract.rpc_id)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise ValueError("Notebook response unavailable.")
    parsed = parse_contract_body(contract, envelope.bodies[0],
                                 **({"notebook_id": arguments["notebook_id"]} if key == "notebook_admin.get" else {}))
    if not parsed.ok:
        raise ValueError("Notebook response shape changed.")
    return parsed
