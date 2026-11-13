"""Account-bound permission preferences with single writes and exact read-back.

Only returned native actions and the observed browser identity are writable.
Catalog defaults are distinct from stored grants, and no native identity or
callback exception text is included in public results or logs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from ..client_manager import ClientAuthenticationRequired
from ..domain import DomainErrorCode, DomainResult
from ..domain.preferences import PermissionRecord
from .deadlines import await_before_deadline

STATE_ALWAYS_ALLOW = 1
STATE_ALWAYS_ASK = 2
_STATE_NAMES = {1: "always_allow", 2: "always_ask"}
_BROWSER_TOOL_FUNCTION = ("web_browser_agent", "actuate")
BROWSER_ATTRIBUTE_INDEX = 9
ATTR_REMY_CHOICE_ID = "remy_choice_id"
ATTR_USER_CONSENT_STATE = "user_consent_state"
CHOICE_REMOTE_ONLY = "vm"
CHOICE_AUTOMATIC_PREFIX = "local_chrome:"
CHOICE_AUTOMATIC_CANONICAL = "local_chrome:"
MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 50, 20
MIN_OFFSET, MAX_OFFSET, MAX_LABEL = 0, 1000, 160
_HEX64 = frozenset("0123456789abcdef")
_BROWSER_MODES = frozenset({"automatic", "remote_only"})
_PERMISSIONS = frozenset({"always_ask", "always_allow"})
_CALLBACK_SECONDS = 30
_SETTING_EVIDENCE = {"behavior_verified": False, "verification_scope": "stored_preference"}
_Result = DomainResult[dict[str, Any]]
_Records = tuple[PermissionRecord, ...]


@dataclass(frozen=True)
class PreferencesDependencies:
    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    read_permissions: Callable[..., Awaitable[_Records]]
    read_catalog: Callable[[Any, str], Awaitable[_Records]]
    write_permission: Callable[..., Awaitable[None]]
    account_key_provider: Callable[[], str | None]


class PreferencesService:
    """Four account operations; callback waits and failure handling are shared."""

    def __init__(self, dependencies: PreferencesDependencies) -> None:
        self._dependencies = dependencies

    def _account_identity(self) -> str | None:
        try:
            value = self._dependencies.account_key_provider()
            return value if isinstance(value, str) and value else None
        except Exception:
            return None

    def _account_matches(self, owner: str | None) -> bool:
        return owner is not None and self._account_identity() == owner

    @staticmethod
    def _opaque_handle(account_key: str, native_permission_id: list[Any]) -> str:
        material = account_key + "|" + json.dumps(native_permission_id, default=str, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _revision(account_key: str, record: PermissionRecord) -> str:
        # Bind every native identity component, including unknown granted-ID
        # fields; display labels are not part of a permission's CAS identity.
        identity = [record.native_permission_id, record.grant_id, record.scope,
                    record.context, record.state, record.native_grant_identity, record.source]
        material = account_key + "|" + json.dumps(identity, default=str, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_opaque64(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(char in _HEX64 for char in value)

    @staticmethod
    def _valid_limit(value: Any) -> bool:
        return type(value) is int and MIN_LIMIT <= value <= MAX_LIMIT

    @staticmethod
    def _valid_offset(value: Any) -> bool:
        return type(value) is int and MIN_OFFSET <= value <= MAX_OFFSET

    @staticmethod
    def _state_name(state: int) -> str:
        return _STATE_NAMES.get(state, str(state)) if type(state) is int else "unknown"

    @staticmethod
    def _is_global(record: PermissionRecord) -> bool:
        return (record.context is None and type(record.scope) is int
                and ((record.source == "stored" and record.scope == 2)
                     or (record.source == "catalog" and record.scope in (0, 2))))

    @classmethod
    def _records_differ(cls, a: PermissionRecord, b: PermissionRecord) -> bool:
        # Python considers True and 1 equal inside lists; native JSON identity
        # comparisons must still distinguish their wire types.
        return a != b or cls._revision("", a) != cls._revision("", b)

    @classmethod
    def _same_native(cls, a: list[Any], b: list[Any]) -> bool:
        return cls._opaque_handle("", a) == cls._opaque_handle("", b)

    @classmethod
    def _native_without_derived_ask_marker(cls, record: PermissionRecord) -> list[Any]:
        native = record.native_permission_id
        if type(record.state) is not int or record.state != STATE_ALWAYS_ASK or len(native) <= BROWSER_ATTRIBUTE_INDEX:
            return native
        attributes = native[BROWSER_ATTRIBUTE_INDEX]
        if not isinstance(attributes, list):
            return native
        consent = [index for index, pair in enumerate(attributes)
                   if isinstance(pair, list) and pair and pair[0] == ATTR_USER_CONSENT_STATE]
        if len(consent) != 1 or attributes[consent[0]] != [ATTR_USER_CONSENT_STATE, "always_ask"]:
            return native
        normalized = deepcopy(native)
        normalized[BROWSER_ATTRIBUTE_INDEX] = attributes[:consent[0]] + attributes[consent[0] + 1:]
        return normalized

    @classmethod
    def _same_readback_identity(cls, desired: PermissionRecord, observed: PermissionRecord) -> bool:
        if cls._same_native(desired.native_permission_id, observed.native_permission_id):
            return True
        if (type(desired.state) is not int or desired.state != STATE_ALWAYS_ASK
                or len(desired.native_permission_id) <= BROWSER_ATTRIBUTE_INDEX):
            return False
        wanted = desired.native_permission_id[BROWSER_ATTRIBUTE_INDEX]
        if not isinstance(wanted, list):
            return False
        # The server has been observed to add precisely this two-element ask
        # marker. Nothing else, including order or unknown fields, is normalized.
        if any(isinstance(pair, list) and pair and pair[0] == ATTR_USER_CONSENT_STATE for pair in wanted):
            return False
        return cls._same_native(desired.native_permission_id, cls._native_without_derived_ask_marker(observed))

    @staticmethod
    def _is_browser_record(record: PermissionRecord) -> bool:
        return (record.tool_name, record.function_name) == _BROWSER_TOOL_FUNCTION

    @staticmethod
    def _browser_attributes(native_permission_id: list[Any]) -> tuple[tuple[str, str], ...]:
        raw = native_permission_id[BROWSER_ATTRIBUTE_INDEX] if len(native_permission_id) > BROWSER_ATTRIBUTE_INDEX else None
        if not isinstance(raw, list):
            return ()
        return tuple((pair[0], pair[1]) for pair in raw if isinstance(pair, list) and len(pair) >= 2
                     and isinstance(pair[0], str) and isinstance(pair[1], str))

    @staticmethod
    def _get_attr_value(attributes: tuple[tuple[str, str], ...], key: str) -> str | None:
        return next((value for name, value in attributes if name == key), None)

    @staticmethod
    def _has_duplicate_attr(attributes: tuple[tuple[str, str], ...], key: str) -> bool:
        return sum(name == key for name, _ in attributes) > 1

    @classmethod
    def _browser_mode(cls, attributes: tuple[tuple[str, str], ...]) -> str | None:
        value = cls._get_attr_value(attributes, ATTR_REMY_CHOICE_ID)
        if value is None:
            return None
        if value == CHOICE_REMOTE_ONLY:
            return "remote_only"
        return "automatic" if value.startswith(CHOICE_AUTOMATIC_PREFIX) else "unknown"

    @classmethod
    def _effective_permission(cls, record: PermissionRecord) -> str:
        if type(record.state) is not int or record.state not in _STATE_NAMES:
            return "unknown"
        attributes = cls._browser_attributes(record.native_permission_id)
        if record.state == STATE_ALWAYS_ALLOW and cls._get_attr_value(attributes, ATTR_USER_CONSENT_STATE) != "always_ask":
            return "always_allow"
        return "always_ask"

    @staticmethod
    def _truncate_label(label: str) -> str:
        return label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"

    @classmethod
    def _merge(cls, stored: _Records, catalog: _Records) -> _Records | None:
        """Stored overrides match exact catalog identities before a known ask alias."""
        layers: list[dict[str, PermissionRecord]] = []
        for records in (catalog, stored):
            layer: dict[str, PermissionRecord] = {}
            grants: set[str] = set()
            aliases: set[str] = set()
            for record in records:
                key = json.dumps(record.native_permission_id, default=str, sort_keys=True)
                alias = json.dumps(cls._native_without_derived_ask_marker(record), default=str, sort_keys=True)
                if key in layer or alias in aliases or (record.grant_id and record.grant_id in grants):
                    return None
                layer[key] = record
                aliases.add(alias)
                if record.grant_id:
                    grants.add(record.grant_id)
            layers.append(layer)
        defaults, overrides = layers
        for key, record in overrides.items():
            matched = key
            if matched not in defaults:
                matched = json.dumps(cls._native_without_derived_ask_marker(record), default=str, sort_keys=True)
            default = defaults.get(matched)
            if default is not None and (not cls._is_global(default) or not cls._is_global(record)):
                return None
            if default is not None:
                del defaults[matched]
        return tuple({**defaults, **overrides}.values())

    @staticmethod
    def _failure(operation: str, message: str, *, code: DomainErrorCode = DomainErrorCode.VERIFICATION_FAILED,
                 status: str = "read_error", mutation: bool = False, sent: bool | None = False) -> _Result:
        data = {"changed": False if sent is False else None, "mutation_sent": sent, "verified": False,
                **_SETTING_EVIDENCE} if mutation else None
        return DomainResult.failure(code, message, verification_status=status, data=data,
                                    details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})

    def _owner_failure(self, owner: str | None, operation: str, *, mutation: bool = False,
                       sent: bool | None = False) -> _Result | None:
        if self._account_matches(owner):
            return None
        return self._failure(operation, "The preference account changed or is unavailable.",
                             code=DomainErrorCode.AUTH_REQUIRED, status="account_mismatch",
                             mutation=mutation, sent=sent)

    async def _start(self, operation: str, *, mutation: bool = False) -> tuple[str, Any] | _Result:
        original = self._account_identity()
        failed = False
        auth_required = False
        try:
            await await_before_deadline(self._dependencies.client_initializer(), timeout=_CALLBACK_SECONDS)
        except ClientAuthenticationRequired:
            auth_required = True
        except Exception:
            failed = True
        owner = self._account_identity()
        if owner is None or (original is not None and owner != original):
            return self._failure(operation, "Gemini account initialization was not verified.",
                                 code=DomainErrorCode.AUTH_REQUIRED,
                                 status="account_required" if owner is None else "account_mismatch", mutation=mutation)
        if auth_required:
            return self._failure(operation, "Gemini authentication is required.",
                                 code=DomainErrorCode.AUTH_REQUIRED, status="authentication_required", mutation=mutation)
        if failed:
            return self._failure(operation, "The preference client could not be initialized.", mutation=mutation)
        try:
            client = self._dependencies.client_provider()
        except Exception:
            return self._failure(operation, "The preference client is unavailable.", mutation=mutation)
        failure = self._owner_failure(owner, operation, mutation=mutation)
        return failure if failure is not None else (owner, client)

    async def _read(self, owner: str, client: Any, source: str, operation: str, *, mutation: bool = False,
                    sent: bool | None = False) -> _Records | _Result:
        failure = self._owner_failure(owner, operation, mutation=mutation, sent=sent)
        if failure is not None:
            return failure
        records: Any = None
        failed = False
        try:
            request = (self._dependencies.read_permissions(client) if source == "stored"
                       else self._dependencies.read_catalog(client, "zh-CN"))
            records = await await_before_deadline(request, timeout=_CALLBACK_SECONDS)
        except Exception:
            failed = True
        failure = self._owner_failure(owner, operation, mutation=mutation, sent=sent)
        if failure is not None:
            return failure
        if failed or not isinstance(records, tuple) or any(
                not isinstance(record, PermissionRecord) or record.source != source for record in records):
            return self._failure(operation, "The preference metadata read was not verified.", mutation=mutation, sent=sent)
        return deepcopy(records)

    async def _snapshot(self, owner: str, client: Any, operation: str, *, mutation: bool = False) -> _Records | _Result:
        stored = await self._read(owner, client, "stored", operation, mutation=mutation)
        if isinstance(stored, DomainResult):
            return stored
        catalog = await self._read(owner, client, "catalog", operation, mutation=mutation)
        if isinstance(catalog, DomainResult):
            return catalog
        merged = self._merge(stored, catalog)
        if merged is None:
            return self._failure(operation, "Conflicting preference identities were observed.",
                                 status="verification_failed", mutation=mutation)
        return merged

    def _browser_record_failure(self, record: PermissionRecord, operation: str, *, mutation: bool = False) -> _Result | None:
        attributes = self._browser_attributes(record.native_permission_id)
        if not self._is_global(record) or any(self._has_duplicate_attr(attributes, key)
                                             for key in (ATTR_REMY_CHOICE_ID, ATTR_USER_CONSENT_STATE)):
            return self._failure(operation, "The browser preference identity is ambiguous or not global.",
                                 status="verification_failed", mutation=mutation)
        return None

    async def get_browser(self) -> _Result:
        operation = "get_browser"
        session = await self._start(operation)
        if isinstance(session, DomainResult):
            return session
        owner, client = session
        rows = await self._snapshot(owner, client, operation)
        if isinstance(rows, DomainResult):
            return rows
        browsers = [record for record in rows if self._is_browser_record(record)]
        if len(browsers) > 1:
            return self._failure(operation, "Multiple browser preference identities were observed.")
        if not browsers:
            return DomainResult.success({"browser": "automatic", "permission_preference": "always_ask",
                                         "effective_permission": "unknown", "observed": False, "revision": None,
                                         **_SETTING_EVIDENCE}, verification_status="unverified_default",
                                        details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})
        record = browsers[0]
        failure = self._browser_record_failure(record, operation)
        if failure is not None:
            return failure
        observed = record.source == "stored"
        return DomainResult.success({"browser": self._browser_mode(record.attributes) or "unknown",
                                     "permission_preference": self._effective_permission(record),
                                     "effective_permission": "unknown", "observed": observed,
                                     "revision": self._revision(owner, record), **_SETTING_EVIDENCE},
                                    verification_status="verified" if observed else "unverified_default",
                                    details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})

    async def list_permissions(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> _Result:
        operation = "list_permissions"
        if not self._valid_limit(limit) or not self._valid_offset(offset):
            return self._failure(operation, "Invalid preference page bounds.", code=DomainErrorCode.INVALID_ARGUMENT,
                                 status="invalid_request")
        session = await self._start(operation)
        if isinstance(session, DomainResult):
            return session
        owner, client = session
        rows = await self._snapshot(owner, client, operation)
        if isinstance(rows, DomainResult):
            return rows
        page = [{"permission_id": self._opaque_handle(owner, record.native_permission_id),
                 "tool": record.tool_name, "function": record.function_name,
                 "label": self._truncate_label(record.label), "category": record.category,
                 "state": (self._effective_permission(record) if self._is_browser_record(record)
                           and record.state in (1, 2) else self._state_name(record.state)),
                 "revision": self._revision(owner, record)}
                for record in rows[offset:offset + limit]]
        for item, record in zip(page, rows[offset:offset + limit]):
            item.update(permission_preference=item["state"], source=record.source, observed=record.source == "stored")
        end = offset + limit
        return DomainResult.success({"permissions": page, "next_offset": end if end < len(rows) else None,
                                     **_SETTING_EVIDENCE}, verification_status="verified",
                                    details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})

    @classmethod
    def _desired_preference(cls, record: PermissionRecord, browser: str | None, permission: str | None) -> PermissionRecord:
        native = deepcopy(record.native_permission_id)
        raw = native[9] if len(native) > 9 and native[9] is not None else []
        if not isinstance(raw, list) or any(not isinstance(pair, list) or len(pair) < 2
                                          or not all(isinstance(value, str) for value in pair[:2]) for pair in raw):
            raise ValueError("Invalid preference attributes.")
        if sum(pair[0] == ATTR_USER_CONSENT_STATE for pair in raw) > 1:
            raise ValueError("Ambiguous consent attributes.")
        choice = CHOICE_REMOTE_ONLY if browser == "remote_only" else CHOICE_AUTOMATIC_CANONICAL
        current = cls._get_attr_value(record.attributes, ATTR_REMY_CHOICE_ID)
        if browser == "automatic" and current is not None and current.startswith(CHOICE_AUTOMATIC_PREFIX):
            choice = current
        patched: list[Any] = []
        found = False
        for original in raw:
            pair = deepcopy(original)
            if pair[0] == ATTR_REMY_CHOICE_ID and browser is not None:
                pair[1], found = choice, True
            if pair[0] == ATTR_USER_CONSENT_STATE and pair[1] == "always_ask" and permission == "always_allow":
                if len(pair) != 2:
                    raise ValueError("Unknown browser consent metadata cannot be discarded.")
                continue
            patched.append(pair)
        if browser is not None and not found:
            patched.append([ATTR_REMY_CHOICE_ID, choice])
        if patched != raw:
            native.extend([None] * max(0, 10 - len(native)))
            native[9] = patched
        state = record.state if permission is None else (1 if permission == "always_allow" else 2)
        if type(state) is not int or state not in (1, 2):
            raise ValueError("An explicit known permission is needed for an unknown browser state.")
        return replace(record, native_permission_id=native, state=state)

    def _stale(self, operation: str) -> _Result:
        return self._failure(operation, "The preference changed before dispatch; read it again.",
                             code=DomainErrorCode.UPSTREAM_CHANGED, status="upstream_changed", mutation=True)

    async def _preflight(self, owner: str, client: Any, operation: str, target: PermissionRecord | None,
                         *, browser: bool = False) -> PermissionRecord | None | _Result:
        fresh = await self._snapshot(owner, client, operation, mutation=True)
        if isinstance(fresh, DomainResult):
            return fresh
        candidates = [record for record in fresh if (self._is_browser_record(record) if browser
                      else target is not None and self._same_native(record.native_permission_id, target.native_permission_id))]
        if target is None:
            return self._stale(operation) if candidates else None
        if len(candidates) != 1 or self._records_differ(target, candidates[0]):
            return self._stale(operation)
        return candidates[0]

    async def _mutate(self, owner: str, client: Any, operation: str, before: PermissionRecord | None,
                      desired: PermissionRecord, *, browser: bool = False) -> _Result:
        if (before is not None and before.source == "stored" and self._same_native(before.native_permission_id, desired.native_permission_id)
                and before.state == desired.state):
            return DomainResult.success({"changed": False, "mutation_sent": False, "verified": True,
                                         "revision": self._revision(owner, before),
                                         "permission_preference": (self._effective_permission(before) if browser
                                                                   else self._state_name(before.state)),
                                         **_SETTING_EVIDENCE}, verification_status="no_op",
                                        details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})
        sent: bool | None = False
        try:
            returned = await await_before_deadline(self._dependencies.write_permission(
                client, desired, state=desired.state, native_permission_id=deepcopy(desired.native_permission_id)),
                timeout=_CALLBACK_SECONDS)
            sent = True if returned is None else None
        except Exception as exc:
            evidence = getattr(exc, "mutation_sent", None)
            sent = evidence if type(evidence) is bool else None
        failure = self._owner_failure(owner, operation, mutation=True, sent=sent)
        if failure is not None:
            return failure
        if sent is False:
            return self._failure(operation, "The preference write was rejected before dispatch.", mutation=True, sent=False)
        rows = await self._read(owner, client, "stored", operation, mutation=True, sent=sent)
        if isinstance(rows, DomainResult):
            return rows
        candidates = [record for record in rows if (self._is_browser_record(record) if browser
                      else self._same_readback_identity(desired, record))]
        observed = candidates[0] if len(candidates) == 1 else None
        verified = (observed is not None and observed.source == "stored" and self._is_global(observed)
                    and observed.scope == 2
                    and self._same_readback_identity(desired, observed)
                    and observed.state == desired.state)
        if (verified and observed is not None and before is not None and before.grant_id
                and self._same_native(before.native_permission_id, desired.native_permission_id)):
            verified = (observed.grant_id == before.grant_id and
                        (not before.native_grant_identity
                         or self._same_native(observed.native_grant_identity, before.native_grant_identity)))
        if verified and browser and observed is not None:
            verified = (self._browser_record_failure(observed, operation) is None
                        and self._effective_permission(observed) == self._effective_permission(desired))
        if not verified or observed is None:
            return self._failure(operation, "The independent read-back did not verify the exact preference change.",
                                 mutation=True, sent=sent)
        return DomainResult.success({"changed": True, "mutation_sent": sent, "verified": True,
                                     "revision": self._revision(owner, observed),
                                     "permission_preference": (self._effective_permission(observed) if browser
                                                               else self._state_name(observed.state)),
                                     **_SETTING_EVIDENCE}, verification_status="verified",
                                    details={"service": "preferences", "operation": operation, **_SETTING_EVIDENCE})

    async def set_permission(self, permission_id: Any, permission: Any, expected_revision: Any = None) -> _Result:
        operation = "set_permission"
        if (not self._is_opaque64(permission_id) or not isinstance(permission, str) or permission not in _PERMISSIONS
                or (expected_revision is not None and not self._is_opaque64(expected_revision))):
            return self._failure(operation, "Invalid permission handle, choice, or revision.",
                                 code=DomainErrorCode.INVALID_ARGUMENT, status="invalid_request", mutation=True)
        session = await self._start(operation, mutation=True)
        if isinstance(session, DomainResult):
            return session
        owner, client = session
        rows = await self._snapshot(owner, client, operation, mutation=True)
        if isinstance(rows, DomainResult):
            return rows
        matches = [record for record in rows if self._opaque_handle(owner, record.native_permission_id) == permission_id]
        if len(matches) != 1 or not self._is_global(matches[0]) or not matches[0].tool_name or not matches[0].function_name:
            return self._failure(operation, "No unique global action matches the permission handle.",
                                 code=DomainErrorCode.INVALID_ARGUMENT, status="verification_failed", mutation=True)
        target = matches[0]
        if expected_revision is not None and self._revision(owner, target) != expected_revision:
            return self._stale(operation)
        is_browser = self._is_browser_record(target)
        if is_browser:
            failure = self._browser_record_failure(target, operation, mutation=True)
            if failure is not None:
                return failure
        try:
            desired = self._desired_preference(target, None, permission)
        except ValueError:
            return self._failure(operation, "The preference attributes are not safely editable.", mutation=True)
        preflight = await self._preflight(owner, client, operation, target, browser=is_browser)
        if isinstance(preflight, DomainResult):
            return preflight
        return await self._mutate(owner, client, operation, preflight, desired, browser=is_browser)

    async def set_browser(self, browser: Any = None, permission: Any = None, expected_revision: Any = None) -> _Result:
        operation = "set_browser"
        if ((browser is None and permission is None)
                or (browser is not None and (not isinstance(browser, str) or browser not in _BROWSER_MODES))
                or (permission is not None and (not isinstance(permission, str) or permission not in _PERMISSIONS))
                or (expected_revision is not None and not self._is_opaque64(expected_revision))):
            return self._failure(operation, "Invalid browser choice, permission, or revision.",
                                 code=DomainErrorCode.INVALID_ARGUMENT, status="invalid_request", mutation=True)
        session = await self._start(operation, mutation=True)
        if isinstance(session, DomainResult):
            return session
        owner, client = session
        rows = await self._snapshot(owner, client, operation, mutation=True)
        if isinstance(rows, DomainResult):
            return rows
        browsers = [record for record in rows if self._is_browser_record(record)]
        if len(browsers) > 1:
            return self._failure(operation, "Multiple browser preference identities were observed.", mutation=True)
        target = browsers[0] if browsers else None
        if expected_revision is not None and (target is None or self._revision(owner, target) != expected_revision):
            return self._stale(operation)
        if target is not None:
            failure = self._browser_record_failure(target, operation, mutation=True)
            if failure is not None:
                return failure
        base = target or PermissionRecord(
            native_permission_id=[["web_browser_agent", "actuate"]], grant_id="", scope=2, context=None,
            state=STATE_ALWAYS_ASK, label="Browser", category=None)
        try:
            desired = self._desired_preference(base, browser if target is not None else browser or "automatic", permission)
        except ValueError:
            return self._failure(operation, "The browser attributes or state are not safely editable.", mutation=True)
        preflight = await self._preflight(owner, client, operation, target, browser=True)
        if isinstance(preflight, DomainResult):
            return preflight
        return await self._mutate(owner, client, operation, preflight, desired, browser=True)


__all__ = ["PreferencesDependencies", "PreferencesService"]
