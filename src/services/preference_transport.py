"""Single-dispatch native permission preferences, without implicit authorization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..domain.preferences import PermissionRecord
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


class PreferenceTransportError(RuntimeError):
    """Safe transport failure; sent=None means the delivery outcome is unknown."""

    code = "UPSTREAM_CHANGED"

    def __init__(self, message: str, *, mutation_sent: bool | None = False):
        super().__init__(message)
        self.message = message
        self.mutation_sent = mutation_sent


def _account(owner: str | None, *, mutation_sent: bool | None = False) -> None:
    if not owner or current_account_key() != owner:
        raise PreferenceTransportError("The preference account changed or is unavailable.", mutation_sent=mutation_sent)


async def _request(client: Any, key: str, *, accept_empty_ack: bool = False, **arguments: Any) -> Any:
    contract = get_contract(key)
    owner = current_account_key()
    _account(owner)
    try:
        contract.build_payload(**arguments)
    except Exception:
        raise PreferenceTransportError("Preference validation failed before dispatch.") from None
    sent: bool | None = None if contract.mode == "mutation" else False
    try:
        response = await await_before_deadline(execute_contract(client, key, retry_count=0, **arguments), timeout=30)
    except Exception:
        raise PreferenceTransportError("The preference transport outcome is unavailable.", mutation_sent=sent) from None
    try:
        status = getattr(response, "status_code", None)
    except Exception:
        status = None
    if contract.mode == "mutation":
        sent = True if type(status) is int and 100 <= status <= 599 else None
    _account(owner, mutation_sent=sent)
    try:
        if type(status) is not int or status != 200:
            raise PreferenceTransportError("The preference HTTP response was not accepted.", mutation_sent=sent)
        envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
        if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
            raise PreferenceTransportError("The preference response envelope was not accepted.", mutation_sent=sent)
        parsed = parse_contract_body(contract, envelope.bodies[0])
        if not parsed.ok or (contract.mode == "mutation" and parsed.status != "success" and not accept_empty_ack):
            raise PreferenceTransportError("The preference response shape was not accepted.", mutation_sent=sent)
    except PreferenceTransportError:
        raise
    except Exception:
        raise PreferenceTransportError("The preference response could not be read.", mutation_sent=sent) from None
    _account(owner, mutation_sent=sent)
    return parsed.value


async def read_preferences(client: Any, *, tool_name: str | None = None,
                           function_name: str | None = None) -> tuple[PermissionRecord, ...]:
    """Read wire-scope 2 metadata only; the upstream exposes no pagination proof."""
    value = await _request(client, "preferences.list", tool_name=tool_name, function_name=function_name)
    records = tuple(PermissionRecord(**item) for item in value)
    if any(record.scope != 2 for record in records):
        raise PreferenceTransportError("The preference response included an unexpected scope.")
    if any((tool_name is not None and record.tool_name != tool_name)
           or (function_name is not None and record.function_name != function_name) for record in records):
        raise PreferenceTransportError("The preference function filter was not respected.")
    return records


async def read_permission_catalog(client: Any, *, locale: str = "zh-CN") -> tuple[PermissionRecord, ...]:
    """Discover native manageable actions; catalog defaults are not stored grants."""
    value = await _request(client, "preferences.catalog", locale=locale)
    return tuple(PermissionRecord(**item) for item in value)


async def write_preference(client: Any, record: PermissionRecord, *, state: int,
                           native_permission_id: list[Any] | None = None) -> None:
    """Send one exact update; the caller must independently read its effect."""
    if (not isinstance(record, PermissionRecord) or type(record.scope) is not int
            or record.scope not in (0, 2)):
        raise PreferenceTransportError("A persistent or catalog preference identity is required.")
    if (not isinstance(record.source, str) or record.source not in {"stored", "catalog"}
            or (record.scope == 0 and record.source != "catalog")):
        raise PreferenceTransportError("The preference source is not safely editable.")
    try:
        native = deepcopy(record.native_permission_id if native_permission_id is None else native_permission_id)
    except Exception:
        raise PreferenceTransportError("The preference identity could not be prepared.") from None
    if not isinstance(native, list) or not isinstance(record.native_permission_id, list):
        raise PreferenceTransportError("The preference identity could not be prepared.")
    candidate = PermissionRecord(native, record.grant_id, record.scope, record.context,
                                 record.state, record.label, record.category)
    if (record.tool_name is None or record.function_name is None
            or candidate.tool_name != record.tool_name or candidate.function_name != record.function_name):
        raise PreferenceTransportError("The preference update changed its observed function identity.")
    await _request(client, "preferences.update", grant_id=record.grant_id, native_permission_id=native, state=state)


async def revoke_preference(client: Any, record: PermissionRecord) -> None:
    """Internal exact-grant rollback; the caller must independently prove absence."""
    if (not isinstance(record, PermissionRecord) or record.source != "stored"
            or type(record.scope) is not int or record.scope != 2 or record.context is not None
            or not isinstance(record.grant_id, str) or not record.grant_id.strip()):
        raise PreferenceTransportError("A stored global preference grant is required for revocation.")
    identity = record.native_grant_identity
    if not isinstance(identity, list) or (identity and (
            len(identity) < 2 or identity[0] != record.grant_id
            or type(identity[1]) is not int or identity[1] != 2)):
        raise PreferenceTransportError("The preference grant identity is inconsistent.")
    await _request(client, "preferences.revoke", accept_empty_ack=True, grant_id=record.grant_id)
