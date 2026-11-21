"""Evidence-backed, read-only native Gemini Skills RPC projection."""

from __future__ import annotations

import json
from typing import Any

from .rpc_contracts import execute_contract, get_contract
from .rpc_parsers import RPCParseResult, parse_rpc_envelope
from ..domain.managed_skills import ManagedSkill
from ..infrastructure.account_identity import current_account_key
from ..services.deadlines import await_before_deadline


def list_payload() -> str:
    return "[]"


def get_payload(*, skill_id: str) -> str:
    if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 256:
        raise ValueError("Invalid Skill identity.")
    return json.dumps([skill_id], ensure_ascii=False, separators=(",", ":"))


def _skill(raw: Any, *, include_instructions: bool = False) -> ManagedSkill:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError
    skill_id, title, description = raw[:3]
    if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 256:
        raise ValueError
    if title is None:
        title = ""
    if description is None:
        description = ""
    if not isinstance(title, str) or not isinstance(description, str):
        raise ValueError
    enabled = raw[4] if len(raw) > 4 else None
    if enabled is not None and type(enabled) is not bool and not (type(enabled) is int and enabled in (0, 1)):
        raise ValueError
    version = raw[13] if len(raw) > 13 else None
    if version is not None and not isinstance(version, str):
        raise ValueError
    instructions = raw[3] if len(raw) > 3 else None
    if include_instructions and instructions is not None and not isinstance(instructions, str):
        raise ValueError
    files = raw[6] if len(raw) > 6 else None
    plain_editable = files is None or files == [] or files == [[]]
    if isinstance(files, list) and len(files) == 1 and isinstance(files[0], list):
        entries = files[0]
        plain_editable = (len(entries) == 1 and isinstance(entries[0], list)
                          and len(entries[0]) >= 2 and entries[0][0] == "SKILL.md"
                          and isinstance(entries[0][1], str)
                          and (len(entries[0]) < 3 or entries[0][2] in (None, "text/markdown"))
                          and all(value is None for value in entries[0][3:])) or not entries
    return ManagedSkill(skill_id, title[:256], description[:800], bool(enabled) if enabled is not None else None,
                        version, instructions if include_instructions else None, plain_editable)


def parse_list(body: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty", value=())
    if not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    try:
        records = tuple(_skill(item) for item in body[0])
        if len({item.skill_id for item in records}) != len(records):
            raise ValueError
    except (ValueError, TypeError):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success" if records else "empty", value=records)


def parse_get(body: Any, *, skill_id: str, include_instructions: bool = False) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")  # Empty body is not authoritative absence.
    try:
        record = _skill(body[0], include_instructions=include_instructions)
        if record.skill_id != skill_id:
            raise ValueError
    except (ValueError, TypeError):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=record)


async def read_skills(client: Any, key: str, *, skill_id: str | None = None,
                      include_instructions: bool = False) -> Any:
    owner = current_account_key()
    if not owner:
        raise ValueError("Account is unavailable.")
    contract = get_contract(key)
    args = {"skill_id": skill_id} if skill_id is not None else {}
    response = await await_before_deadline(execute_contract(client, key, retry_count=0, **args), timeout=30)
    if current_account_key() != owner:
        raise ValueError("Account changed during read.")
    if getattr(response, "status_code", None) != 200:
        raise ValueError("Skill read was not accepted.")
    envelope = parse_rpc_envelope(getattr(response, "text", ""), contract.rpc_id)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise ValueError("Skill response envelope was not accepted.")
    parsed = (parse_list(envelope.bodies[0]) if key == "managed_skills.list" else
              parse_get(envelope.bodies[0], skill_id=skill_id or "", include_instructions=include_instructions))
    if not parsed.ok or (key != "managed_skills.list" and parsed.status != "success"):
        raise ValueError("Skill response is unavailable or changed.")
    if current_account_key() != owner:
        raise ValueError("Account changed during read.")
    return parsed.value
