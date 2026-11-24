"""Historical browser-input claims remain at most once across account formats."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from src.infrastructure import browser_action_store as module
from src.infrastructure.account_identity import current_account_keys
from src.infrastructure.browser_action_store import BrowserActionStore


@pytest.fixture
def owned(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-alpha")
    canonical, legacy = current_account_keys()
    store = BrowserActionStore(tmp_path / "operations.sqlite")
    args = {
        "account_key": canonical, "action_id": "action_" + "a" * 25,
        "chat_id": "c_fixture", "response_id": "r_fixture", "candidate_id": "rc_fixture",
        "action": "click", "expected_frame_sha256": "a" * 64,
    }
    return store, canonical, legacy, args


def seed_legacy(owned):
    store, canonical, legacy, args = owned
    store.claim(**args)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE browser_actions SET account_key=? WHERE account_key=?", (legacy, canonical))


def rows(store):
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM browser_actions ORDER BY account_key")]


def test_legacy_claim_is_reused_after_restart_and_never_dispatches_again(owned):
    seed_legacy(owned)
    store, canonical, legacy, args = owned
    before = rows(store)
    reopened = BrowserActionStore(store.path)
    assert reopened.read(canonical, args["action_id"])["account_key"] == legacy
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: reopened.claim(**{**args, "account_key": legacy if i % 2 else canonical}), range(12)))
    assert all(result["new"] is False and result["account_key"] == legacy for result in results)
    assert rows(store) == before


@pytest.mark.parametrize("field,value", [
    ("chat_id", "c_other"), ("response_id", "r_other"), ("candidate_id", "rc_other"),
    ("action", "key"), ("expected_frame_sha256", "b" * 64), ("candidate_id", None),
])
def test_legacy_claim_keeps_every_original_metadata_check(owned, field, value):
    seed_legacy(owned)
    store, _canonical, _legacy, args = owned
    before = rows(store)
    with pytest.raises(ValueError, match="different metadata"):
        store.claim(**{**args, field: value})
    assert rows(store) == before


def test_legacy_finish_updates_only_original_row_with_monotonic_transitions(owned):
    seed_legacy(owned)
    store, canonical, legacy, args = owned
    action_id = args["action_id"]
    assert store.finish(canonical, action_id, state="dispatched")
    assert store.finish(legacy, action_id, state="outcome_unverified")
    assert not store.finish(canonical, action_id, state="dispatched")
    assert store.finish(canonical, action_id, state="verified", artifact_id="art_fixture")
    assert not store.finish(canonical, action_id, state="verified", artifact_id="art_other")
    saved = rows(store)
    assert len(saved) == 1 and saved[0]["account_key"] == legacy
    assert saved[0]["artifact_id"] == "art_fixture" and saved[0]["state"] == "verified"


@pytest.mark.parametrize("method", ["claim", "read", "finish"])
def test_conflicting_same_action_alias_rows_fail_closed_without_modification(owned, method):
    seed_legacy(owned)
    store, canonical, _legacy, args = owned
    with sqlite3.connect(store.path) as db:
        db.execute("""INSERT INTO browser_actions SELECT ?,action_id,chat_id,response_id,action,
            expected_frame_sha256,state,artifact_id,created_at,updated_at,candidate_id
            FROM browser_actions""", (canonical,))
    before = rows(store)
    with pytest.raises(ValueError, match="conflicting account scopes"):
        if method == "claim":
            store.claim(**args)
        elif method == "read":
            store.read(canonical, args["action_id"])
        else:
            store.finish(canonical, args["action_id"], state="dispatched")
    assert rows(store) == before


def test_new_action_requested_with_current_legacy_alias_uses_canonical_row(owned):
    store, canonical, legacy, args = owned
    result = store.claim(**{**args, "account_key": legacy})
    assert result["new"] is True and result["account_key"] == canonical
    assert store.read(legacy, args["action_id"])["account_key"] == canonical


@pytest.mark.parametrize("untrusted", ["", "unknown", None])
def test_empty_and_unknown_scopes_cannot_create_or_read_claims(owned, untrusted):
    store, _canonical, _legacy, args = owned
    with pytest.raises(ValueError, match="identity"):
        store.claim(**{**args, "account_key": untrusted})
    assert not store.path.exists()


def test_account_switch_or_missing_psid_cannot_expand_legacy_scope(owned, monkeypatch):
    seed_legacy(owned)
    store, canonical, legacy, args = owned
    before = rows(store)
    monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-beta")
    other = current_account_keys()[0]
    assert store.read(other, args["action_id"]) is None
    assert not store.finish(other, args["action_id"], state="verified")
    for key in (canonical, legacy):
        with pytest.raises(ValueError, match="identity"):
            store.claim(**{**args, "account_key": key})
    monkeypatch.delenv("GEMINI_PSID")
    with pytest.raises(ValueError, match="identity"):
        store.read(legacy, args["action_id"])
    assert rows(store) == before


@pytest.mark.parametrize("method", ["claim", "read", "finish"])
def test_account_switch_during_alias_lookup_rolls_back_and_returns_no_claim(owned, monkeypatch, method):
    seed_legacy(owned)
    store, canonical, _legacy, args = owned
    before = rows(store)
    original = module._find_action

    def switch_after_lookup(*values):
        row = original(*values)
        monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-beta")
        return row

    monkeypatch.setattr(module, "_find_action", switch_after_lookup)
    with pytest.raises(ValueError, match="identity"):
        if method == "claim":
            store.claim(**args)
        elif method == "read":
            store.read(canonical, args["action_id"])
        else:
            store.finish(canonical, args["action_id"], state="dispatched")
    assert rows(store) == before
