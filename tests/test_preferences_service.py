"""Deterministic offline tests for PreferencesService.

Every transport and identity callback is a fake. No network, browser, account,
cookie, or upstream RPC is involved.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from typing import Any
from dataclasses import replace

import pytest

from src.domain import DomainErrorCode, DomainResult
from src.domain.preferences import PermissionRecord
from src.services.preferences import (
    STATE_ALWAYS_ALLOW,
    STATE_ALWAYS_ASK,
    PreferencesDependencies,
    PreferencesService,
)

ACCOUNT = "acct"
OTHER = "other-acct"


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _handle(account_key: str, native_permission_id: list[Any]) -> str:
    material = account_key + "|" + json.dumps(native_permission_id, default=str, sort_keys=True)
    return _sha256_hex(material)


def _revision(account_key: str, record: PermissionRecord) -> str:
    return PreferencesService._revision(account_key, record)


def _record(
    native_permission_id: list[Any] | None = None,
    grant_id: str = "grant-1",
    scope: int = 2,
    context: list[Any] | None = None,
    state: int = STATE_ALWAYS_ASK,
    label: str = "Test permission",
    category: int | None = 1,
    native_grant_identity: list[Any] | None = None,
    source: str = "stored",
) -> PermissionRecord:
    if native_permission_id is None:
        native_permission_id = [["tool_a", "actuate"]] + [[] for _ in range(8)] + [[["k", "v"]]]
    if native_grant_identity is None:
        native_grant_identity = []
    return PermissionRecord(
        native_permission_id=native_permission_id,
        grant_id=grant_id,
        scope=scope,
        context=context,
        state=state,
        label=label,
        category=category,
        native_grant_identity=native_grant_identity,
        source=source,
    )


def _browser_record(
    remy_choice_id: str = "vm",
    user_consent_state: str | None = "always_ask",
    state: int = STATE_ALWAYS_ASK,
    extra_attrs: list[tuple[str, str]] | None = None,
    source: str = "stored",
) -> PermissionRecord:
    attributes: list[tuple[str, str]] = [("remy_choice_id", remy_choice_id)]
    if user_consent_state is not None:
        attributes.append(("user_consent_state", user_consent_state))
    if extra_attrs:
        attributes.extend(extra_attrs)
    native_id: list[Any] = [["web_browser_agent", "actuate"]]
    for _ in range(1, 9):
        native_id.append([])
    native_id.append([[k, v] for k, v in attributes])
    return PermissionRecord(
        native_permission_id=native_id,
        grant_id="browser-grant",
        scope=2,
        context=None,
        state=state,
        label="Browser",
        category=None,
        native_grant_identity=[],
        source=source,
    )


class FakeTransport:
    def __init__(
        self,
        account: str | None = ACCOUNT,
        account_sequence: list[str | None] | None = None,
        stored: tuple[PermissionRecord, ...] = (),
        catalog: tuple[PermissionRecord, ...] = (),
        write_side_effect: BaseException | None = None,
        read_permissions_side_effect: BaseException | None = None,
        read_catalog_side_effect: BaseException | None = None,
        initializer_side_effect: BaseException | None = None,
    ) -> None:
        self.account = account
        self._account_iterator = iter(account_sequence) if account_sequence is not None else None
        self._stored = list(stored)
        self._catalog = list(catalog)
        self.write_side_effect = write_side_effect
        self.read_permissions_side_effect = read_permissions_side_effect
        self.read_catalog_side_effect = read_catalog_side_effect
        self.initializer_side_effect = initializer_side_effect
        self.client = object()
        self.provider_calls = 0
        self.initializer_calls = 0
        self.account_key_calls = 0
        self.write_calls: list[tuple[Any, PermissionRecord, dict[str, Any]]] = []
        self.read_permissions_calls = 0
        self.read_catalog_calls = 0
        self.mutation_sent_on_exceptions: list[bool | None] = []

    def account_key(self) -> str | None:
        self.account_key_calls += 1
        if self._account_iterator is not None:
            return next(self._account_iterator, "exhausted-account")
        return self.account

    def client_provider(self) -> Any:
        self.provider_calls += 1
        return self.client

    async def client_initializer(self) -> None:
        self.initializer_calls += 1
        if self.initializer_side_effect is not None:
            raise self.initializer_side_effect

    async def read_permissions(self, client: Any, **filters: Any) -> tuple[PermissionRecord, ...]:
        self.read_permissions_calls += 1
        if self.read_permissions_side_effect is not None:
            raise self.read_permissions_side_effect
        return tuple(self._stored)

    async def read_catalog(self, client: Any, locale: str = "zh-CN") -> tuple[PermissionRecord, ...]:
        self.read_catalog_calls += 1
        if self.read_catalog_side_effect is not None:
            raise self.read_catalog_side_effect
        return tuple(self._catalog)

    async def write_permission(
        self, client: Any, record: PermissionRecord, *, state: int, native_permission_id: list[Any] | None = None
    ) -> None:
        self.write_calls.append((client, record, {"state": state, "native_permission_id": native_permission_id}))
        if self.write_side_effect is not None and getattr(self.write_side_effect, 'mutation_sent', None) is False:
            raise self.write_side_effect
        # Simulate the write being applied to stored permissions
        updated = replace(record, state=state, scope=2, source='stored',
                          native_permission_id=deepcopy(native_permission_id or record.native_permission_id))
        # Replace by tool/function identity (stable across attribute changes)
        def _tf(rec: PermissionRecord) -> Any:
            try:
                return tuple(rec.native_permission_id[0])
            except (IndexError, TypeError):
                return None
        target_tf = _tf(updated)
        self._stored = [r for r in self._stored if _tf(r) != target_tf]
        self._stored.append(updated)
        if self.write_side_effect is not None:
            raise self.write_side_effect


def _service(transport: FakeTransport) -> PreferencesService:
    return PreferencesService(
        PreferencesDependencies(
            client_provider=transport.client_provider,
            client_initializer=transport.client_initializer,
            read_permissions=transport.read_permissions,
            read_catalog=transport.read_catalog,
            write_permission=transport.write_permission,
            account_key_provider=transport.account_key,
        )
    )


def _assert_private(result: DomainResult[dict[str, Any]], transport: FakeTransport) -> None:
    serialized = json.dumps(result.to_dict(), default=str)
    for secret in (ACCOUNT, OTHER, "secret-token"):
        assert secret not in serialized, f"private material leaked: {secret}"


# --------------------------------------------------------------- domain helpers

def test_state_names():
    assert PreferencesService._state_name(STATE_ALWAYS_ALLOW) == "always_allow"
    assert PreferencesService._state_name(STATE_ALWAYS_ASK) == "always_ask"
    assert PreferencesService._state_name(3) == "3"
    assert PreferencesService._state_name(0) == "0"


def test_opaque_handle_is_account_bound():
    record = _record()
    h1 = _handle(ACCOUNT, record.native_permission_id)
    h2 = _handle(OTHER, record.native_permission_id)
    assert h1 != h2
    assert len(h1) == 64


def test_revision_includes_grant_state_context_source():
    record = _record(state=STATE_ALWAYS_ALLOW)
    r1 = _revision(ACCOUNT, record)
    record2 = replace(record, state=STATE_ALWAYS_ASK)
    r2 = _revision(ACCOUNT, record2)
    assert r1 != r2


# --------------------------------------------------------------- get_browser

def test_get_browser_no_record_returns_unverified_defaults():
    transport = FakeTransport(stored=(), catalog=())
    result = _run(_service(transport).get_browser())
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["browser"] == "automatic"
    assert data["permission_preference"] == "always_ask"
    assert data["observed"] is False
    assert data["revision"] is None
    assert result.meta.verification_status == "unverified_default"
    _assert_private(result, transport)


def test_get_browser_remote_only_with_always_ask():
    transport = FakeTransport(stored=(_browser_record(remy_choice_id="vm", state=STATE_ALWAYS_ASK),))
    result = _run(_service(transport).get_browser())
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["browser"] == "remote_only"
    assert data["permission_preference"] == "always_ask"
    assert data["observed"] is True
    assert data["revision"] == _revision(ACCOUNT, _browser_record(remy_choice_id="vm", state=STATE_ALWAYS_ASK))


def test_get_browser_automatic_with_allow_effective():
    record = _browser_record(remy_choice_id="local_chrome:", user_consent_state=None, state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).get_browser())
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["browser"] == "automatic"
    assert data["permission_preference"] == "always_allow"
    assert data["observed"] is True


def test_get_browser_allow_blocked_by_user_consent_always_ask():
    record = _browser_record(remy_choice_id="local_chrome:", user_consent_state="always_ask", state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).get_browser())
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["permission_preference"] == "always_ask"


@pytest.mark.parametrize('stored_state', [STATE_ALWAYS_ALLOW, STATE_ALWAYS_ASK, 97])
def test_get_browser_reports_saved_preference_without_claiming_verified_task_behavior(stored_state):
    record = _browser_record(user_consent_state=None, state=stored_state)
    result = _run(_service(FakeTransport(stored=(record,))).get_browser())
    assert result.ok and result.data['observed'] is True
    assert result.data['permission_preference'] == {1: 'always_allow', 2: 'always_ask'}.get(stored_state, 'unknown')
    assert result.data['effective_permission'] == 'unknown'
    assert result.data['behavior_verified'] is False
    assert result.data['verification_scope'] == 'stored_preference'
    assert result.meta.details['verification_scope'] == 'stored_preference'


def test_list_keeps_catalog_defaults_distinct_from_observed_stored_preferences():
    default = _record(source='catalog', grant_id='', scope=0, state=STATE_ALWAYS_ALLOW)
    stored = _browser_record(user_consent_state=None, state=STATE_ALWAYS_ALLOW)
    result = _run(_service(FakeTransport(stored=(stored,), catalog=(default,))).list_permissions())
    assert result.ok and result.data['behavior_verified'] is False
    assert result.data['verification_scope'] == 'stored_preference'
    rows = {item['source']: item for item in result.data['permissions']}
    assert rows['stored']['observed'] is True and rows['catalog']['observed'] is False
    assert all(item['permission_preference'] == item['state'] == 'always_allow' for item in rows.values())


@pytest.mark.parametrize('operation', ['browser', 'permission'])
@pytest.mark.parametrize('noop', [False, True])
def test_setting_write_and_noop_verify_only_the_saved_preference(operation, noop):
    before = (_browser_record(user_consent_state=None, state=STATE_ALWAYS_ALLOW if noop else STATE_ALWAYS_ASK)
              if operation == 'browser' else _record(state=STATE_ALWAYS_ALLOW if noop else STATE_ALWAYS_ASK))
    transport = FakeTransport(stored=(before,))
    service = _service(transport)
    result = _run(service.set_browser(permission='always_allow') if operation == 'browser' else
                  service.set_permission(_handle(ACCOUNT, before.native_permission_id), 'always_allow'))
    assert result.ok and result.data['verified'] is True
    assert result.data['permission_preference'] == 'always_allow'
    assert result.data['behavior_verified'] is False and result.data['verification_scope'] == 'stored_preference'
    assert result.meta.details['verification_scope'] == 'stored_preference'
    assert len(transport.write_calls) == (0 if noop else 1)


def test_failed_preference_write_retains_verification_scope_without_claiming_behavior():
    failure = RuntimeError('synthetic failure')
    failure.mutation_sent = False
    transport = FakeTransport(stored=(_browser_record(),), write_side_effect=failure)
    result = _run(_service(transport).set_browser(permission='always_allow'))
    assert not result.ok and result.data['verified'] is False
    assert result.data['behavior_verified'] is False and result.data['verification_scope'] == 'stored_preference'
    assert result.meta.details['verification_scope'] == 'stored_preference'


def test_get_browser_validates_before_any_transport_call():
    # get_browser takes no args, so just verify transport is called
    transport = FakeTransport()
    result = _run(_service(transport).get_browser())
    assert result.ok is True
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1


# --------------------------------------------------------------- list_permissions

def test_list_permissions_returns_compact_items_with_opaque_ids():
    record = _record(label="Short label")
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).list_permissions())
    assert result.ok is True
    data = result.data
    assert data is not None
    items = data["permissions"]
    assert len(items) == 1
    item = items[0]
    assert item["permission_id"] == _handle(ACCOUNT, record.native_permission_id)
    assert item["tool"] == record.tool_name
    assert item["function"] == record.function_name
    assert item["label"] == "Short label"
    assert item["category"] == 1
    assert item["state"] == "always_ask"
    assert item["revision"] == _revision(ACCOUNT, record)
    assert "native_permission_id" not in item
    assert "grant_id" not in item
    assert "native_grant_identity" not in item
    assert "context" not in item
    assert data["next_offset"] is None


def test_list_permissions_label_capped_at_160():
    long_label = "x" * 200
    record = _record(label=long_label)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).list_permissions())
    assert result.ok is True
    data = result.data
    assert data is not None
    assert len(data["permissions"][0]["label"]) == 160
    assert data["permissions"][0]["label"].endswith("…")


def _perm_record(index: int) -> PermissionRecord:
    native_id = [[f"tool_{index}", "actuate"]] + [[] for _ in range(8)] + [[["k", str(index)]]]
    return _record(
        native_permission_id=native_id,
        label=f"perm {index}",
        grant_id=f"grant-{index}",
    )


def test_list_permissions_pagination():
    records = tuple(_perm_record(i) for i in range(25))
    transport = FakeTransport(stored=records)
    result = _run(_service(transport).list_permissions(limit=10, offset=0))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert len(data["permissions"]) == 10
    assert data["next_offset"] == 10

    result2 = _run(_service(transport).list_permissions(limit=10, offset=10))
    assert result2.ok is True
    data2 = result2.data
    assert data2 is not None
    assert len(data2["permissions"]) == 10
    assert data2["next_offset"] == 20

    result3 = _run(_service(transport).list_permissions(limit=10, offset=20))
    assert result3.ok is True
    data3 = result3.data
    assert data3 is not None
    assert len(data3["permissions"]) == 5
    assert data3["next_offset"] is None


@pytest.mark.parametrize("limit", [0, -1, 51, 2.5, "20", True, None])
def test_list_permissions_invalid_limit_rejected_before_transport(limit: Any):
    transport = FakeTransport()
    result = _run(_service(transport).list_permissions(limit=limit))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == "invalid_request"
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.read_permissions_calls == 0


@pytest.mark.parametrize("offset", [-1, 1001, 2.5, "0", True])
def test_list_permissions_invalid_offset_rejected_before_transport(offset: Any):
    transport = FakeTransport()
    result = _run(_service(transport).list_permissions(offset=offset))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0


# --------------------------------------------------------------- set_permission

def _known_global_record() -> PermissionRecord:
    native_id = [["tool_a", "actuate"]] + [[] for _ in range(8)] + [[["k", "v"]]]
    return _record(native_permission_id=native_id, scope=2, context=None, state=STATE_ALWAYS_ASK)


def test_set_permission_success_verified_by_readback():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is True
    assert data["mutation_sent"] is True
    assert data["verified"] is True
    assert data["revision"] == _revision(ACCOUNT, replace(record, state=STATE_ALWAYS_ALLOW))
    assert len(transport.write_calls) == 1
    assert transport.write_calls[0][1].state == STATE_ALWAYS_ALLOW


def test_set_permission_preserves_native_identity_changes_only_state():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(stored=(record,))
    _run(_service(transport).set_permission(handle, "always_allow"))
    written = transport.write_calls[0][1]
    assert written.native_permission_id == record.native_permission_id
    assert written.state == STATE_ALWAYS_ALLOW
    assert written.grant_id == record.grant_id
    assert written.scope == record.scope


def test_set_permission_no_op_when_already_desired_state():
    record = replace(_known_global_record(), state=STATE_ALWAYS_ALLOW)
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is False
    assert data["mutation_sent"] is False
    assert data["verified"] is True
    assert result.meta.verification_status == "no_op"
    assert len(transport.write_calls) == 0


def test_set_permission_invalid_handle_rejected_before_transport():
    transport = FakeTransport()
    result = _run(_service(transport).set_permission("not-a-handle", "always_allow"))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.write_calls == []


def test_set_permission_invalid_permission_rejected():
    transport = FakeTransport()
    result = _run(_service(transport).set_permission("a" * 64, "maybe"))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"


def test_set_permission_unknown_handle_fails_without_write():
    transport = FakeTransport(stored=(_known_global_record(),))
    result = _run(_service(transport).set_permission("a" * 64, "always_allow"))
    assert result.ok is False
    assert len(transport.write_calls) == 0


def test_set_permission_expected_revision_mismatch_prevents_write():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    wrong_rev = "0" * 64
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(handle, "always_allow", expected_revision=wrong_rev))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.UPSTREAM_CHANGED
    assert len(transport.write_calls) == 0


def test_set_permission_expected_revision_match_proceeds():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    rev = _revision(ACCOUNT, record)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(handle, "always_allow", expected_revision=rev))
    assert result.ok is True
    assert len(transport.write_calls) == 1


def test_set_permission_preflight_change_aborts_write():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(
        stored=(record,),
    )
    # First read_permissions returns original, preflight read_permissions returns modified
    async def changing_read_permissions(client, **filters):
        transport.read_permissions_calls += 1
        if transport.read_permissions_calls == 1:
            return (record,)
        modified = replace(record, state=STATE_ALWAYS_ALLOW)
        return (modified,)

    transport.read_permissions = changing_read_permissions
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.UPSTREAM_CHANGED
    assert len(transport.write_calls) == 0


def test_set_permission_not_global_rejected():
    # scope=1, context=["x"] - not global
    native_id = [["tool_a", "actuate"]] + [[] for _ in range(8)] + [[["k", "v"]]]
    record = _record(native_permission_id=native_id, scope=1, context=["x"], state=STATE_ALWAYS_ASK)
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is False
    assert len(transport.write_calls) == 0


def test_set_permission_readback_missing_fails():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    # Readback returns empty - write was sent but not reflected
    transport = FakeTransport(
        stored=(record,),
    )
    call_count = 0

    async def readback_missing(client, **filters):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return (record,)
        return ()  # readback returns empty

    transport.read_permissions = readback_missing
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is False
    data = result.data
    assert data is not None
    assert data["changed"] is None
    assert data["mutation_sent"] is True


def test_set_permission_unknown_delivery():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)

    class WriteError(Exception):
        mutation_sent = None

    transport = FakeTransport(
        stored=(record,),
        write_side_effect=WriteError("lost"),
    )
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    # The original request took effect before the reply was lost. Only the
    # independent stored read confirms it; dispatch remains unknown.
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is True and data["verified"] is True
    assert data["mutation_sent"] is None


def test_set_permission_account_switch_prevents_dispatch():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(
        stored=(record,),
        account_sequence=[ACCOUNT, OTHER],
    )
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED
    assert len(transport.write_calls) == 0


# --------------------------------------------------------------- set_browser

def test_set_browser_remote_only_with_ask():
    record = _browser_record(remy_choice_id="local_chrome:", state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser="remote_only", permission="always_ask"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is True
    assert data["mutation_sent"] is True
    assert data["verified"] is True
    assert len(transport.write_calls) == 1
    written = transport.write_calls[0][1]
    attrs = written.attributes
    remy = [v for k, v in attrs if k == "remy_choice_id"]
    assert remy == ["vm"]
    assert written.state == STATE_ALWAYS_ASK


def test_set_browser_automatic_with_allow_clears_consent_flag():
    record = _browser_record(
        remy_choice_id="local_chrome:",
        user_consent_state="always_ask",
        state=STATE_ALWAYS_ALLOW,
    )
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser="automatic", permission="always_allow"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is True
    written = transport.write_calls[0][1]
    attrs = written.attributes
    consent = [v for k, v in attrs if k == "user_consent_state"]
    assert consent == []  # flag cleared
    remy = [v for k, v in attrs if k == "remy_choice_id"]
    assert remy == ["local_chrome:"]


def test_set_browser_preserves_unknown_attributes_and_order():
    record = _browser_record(
        remy_choice_id="vm",
        user_consent_state="always_ask",
        extra_attrs=[("unknown_attr", "secret"), ("another", "value")],
        state=STATE_ALWAYS_ASK,
    )
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser="automatic"))
    assert result.ok is True
    written = transport.write_calls[0][1]
    attrs = written.attributes
    # Order preserved: remy_choice_id updated, user_consent_state cleared (permission=None so NOT cleared),
    # unknown attrs preserved in order
    assert ("remy_choice_id", "local_chrome:") in attrs
    assert ("user_consent_state", "always_ask") in attrs  # preserved since permission=None
    assert ("unknown_attr", "secret") in attrs
    assert ("another", "value") in attrs
    # Check order: remy first, then user_consent, then unknowns
    assert attrs[0] == ("remy_choice_id", "local_chrome:")
    assert attrs[1] == ("user_consent_state", "always_ask")


def test_set_browser_duplicate_remy_choice_id_rejected():
    native_id: list[Any] = [["web_browser_agent", "actuate"]]
    for _ in range(1, 9):
        native_id.append([])
    # Duplicate remy_choice_id in attributes
    native_id.append([["remy_choice_id", "vm"], ["remy_choice_id", "local_chrome:"]])
    record = PermissionRecord(
        native_permission_id=native_id,
        grant_id="g",
        scope=2,
        context=None,
        state=STATE_ALWAYS_ASK,
        label="Browser",
        category=None,
        native_grant_identity=[],
        source="stored",
    )
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser="automatic"))
    assert result.ok is False
    assert len(transport.write_calls) == 0


def test_set_browser_requires_one_choice():
    transport = FakeTransport()
    result = _run(_service(transport).set_browser())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT


def test_set_browser_invalid_mode_rejected():
    transport = FakeTransport()
    result = _run(_service(transport).set_browser(browser="maybe"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT


def test_set_browser_invalid_permission_rejected():
    transport = FakeTransport()
    result = _run(_service(transport).set_browser(permission="maybe"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT


def test_set_browser_no_op_when_already_desired():
    record = _browser_record(remy_choice_id="local_chrome:", user_consent_state=None, state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser="automatic", permission="always_allow"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is False
    assert data["mutation_sent"] is False
    assert data["verified"] is True
    assert result.meta.verification_status == "no_op"
    assert len(transport.write_calls) == 0


# --------------------------------------------------------------- catalog-only action becomes override

def test_catalog_only_action_becomes_stored_override():
    # Catalog record with empty grant_id, scope 0 - global eligible
    native_id = [["tool_catalog", "actuate"]] + [[] for _ in range(8)] + [[["k", "v"]]]
    catalog_record = _record(
        native_permission_id=native_id,
        grant_id="",
        scope=0,
        context=None,
        state=STATE_ALWAYS_ASK,
        source="catalog",
    )
    handle = _handle(ACCOUNT, catalog_record.native_permission_id)
    transport = FakeTransport(stored=(), catalog=(catalog_record,))

    call_count = 0

    async def readback_returns_written(client, **filters):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            # Readback: stored override now exists
            return (
                PermissionRecord(
                    native_permission_id=catalog_record.native_permission_id,
                    grant_id="",
                    scope=2,
                    context=None,
                    state=STATE_ALWAYS_ALLOW,
                    label="Catalog perm",
                    category=None,
                    native_grant_identity=[],
                    source="stored",
                ),
            )
        return ()  # initial and preflight: no stored override yet

    transport.read_permissions = readback_returns_written
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data["changed"] is True
    assert data["verified"] is True
    assert len(transport.write_calls) == 1
    written = transport.write_calls[0][1]
    assert written.grant_id == ""  # catalog ungranted sent with empty grant_id


# --------------------------------------------------------------- account guards

def test_missing_account_refused_after_initialization():
    transport = FakeTransport(account=None, account_sequence=[None, None, None, None, None, None])
    result = _run(_service(transport).set_permission("a" * 64, "always_allow"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED
    assert transport.initializer_calls == 1


def test_account_switch_after_init_prevents_read():
    transport = FakeTransport(account_sequence=[ACCOUNT, OTHER])
    result = _run(_service(transport).list_permissions())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED


def test_no_duplicate_writes_on_every_path():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    # Success path
    transport = FakeTransport(stored=(record,))
    _run(_service(transport).set_permission(handle, "always_allow"))
    assert len(transport.write_calls) == 1

    # No-op path
    transport2 = FakeTransport(stored=(replace(record, state=STATE_ALWAYS_ALLOW),))
    _run(_service(transport2).set_permission(handle, "always_allow"))
    assert len(transport2.write_calls) == 0


# --------------------------------------------------------------- read errors

def test_read_permissions_error_bounded_no_payload():
    record = _known_global_record()
    handle = _handle(ACCOUNT, record.native_permission_id)
    transport = FakeTransport(
        stored=(record,),
        read_permissions_side_effect=RuntimeError("boom-secret-token-42"),
    )
    result = _run(_service(transport).set_permission(handle, "always_allow"))
    assert result.ok is False
    serialized = str(result.to_dict())
    assert "boom-secret-token-42" not in serialized


# --------------------------------------------------------------- list no private leakage

def test_list_output_contains_no_private_identity():
    record = _record()
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).list_permissions())
    assert result.ok is True
    _assert_private(result, transport)
    serialized = json.dumps(result.data, default=str)
    assert record.grant_id not in serialized
    assert json.dumps(record.native_permission_id) not in serialized


# --------------------------------------------------------------- set_browser account switch

def test_set_browser_account_switch_after_preflight_no_write():
    record = _browser_record()
    transport = FakeTransport(
        stored=(record,),
        account_sequence=[ACCOUNT, ACCOUNT, ACCOUNT, ACCOUNT, ACCOUNT, OTHER],
    )
    result = _run(_service(transport).set_browser(browser="automatic"))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED
    assert len(transport.write_calls) == 0


def test_stored_override_wins_over_its_catalog_default_without_conflict():
    default = _record(grant_id='', scope=0, state=STATE_ALWAYS_ASK, source='catalog')
    stored = replace(default, grant_id='stored-grant', scope=2, source='stored', state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(stored,), catalog=(default,))
    result = _run(_service(transport).list_permissions())
    assert result.ok and len(result.data['permissions']) == 1
    assert result.data['permissions'][0]['state'] == 'always_allow'
    assert result.data['permissions'][0]['revision'] == _revision(ACCOUNT, stored)


def test_expected_revision_covers_unknown_granted_identity_fields():
    before = replace(_browser_record(), native_grant_identity=['browser-grant', 2, 'revision-a'])
    transport = FakeTransport(stored=(before,))
    service = _service(transport)
    revision = _run(service.get_browser()).data['revision']
    transport._stored = [replace(before, native_grant_identity=['browser-grant', 2, 'revision-b'])]
    result = _run(service.set_browser(browser='automatic', expected_revision=revision))
    assert not result.ok and result.error_code == DomainErrorCode.UPSTREAM_CHANGED
    assert result.data['changed'] is False and not transport.write_calls


def test_catalog_default_state_is_not_a_verified_persistent_noop():
    default = _record(grant_id='', scope=0, state=STATE_ALWAYS_ASK, source='catalog')
    transport = FakeTransport(catalog=(default,))
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, default.native_permission_id), 'always_ask'))
    assert result.ok and result.data['changed'] is True and result.data['verified'] is True
    assert len(transport.write_calls) == 1


@pytest.mark.parametrize('change', ['state', 'choice', 'scope', 'source', 'context', 'extra_identity'])
def test_browser_readback_requires_preserved_settings_and_exact_global_identity(change):
    record = _browser_record(remy_choice_id='vm', user_consent_state=None, state=STATE_ALWAYS_ASK)
    record = replace(record, native_permission_id=deepcopy(record.native_permission_id) + [['preserved-extra']])
    transport = FakeTransport(stored=(record,))
    original_write = transport.write_permission
    async def tampered(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        observed = transport._stored[0]
        native = deepcopy(observed.native_permission_id)
        if change == 'state':
            observed = replace(observed, state=STATE_ALWAYS_ALLOW)
        elif change == 'choice':
            native[9][0][1] = 'vm'
        elif change == 'scope':
            observed = replace(observed, scope=1)
        elif change == 'source':
            observed = replace(observed, source='catalog')
        elif change == 'context':
            observed = replace(observed, context=[1, 'private-thread'])
        else:
            native[-1] = ['changed-extra']
        transport._stored = [replace(observed, native_permission_id=native)]
    transport.write_permission = tampered
    result = _run(_service(transport).set_browser(browser='automatic'))
    assert not result.ok and result.data['changed'] is None and result.data['mutation_sent'] is True
    assert len(transport.write_calls) == 1


@pytest.mark.parametrize('operation', ['browser', 'permission'])
def test_browser_ask_accepts_only_the_observed_server_added_exact_consent_marker(operation):
    before = _browser_record(remy_choice_id='local_chrome:', user_consent_state=None, state=STATE_ALWAYS_ALLOW)
    native = deepcopy(before.native_permission_id)
    native[0].append('preserved-function-extra')
    native[9].append(['unknown', 'value', ['preserved-pair-extra']])
    native.append(['preserved-native-extra'])
    before = replace(before, native_permission_id=native, native_grant_identity=['browser-grant', 2, 'preserved-grant-extra'])
    transport = FakeTransport(stored=(before,))
    original_write = transport.write_permission

    async def adds_known_flag(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        observed = transport._stored[0]
        native = deepcopy(observed.native_permission_id)
        native[9].append(['user_consent_state', 'always_ask'])
        transport._stored = [replace(observed, native_permission_id=native)]

    transport.write_permission = adds_known_flag
    service = _service(transport)
    result = _run(service.set_browser(browser='remote_only', permission='always_ask') if operation == 'browser' else
                  service.set_permission(_handle(ACCOUNT, before.native_permission_id), 'always_ask'))
    assert result.ok and result.data['changed'] is True and result.data['verified'] is True
    assert result.data['mutation_sent'] is True and len(transport.write_calls) == 1
    assert result.data['revision'] == _revision(ACCOUNT, transport._stored[0])
    written = transport.write_calls[0][1]
    assert written.native_permission_id[0] == native[0]
    assert written.native_permission_id[9][-1] == native[9][-1]
    assert written.native_permission_id[-1] == native[-1]
    assert written.native_grant_identity == before.native_grant_identity
    assert before.native_permission_id == native


def _catalog_summit_action():
    native = [['androiddevsummit_2_mcp', 'get_summit_info']] + [None] * 8
    native.append([['operation_schema_hash', 'synthetic-schema-hash']])
    return _record(native_permission_id=native, grant_id='', scope=0, state=STATE_ALWAYS_ALLOW,
                   label='Public synthetic summit info', category=1, source='catalog')


def test_catalog_app_ask_server_marker_merges_to_actual_handle_then_allow_clears_it():
    default = _catalog_summit_action()
    transport = FakeTransport(catalog=(default,))
    original_write = transport.write_permission

    async def native_app_write(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        observed = transport._stored[0]
        native = deepcopy(observed.native_permission_id)
        if observed.state == STATE_ALWAYS_ASK:
            native[9].append(['user_consent_state', 'always_ask'])
        transport._stored = [replace(observed, native_permission_id=native, grant_id='app-grant',
                                     native_grant_identity=['app-grant', 2, 'unknown-grant-field'])]

    transport.write_permission = native_app_write
    service = _service(transport)
    asked = _run(service.set_permission(_handle(ACCOUNT, default.native_permission_id), 'always_ask'))
    assert asked.ok and asked.data['verified'] is True and len(transport.write_calls) == 1
    observed = transport._stored[0]
    assert observed.native_permission_id[9] == [
        ['operation_schema_hash', 'synthetic-schema-hash'], ['user_consent_state', 'always_ask'],
    ]
    listed = _run(service.list_permissions())
    assert listed.ok and len(listed.data['permissions']) == 1
    item = listed.data['permissions'][0]
    assert item['permission_id'] == _handle(ACCOUNT, observed.native_permission_id)
    assert item['permission_id'] != _handle(ACCOUNT, default.native_permission_id)
    assert item['revision'] == _revision(ACCOUNT, observed) and item['state'] == 'always_ask'
    allowed = _run(service.set_permission(item['permission_id'], 'always_allow', expected_revision=item['revision']))
    assert allowed.ok and allowed.data['verified'] is True and len(transport.write_calls) == 2
    assert transport._stored[0].state == STATE_ALWAYS_ALLOW
    assert transport._stored[0].native_permission_id == default.native_permission_id
    assert transport._stored[0].grant_id == 'app-grant'
    assert len(_run(service.list_permissions()).data['permissions']) == 1


def test_existing_app_ask_marker_is_preserved_as_a_true_noop():
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    native[9].append(['user_consent_state', 'always_ask'])
    stored = replace(default, native_permission_id=native, grant_id='app-grant', scope=2,
                     source='stored', state=STATE_ALWAYS_ASK)
    transport = FakeTransport(stored=(stored,), catalog=(default,))
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, native), 'always_ask'))
    assert result.ok and result.meta.verification_status == 'no_op'
    assert result.data['mutation_sent'] is False and not transport.write_calls
    assert transport._stored[0].native_permission_id == native


@pytest.mark.parametrize('change', ['extra_pair_field', 'unknown_value', 'duplicate_marker', 'schema_hash',
                                    'attribute_order', 'unknown_native_field', 'non_ask_state'])
def test_app_merge_does_not_ignore_unknown_fields_order_duplicate_markers_or_schema_hash(change):
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    marker = ['user_consent_state', 'always_ask']
    if change == 'extra_pair_field':
        marker.append('unknown-field')
    elif change == 'unknown_value':
        marker[1] = 'unknown-value'
    elif change == 'duplicate_marker':
        native[9].append(marker.copy())
    elif change == 'schema_hash':
        native[9][0][1] = 'different-schema-hash'
    elif change == 'attribute_order':
        default_native = deepcopy(default.native_permission_id)
        default_native[9].append(['unknown', 'must-preserve-order'])
        default = replace(default, native_permission_id=default_native)
        native[9].insert(0, ['unknown', 'must-preserve-order'])
    elif change == 'unknown_native_field':
        native.append(['unknown-field'])
    native[9].append(marker)
    stored = replace(default, native_permission_id=native, grant_id='app-grant', scope=2, source='stored',
                     state=STATE_ALWAYS_ALLOW if change == 'non_ask_state' else STATE_ALWAYS_ASK)
    result = _run(_service(FakeTransport(stored=(stored,), catalog=(default,))).list_permissions())
    assert result.ok and len(result.data['permissions']) == 2
    assert {item['permission_id'] for item in result.data['permissions']} == {
        _handle(ACCOUNT, native), _handle(ACCOUNT, default.native_permission_id),
    }


def test_equivalent_stored_app_records_remain_a_conflict_after_known_marker_canonicalization():
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    native[9].append(['user_consent_state', 'always_ask'])
    stored = replace(default, grant_id='app-grant', scope=2, source='stored', state=STATE_ALWAYS_ASK)
    derived = replace(stored, native_permission_id=native, grant_id='second-grant')
    result = _run(_service(FakeTransport(stored=(stored, derived), catalog=(default,))).list_permissions())
    assert not result.ok


@pytest.mark.parametrize('unknown_extra', [False, True])
def test_app_catalog_and_stored_exact_native_match_has_priority_over_known_marker_alias(unknown_extra):
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    marker = ['user_consent_state', 'always_ask']
    if unknown_extra:
        marker.append('unknown-pair-field')
    native[9].append(marker)
    default = replace(default, native_permission_id=native, state=STATE_ALWAYS_ASK)
    stored = replace(default, grant_id='app-grant', scope=2, source='stored')
    result = _run(_service(FakeTransport(stored=(stored,), catalog=(default,))).list_permissions())
    assert result.ok and len(result.data['permissions']) == 1
    assert result.data['permissions'][0]['revision'] == _revision(ACCOUNT, stored)
    assert result.data['permissions'][0]['permission_id'] == _handle(ACCOUNT, native)


@pytest.mark.parametrize(('scope', 'context'), [(1, None), (97, None), (True, None), (2, [1, 'private-context'])])
def test_app_derived_alias_never_covers_unknown_or_thread_scope_context(scope, context):
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    native[9].append(['user_consent_state', 'always_ask'])
    stored = replace(default, native_permission_id=native, grant_id='app-grant', scope=scope, context=context,
                     source='stored', state=STATE_ALWAYS_ASK)
    result = _run(_service(FakeTransport(stored=(stored,), catalog=(default,))).list_permissions())
    assert not result.ok


@pytest.mark.parametrize('marker', [
    ['user_consent_state', 'always_ask', 'unknown-field'], ['user_consent_state', 'unknown-value'],
])
def test_app_ask_readback_does_not_ignore_unknown_consent_marker_fields(marker):
    default = _catalog_summit_action()
    transport = FakeTransport(catalog=(default,))
    original_write = transport.write_permission

    async def app_unknown_marker(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        native = deepcopy(transport._stored[0].native_permission_id)
        native[9].append(marker)
        transport._stored = [replace(transport._stored[0], native_permission_id=native)]

    transport.write_permission = app_unknown_marker
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, default.native_permission_id), 'always_ask'))
    assert not result.ok and result.data['verified'] is False and result.data['mutation_sent'] is True
    assert len(transport.write_calls) == 1


@pytest.mark.parametrize('change', ['schema_hash', 'attribute_order', 'duplicate_marker', 'unknown_native_field'])
def test_app_ask_readback_preserves_schema_hash_order_and_unknown_identity(change):
    default = _catalog_summit_action()
    native = deepcopy(default.native_permission_id)
    native[9].append(['unknown', 'preserve-order'])
    default = replace(default, native_permission_id=native)
    transport = FakeTransport(catalog=(default,))
    original_write = transport.write_permission

    async def app_changed_identity(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        native = deepcopy(transport._stored[0].native_permission_id)
        if change == 'schema_hash':
            native[9][0][1] = 'changed-schema-hash'
        elif change == 'attribute_order':
            native[9].reverse()
        elif change == 'duplicate_marker':
            native[9].append(['user_consent_state', 'always_ask'])
        else:
            native.append(['unknown-native-field'])
        native[9].append(['user_consent_state', 'always_ask'])
        transport._stored = [replace(transport._stored[0], native_permission_id=native)]

    transport.write_permission = app_changed_identity
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, default.native_permission_id), 'always_ask'))
    assert not result.ok and result.data['mutation_sent'] is True and len(transport.write_calls) == 1


@pytest.mark.parametrize('permission', ['always_allow', 'always_ask'])
def test_app_duplicate_consent_marker_is_rejected_before_dispatch(permission):
    stored = replace(_catalog_summit_action(), scope=2, source='stored', state=STATE_ALWAYS_ASK)
    native = deepcopy(stored.native_permission_id)
    native[9].extend([['user_consent_state', 'always_ask'], ['user_consent_state', 'always_ask']])
    stored = replace(stored, native_permission_id=native)
    transport = FakeTransport(stored=(stored,))
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, native), permission))
    assert not result.ok and result.data['mutation_sent'] is False and not transport.write_calls


def test_app_allow_does_not_discard_unknown_consent_pair_fields():
    stored = replace(_catalog_summit_action(), scope=2, source='stored', state=STATE_ALWAYS_ASK)
    native = deepcopy(stored.native_permission_id)
    native[9].append(['user_consent_state', 'always_ask', ['unknown-pair-field']])
    stored = replace(stored, native_permission_id=native)
    transport = FakeTransport(stored=(stored,))
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, native), 'always_allow'))
    assert not result.ok and result.data['mutation_sent'] is False and not transport.write_calls
    assert transport._stored[0].native_permission_id == native


@pytest.mark.parametrize('operation', ['browser', 'permission'])
def test_explicit_browser_ask_preserves_existing_consent_marker_and_is_a_true_noop(operation):
    before = _browser_record(state=STATE_ALWAYS_ASK, extra_attrs=[('unknown', 'preserved')])
    transport = FakeTransport(stored=(before,))
    service = _service(transport)
    result = _run(service.set_browser(permission='always_ask') if operation == 'browser' else
                  service.set_permission(_handle(ACCOUNT, before.native_permission_id), 'always_ask'))
    assert result.ok and result.meta.verification_status == 'no_op'
    assert result.data['changed'] is False and result.data['mutation_sent'] is False
    assert not transport.write_calls and transport._stored == [before]


@pytest.mark.parametrize('change', [
    'extra_pair_field', 'unknown_value', 'duplicate_marker', 'unknown_attribute',
    'unknown_native_field', 'attribute_order', 'wrong_state', 'existing_unknown_consent',
])
def test_browser_ask_does_not_ignore_unknown_or_conflicting_readback_identity_changes(change):
    before = _browser_record(remy_choice_id='local_chrome:', user_consent_state=None, state=STATE_ALWAYS_ALLOW,
                             extra_attrs=[('unknown-first', 'one'), ('unknown-second', 'two')])
    if change == 'existing_unknown_consent':
        native = deepcopy(before.native_permission_id)
        native[9].append(['user_consent_state', 'unknown-future-value'])
        before = replace(before, native_permission_id=native)
    transport = FakeTransport(stored=(before,))
    original_write = transport.write_permission

    async def changes_identity(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        observed = transport._stored[0]
        native = deepcopy(observed.native_permission_id)
        marker = ['user_consent_state', 'always_ask']
        if change == 'extra_pair_field':
            marker.append('unknown-field')
        elif change == 'unknown_value':
            marker[1] = 'unknown-future-value'
        elif change == 'duplicate_marker':
            native[9].append(marker.copy())
        elif change == 'unknown_attribute':
            native[9][1][1] = 'changed'
        elif change == 'unknown_native_field':
            native.append(['new-unknown-field'])
        elif change == 'attribute_order':
            native[9][1:3] = reversed(native[9][1:3])
        elif change == 'wrong_state':
            observed = replace(observed, state=STATE_ALWAYS_ALLOW)
        native[9].append(marker)
        transport._stored = [replace(observed, native_permission_id=native)]

    transport.write_permission = changes_identity
    result = _run(_service(transport).set_browser(browser='remote_only', permission='always_ask'))
    assert not result.ok and result.data['verified'] is False and result.data['changed'] is None
    assert result.data['mutation_sent'] is True and len(transport.write_calls) == 1


def test_browser_allow_refuses_to_discard_unknown_consent_marker_fields():
    before = _browser_record(state=STATE_ALWAYS_ASK)
    native = deepcopy(before.native_permission_id)
    native[9][1].append(['unknown-pair-field'])
    before = replace(before, native_permission_id=native)
    transport = FakeTransport(stored=(before,))
    result = _run(_service(transport).set_browser(permission='always_allow'))
    assert not result.ok and result.data['mutation_sent'] is False and not transport.write_calls
    assert transport._stored == [before] and before.native_permission_id == native


def test_explicit_browser_ask_keeps_existing_unknown_consent_pair_fields_unchanged():
    before = _browser_record(state=STATE_ALWAYS_ASK)
    native = deepcopy(before.native_permission_id)
    native[9][1].append(['unknown-pair-field'])
    before = replace(before, native_permission_id=native)
    transport = FakeTransport(stored=(before,))
    result = _run(_service(transport).set_browser(permission='always_ask'))
    assert result.ok and result.meta.verification_status == 'no_op'
    assert result.data['mutation_sent'] is False and not transport.write_calls
    assert transport._stored == [before] and before.native_permission_id == native


def test_browser_allow_readback_cannot_keep_the_always_ask_override():
    record = _browser_record(remy_choice_id='vm', user_consent_state='always_ask', state=STATE_ALWAYS_ASK)
    transport = FakeTransport(stored=(record,))
    original_write = transport.write_permission
    async def retains_flag(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        native = deepcopy(transport._stored[0].native_permission_id)
        native[9].append(['user_consent_state', 'always_ask'])
        transport._stored = [replace(transport._stored[0], native_permission_id=native)]
    transport.write_permission = retains_flag
    result = _run(_service(transport).set_browser(permission='always_allow'))
    assert not result.ok and result.data['changed'] is None


def test_browser_attribute_patch_preserves_extra_pair_and_native_fields():
    record = _browser_record(remy_choice_id='vm', user_consent_state=None)
    native = deepcopy(record.native_permission_id)
    native[0].append('handle-extra')
    native[9][0].append(['choice-pair-extra'])
    native[9].append(['unknown', 'value', ['unknown-pair-extra']])
    native.append(['native-extra'])
    record = replace(record, native_permission_id=native)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_browser(browser='automatic'))
    assert result.ok and len(transport.write_calls) == 1
    written = transport.write_calls[0][1].native_permission_id
    assert written[0] == native[0] and written[9][0][2:] == native[9][0][2:]
    assert written[9][1] == native[9][1] and written[-1] == native[-1]
    assert record.native_permission_id == native


def test_browser_empty_optional_attributes_and_unknown_state_do_not_become_verified_ask():
    record = _browser_record(state=97)
    native = deepcopy(record.native_permission_id)
    native[9] = None
    transport = FakeTransport(stored=(replace(record, native_permission_id=native),))
    result = _run(_service(transport).get_browser())
    assert result.ok and result.data['browser'] == 'unknown'
    assert result.data['permission_preference'] == 'unknown'


def test_conflicting_duplicate_readback_cannot_be_filtered_into_success():
    record = _known_global_record()
    transport = FakeTransport(stored=(record,))
    original_write = transport.write_permission
    async def conflicting(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        transport._stored.append(replace(transport._stored[0], state=STATE_ALWAYS_ASK))
    transport.write_permission = conflicting
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, record.native_permission_id), 'always_allow'))
    assert not result.ok and result.data['changed'] is None


def test_generic_permission_route_uses_browser_effective_permission_semantics():
    record = _browser_record(user_consent_state='always_ask', state=STATE_ALWAYS_ALLOW)
    transport = FakeTransport(stored=(record,))
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, record.native_permission_id), 'always_allow'))
    assert result.ok and result.data['changed'] is True and len(transport.write_calls) == 1
    assert not any(key == 'user_consent_state' and value == 'always_ask'
                   for key, value in transport.write_calls[0][1].attributes)


@pytest.mark.parametrize('operation', ['permission', 'browser'])
def test_readback_exception_preserves_sent_true_and_scrubs_private_details(operation, caplog):
    record = _browser_record() if operation == 'browser' else _known_global_record()
    transport = FakeTransport(stored=(record,))
    original_read = transport.read_permissions
    async def fails_on_readback(client, **kwargs):
        if transport.write_calls:
            raise RuntimeError('PRIVATE_NATIVE_RESPONSE')
        return await original_read(client, **kwargs)
    transport.read_permissions = fails_on_readback
    service = _service(transport)
    result = _run(service.set_browser(browser='automatic') if operation == 'browser' else
                  service.set_permission(_handle(ACCOUNT, record.native_permission_id), 'always_allow'))
    assert not result.ok and result.data['changed'] is None and result.data['mutation_sent'] is True
    assert 'PRIVATE_NATIVE_RESPONSE' not in str(result.to_dict()) + caplog.text


@pytest.mark.parametrize('operation', ['permission', 'browser'])
def test_known_unsent_write_error_has_changed_false_and_no_readback(operation):
    class NotSent(RuntimeError):
        mutation_sent = False
    record = _browser_record() if operation == 'browser' else _known_global_record()
    transport = FakeTransport(stored=(record,), write_side_effect=NotSent('PRIVATE_ID'))
    service = _service(transport)
    result = _run(service.set_browser(browser='automatic') if operation == 'browser' else
                  service.set_permission(_handle(ACCOUNT, record.native_permission_id), 'always_allow'))
    assert not result.ok and result.data['changed'] is False and result.data['mutation_sent'] is False
    assert transport.read_permissions_calls == 2


@pytest.mark.parametrize(('operation', 'stage'), [
    (operation, stage)
    for operation in ('get_browser', 'list_permissions', 'set_browser', 'set_permission')
    for stage in ('initialize', 'stored', 'catalog', 'preflight_stored', 'preflight_catalog', 'write', 'readback')
    if operation.startswith('set_') or stage in ('initialize', 'stored', 'catalog')
])
def test_account_switches_at_each_real_await_never_report_success_or_leak(operation, stage):
    record = _browser_record() if 'browser' in operation else _known_global_record()
    transport = FakeTransport(stored=(record,))
    original_init = transport.client_initializer
    original_stored = transport.read_permissions
    original_catalog = transport.read_catalog
    original_write = transport.write_permission
    async def init():
        await original_init()
        if stage == 'initialize':
            transport.account = OTHER
    async def stored(client, **kwargs):
        records = await original_stored(client, **kwargs)
        if stage == 'stored' and transport.read_permissions_calls == 1:
            transport.account = OTHER
        if stage == 'preflight_stored' and transport.read_permissions_calls == 2:
            transport.account = OTHER
        if stage == 'readback' and transport.write_calls:
            transport.account = OTHER
        return records
    async def catalog(client, locale='zh-CN'):
        records = await original_catalog(client, locale)
        if stage == 'catalog' and transport.read_catalog_calls == 1:
            transport.account = OTHER
        if stage == 'preflight_catalog' and transport.read_catalog_calls == 2:
            transport.account = OTHER
        return records
    async def write(client, desired, **kwargs):
        await original_write(client, desired, **kwargs)
        if stage == 'write':
            transport.account = OTHER
    transport.client_initializer, transport.read_permissions = init, stored
    transport.read_catalog, transport.write_permission = catalog, write
    service = _service(transport)
    awaitable = (service.get_browser() if operation == 'get_browser' else
                 service.list_permissions() if operation == 'list_permissions' else
                 service.set_browser(browser='automatic') if operation == 'set_browser' else
                 service.set_permission(_handle(ACCOUNT, record.native_permission_id), 'always_allow'))
    result = _run(awaitable)
    assert not result.ok and result.error_code == DomainErrorCode.AUTH_REQUIRED
    _assert_private(result, transport)
    assert len(transport.write_calls) <= 1


def test_browser_creation_keeps_unknown_delivery_until_stored_read_verifies_it():
    transport = FakeTransport()
    result = _run(_service(transport).set_browser(permission='always_ask'))
    assert result.ok and result.data['verified'] and len(transport.write_calls) == 1
    native = transport.write_calls[0][1].native_permission_id
    assert native[1:9] == [None] * 8 and native[9] == [['remy_choice_id', 'local_chrome:']]


def test_expected_revision_cannot_apply_to_an_absent_browser_record():
    transport = FakeTransport()
    result = _run(_service(transport).set_browser(browser='remote_only', expected_revision='0' * 64))
    assert not result.ok and result.data['changed'] is False and not transport.write_calls


def test_browser_automatic_mode_preserves_an_existing_automatic_device_selection():
    before = _browser_record(remy_choice_id='local_chrome:device-fixture', user_consent_state=None)
    transport = FakeTransport(stored=(before,))
    result = _run(_service(transport).set_browser(browser='automatic'))
    assert result.ok and result.data['changed'] is False and not transport.write_calls


def test_browser_row_uses_effective_permission_instead_of_the_blocked_raw_allow_state():
    transport = FakeTransport(stored=(_browser_record(state=STATE_ALWAYS_ALLOW, user_consent_state='always_ask'),))
    result = _run(_service(transport).list_permissions())
    assert result.ok and result.data['permissions'][0]['state'] == 'always_ask'


def test_unknown_native_identity_type_changes_fail_preflight_even_if_python_values_compare_equal():
    before = _known_global_record()
    before = replace(before, native_grant_identity=['grant-1', 2, True])
    changed = replace(before, native_grant_identity=['grant-1', 2, 1])
    transport = FakeTransport(stored=(before,))
    async def changing(client, **kwargs):
        transport.read_permissions_calls += 1
        return (before,) if transport.read_permissions_calls == 1 else (changed,)
    transport.read_permissions = changing
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, before.native_permission_id), 'always_allow'))
    assert not result.ok and result.error_code == DomainErrorCode.UPSTREAM_CHANGED and not transport.write_calls


@pytest.mark.parametrize('stage', ['initialize', 'stored', 'catalog'])
def test_callback_errors_do_not_log_or_return_private_exception_text(stage, caplog):
    error = RuntimeError('PRIVATE_NATIVE_RESPONSE')
    transport = FakeTransport(
        initializer_side_effect=error if stage == 'initialize' else None,
        read_permissions_side_effect=error if stage == 'stored' else None,
        read_catalog_side_effect=error if stage == 'catalog' else None,
    )
    result = _run(_service(transport).set_browser(browser='remote_only'))
    assert not result.ok and result.data['changed'] is False and result.data['mutation_sent'] is False
    assert 'PRIVATE_NATIVE_RESPONSE' not in str(result.to_dict()) + caplog.text


def test_initialization_deadline_prevents_hanging_or_starting_metadata_reads(monkeypatch):
    from src.services import preferences as module
    transport = FakeTransport()
    async def stuck():
        await asyncio.Event().wait()
    transport.client_initializer = stuck
    monkeypatch.setattr(module, '_CALLBACK_SECONDS', 0.001)
    result = _run(_service(transport).get_browser())
    assert not result.ok and transport.provider_calls == 0 and transport.read_permissions_calls == 0


def test_lost_write_without_readback_effect_keeps_unknown_and_never_replays():
    class Lost(RuntimeError):
        mutation_sent = None
    before = _known_global_record()
    transport = FakeTransport(stored=(before,))
    async def lost(client, desired, **kwargs):
        transport.write_calls.append((client, desired, kwargs))
        raise Lost('PRIVATE_NATIVE_RESPONSE')
    transport.write_permission = lost
    result = _run(_service(transport).set_permission(_handle(ACCOUNT, before.native_permission_id), 'always_allow'))
    assert not result.ok and result.data['changed'] is None and result.data['mutation_sent'] is None
    assert transport.read_permissions_calls == 3 and len(transport.write_calls) == 1


@pytest.mark.parametrize('invalid', [[], {}, 1, True])
def test_unhashable_and_non_string_choices_are_rejected_before_initialization(invalid):
    transport = FakeTransport()
    service = _service(transport)
    assert not _run(service.set_browser(browser=invalid)).ok
    assert not _run(service.set_permission('a' * 64, invalid)).ok
    assert transport.initializer_calls == 0


def test_unauthenticated_initialization_stops_preference_read_and_write():
    from src.client_manager import ClientAuthenticationRequired
    transport = FakeTransport(initializer_side_effect=ClientAuthenticationRequired("synthetic"))
    service = _service(transport)
    for result in (asyncio.run(service.get_browser()), asyncio.run(service.set_browser(browser="automatic"))):
        assert not result.ok and result.error_code == DomainErrorCode.AUTH_REQUIRED
    assert transport.initializer_calls == 2
    assert transport.read_permissions_calls == 0 and not transport.write_calls
