"""Same-credential historical scopes preserve ownership without rewriting rows."""

import asyncio
import hashlib
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.constants import OMNI_VIDEO_API_BACKEND
from src.domain import CleanupObservation, CleanupState, DomainResult
from src.infrastructure.account_identity import (
    account_key_scope,
    account_matches,
    canonical_account_key,
    current_account_key,
    current_account_keys,
    operation_account_matches,
)
from src.infrastructure.cleanup_store import CleanupStore
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-alpha")
    return current_account_keys()


def seed(store, account, cid="c_owned", state="pending", *, token=None):
    with store._connect() as db:
        db.execute("""INSERT INTO cleanup(account_key,chat_id,state,delete_at,source,lease_token,lease_until)
            VALUES(?,?,?,0,'test',?,?)""", (account, cid, state, token, time.time() + 600 if token else None))


def raw_rows(store):
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM cleanup ORDER BY account_key,chat_id")]


def test_alias_derivation_matches_the_previous_installation_exactly(owner):
    canonical, legacy = owner
    assert canonical == current_account_key() == hashlib.sha256(b"synthetic-account-alpha").hexdigest()
    assert legacy == hashlib.pbkdf2_hmac("sha256", b"synthetic-account-alpha", b"gemini-psid", 10_000).hex()
    assert canonical != legacy
    assert account_key_scope(canonical) == account_key_scope(legacy) == owner
    assert canonical_account_key(legacy) == canonical_account_key(canonical) == canonical
    assert account_matches(legacy, canonical) and account_matches(canonical, legacy)
    assert operation_account_matches(SimpleNamespace(account_key=legacy, effective_backend=None))


@pytest.mark.parametrize("unbound", [None, "", "unknown"])
def test_empty_unknown_and_foreign_aliases_are_not_current_ownership(monkeypatch, owner, unbound):
    assert canonical_account_key(unbound) is None
    assert not account_matches(unbound, owner[0])
    assert account_key_scope(unbound) == (() if not unbound else (unbound,))
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-beta")
    assert all(canonical_account_key(key) is None for key in owner)
    assert all(not account_matches(key, current_account_key()) for key in owner)
    assert not operation_account_matches(SimpleNamespace(account_key=owner[1], effective_backend=None))


def test_no_credential_does_not_expand_or_authenticate_an_old_key(monkeypatch, owner):
    monkeypatch.delenv("GEMINI_PSID")
    assert current_account_keys() == () and current_account_key() is None
    assert canonical_account_key(owner[1]) is None
    assert account_key_scope(owner[1]) == (owner[1],)
    assert not operation_account_matches(SimpleNamespace(account_key=owner[1], effective_backend=None))


def test_api_backend_never_accepts_a_cookie_account_alias(monkeypatch, owner):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-api-key")
    assert not operation_account_matches(SimpleNamespace(account_key=owner[1], effective_backend=OMNI_VIDEO_API_BACKEND))
    api_owner = hashlib.sha256(b"gemini-video-api\0synthetic-api-key").hexdigest()
    assert operation_account_matches(SimpleNamespace(account_key=api_owner, effective_backend=OMNI_VIDEO_API_BACKEND))
    monkeypatch.delenv("GEMINI_API_KEY")
    assert not operation_account_matches(SimpleNamespace(account_key=api_owner, effective_backend=OMNI_VIDEO_API_BACKEND))


def test_cleanup_reads_both_same_account_scopes_without_rewriting_or_foreign_listing(tmp_path, owner):
    store = CleanupStore(tmp_path / "cleanup.sqlite")
    seed(store, owner[1])
    seed(store, owner[0], "c_new")
    seed(store, "foreign", "c_foreign")
    before = raw_rows(store)
    assert {row["chat_id"] for row in store.all_records(owner[0])} == {"c_owned", "c_new"}
    assert {row["chat_id"] for row in store.pending(owner[1])} == {"c_owned", "c_new"}
    assert store.get(owner[0], "c_owned")["account_key"] == owner[1]
    assert store.get(owner[0], "c_foreign") is None
    assert store.all_records("") == []
    assert raw_rows(store) == before


def test_cleanup_new_records_use_sha_but_existing_legacy_row_and_lease_stay_exact(tmp_path, owner):
    store = CleanupStore(tmp_path / "cleanup.sqlite")
    seed(store, owner[1])
    store.schedule(owner[0], "c_owned", 0, "new-worker", retain=False)
    token = store.claim(owner[0], "c_owned")
    assert token
    assert store.claim(owner[1], "c_owned") is None
    assert store.renew(owner[0], "c_owned", token)
    assert not store.renew(owner[0], "c_owned", "wrong-token")
    store.finish(owner[0], "c_owned", "wrong-token", verified=True, diagnostic_id=None)
    assert store.get(owner[0], "c_owned")["state"] == "pending"
    store.finish(owner[0], "c_owned", token, verified=True, diagnostic_id=None)
    assert store.get(owner[0], "c_owned")["state"] == "completed"
    store.schedule(owner[1], "c_new", 0, "new-worker", retain=False)
    assert {(row["chat_id"], row["account_key"]) for row in raw_rows(store)} == {
        ("c_owned", owner[1]), ("c_new", owner[0]),
    }


def test_foreign_account_cannot_resolve_claim_or_finish_legacy_ownership(tmp_path, owner, monkeypatch):
    store = CleanupStore(tmp_path / "cleanup.sqlite")
    seed(store, owner[1], token="old-worker-token")
    before = raw_rows(store)
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-beta")
    current = current_account_key()
    assert store.all_records(current) == []
    assert store.get(current, "c_owned") is None
    assert store.claim(current, "c_owned", force=True) is None
    assert not store.renew(current, "c_owned", "old-worker-token")
    store.finish(current, "c_owned", "old-worker-token", verified=True, diagnostic_id=None)
    assert raw_rows(store) == before


def test_existing_legacy_retention_and_active_lease_cannot_be_bypassed(tmp_path, owner):
    store = CleanupStore(tmp_path / "cleanup.sqlite")
    seed(store, owner[1], state="retained")
    seed(store, owner[1], "c_leased", token="old-worker-token")
    before = raw_rows(store)
    assert store.claim(owner[0], "c_owned", force=True) is None
    assert store.claim(owner[0], "c_leased", force=True) is None
    assert not store.record_verified_absence(owner[0], "c_owned")
    assert not store.record_verified_absence(owner[0], "c_leased")
    store.schedule(owner[0], "c_leased", 0, "new", retain=True)
    assert raw_rows(store) == before


@pytest.mark.parametrize("legacy_state,new_state,leased", [
    ("retained", "pending", False), ("pending", "retained", False),
    ("pending", "pending", False), ("completed", "pending", False),
    ("pending", "pending", True),
])
def test_conflicting_alias_rows_never_overwrite_duplicate_or_claim(tmp_path, owner, legacy_state, new_state, leased):
    store = CleanupStore(tmp_path / "cleanup.sqlite")
    seed(store, owner[1], state=legacy_state, token="old-token" if leased else None)
    seed(store, owner[0], state=new_state)
    before = raw_rows(store)
    assert len(store.all_records(owner[0])) == 1
    if "retained" in {legacy_state, new_state}:
        assert store.get(owner[0], "c_owned")["state"] == "retained"
        assert store.pending(owner[0]) == []
    assert store.claim(owner[0], "c_owned", force=True) is None
    store.schedule(owner[0], "c_owned", 0, "new", retain=False, override_retained=True)
    store.expedite_pending(owner[0], "c_owned")
    store.record_failure(owner[0], "c_owned", "diag")
    store.reopen_verified_present(owner[0], "c_owned")
    store.defer_protected(owner[0], "c_owned")
    assert not store.record_explicit_verified_absence(owner[0], "c_owned")
    assert raw_rows(store) == before


def test_protected_legacy_cleanup_remains_pending_without_network_or_duplicate(tmp_path, owner, monkeypatch):
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", cleanup_guard=lambda _cid: True, account_key_provider=current_account_key,
    )
    seed(manager._store, owner[1])
    delete = AsyncMock()
    monkeypatch.setattr("src.services.history.delete_chat_result", delete)

    result = asyncio.run(manager.delete_chat_result("c_owned", client=object(), _expected_account=owner[1]))

    assert result.state is CleanupState.PENDING
    delete.assert_not_awaited()
    rows = raw_rows(manager._store)
    assert len(rows) == 1 and rows[0]["account_key"] == owner[1] and rows[0]["attempts"] == 0


def test_legacy_cleanup_executes_with_current_network_fence_and_finishes_original_row(tmp_path, owner, monkeypatch):
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", cleanup_guard=lambda _cid: False, account_key_provider=current_account_key,
    )
    seed(manager._store, owner[1])

    async def delete(_client, _cid, *, expected_account, account_key_provider):
        assert expected_account == account_key_provider() == owner[0]
        return DomainResult.success({}, verification_status="verified_absent")

    send = AsyncMock(side_effect=delete)
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    result = asyncio.run(manager.delete_chat_result("c_owned", client=object(), _expected_account=owner[1]))
    assert result.state is CleanupState.COMPLETED
    send.assert_awaited_once()
    rows = raw_rows(manager._store)
    assert len(rows) == 1 and rows[0]["account_key"] == owner[1] and rows[0]["state"] == "completed"


def test_legacy_cleanup_fails_when_account_changes_during_network_readback(tmp_path, owner, monkeypatch):
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", cleanup_guard=lambda _cid: False, account_key_provider=current_account_key,
    )
    seed(manager._store, owner[1])

    async def delete(_client, _cid, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-account-beta")
        return DomainResult.success({}, verification_status="verified_absent")

    monkeypatch.setattr("src.services.history.delete_chat_result", AsyncMock(side_effect=delete))
    result = asyncio.run(manager.delete_chat_result("c_owned", client=object(), _expected_account=owner[1]))
    assert result.state is CleanupState.FAILED
    assert raw_rows(manager._store)[0]["state"] == "pending"


def test_foreign_or_empty_expected_binding_never_dispatches_legacy_cleanup(tmp_path, owner, monkeypatch):
    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", cleanup_guard=lambda _cid: False, account_key_provider=current_account_key,
    )
    seed(manager._store, owner[1])
    before = raw_rows(manager._store)
    send = AsyncMock()
    monkeypatch.setattr("src.services.history.delete_chat_result", send)
    for expected in ("", "unknown"):
        result = asyncio.run(manager.delete_chat_result("c_owned", client=object(), _expected_account=expected))
        assert result.state is CleanupState.FAILED
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-beta")
    result = asyncio.run(manager.delete_chat_result("c_owned", client=object(), _expected_account=owner[1]))
    assert result.state is CleanupState.FAILED
    send.assert_not_awaited()
    assert raw_rows(manager._store) == before


def test_cleanup_facade_accepts_only_current_credential_legacy_binding(tmp_path, owner, monkeypatch):
    import src.client_wrapper as wrapper

    manager = RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite", cleanup_guard=lambda _cid: False, account_key_provider=current_account_key,
    )
    seed(manager._store, owner[1])
    monkeypatch.setattr(wrapper, "_cleanup_manager", manager)
    complete = CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_owned")
    delete = AsyncMock(return_value=complete)
    monkeypatch.setattr(wrapper, "delete_remote_chat_result", delete)
    result = asyncio.run(wrapper.finalize_remote_chat_cleanup(
        "c_owned", delete_after_seconds=0, client=object(), expected_account=owner[1],
    ))
    assert result.state is CleanupState.COMPLETED
    delete.assert_awaited_once()
