from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3

import pytest

from src.infrastructure import browser_action_store as module
from src.infrastructure.browser_action_store import BrowserActionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'current_account_key', lambda: 'account-a')
    return BrowserActionStore(tmp_path / 'private' / 'operations.sqlite3')


def claim(store, **overrides):
    args = {'account_key': 'account-a', 'chat_id': 'c_fixture', 'response_id': 'r_fixture',
            'action_id': 'action_' + 'a' * 25, 'action': 'click', 'expected_frame_sha256': 'a' * 64}
    args.update(overrides)
    return store.claim(**args)


def test_concurrent_claim_and_restart_are_at_most_once(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim(store), range(12)))
    assert sum(item['new'] for item in results) == 1
    reopened = BrowserActionStore(store.path)
    assert not claim(reopened)['new']
    assert reopened.read('account-a', 'action_' + 'a' * 25)['state'] == 'claimed'
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert os.stat(store.path.parent).st_mode & 0o777 == 0o700


def test_conflicting_identity_never_changes_record(store):
    original = claim(store)
    for field, value in [('chat_id', 'other'), ('response_id', 'other'), ('action', 'key'),
                         ('expected_frame_sha256', 'b' * 64)]:
        with pytest.raises(ValueError):
            claim(store, **{field: value})
    assert store.read('account-a', original['action_id'])['state'] == 'claimed'


def test_monotonic_transitions_and_evidence_upgrade(store):
    action_id = claim(store)['action_id']
    assert store.finish('account-a', action_id, state='dispatched')
    assert store.finish('account-a', action_id, state='outcome_unverified')
    assert not store.finish('account-a', action_id, state='dispatched')
    assert store.finish('account-a', action_id, state='verified', artifact_id='art_fixture')
    assert not store.finish('account-a', action_id, state='outcome_unverified')
    assert not store.finish('account-a', action_id, state='verified', artifact_id='other')
    assert store.read('account-a', action_id)['artifact_id'] == 'art_fixture'


def test_account_isolation_and_switch_during_write_rolls_back(store, monkeypatch):
    first = claim(store)
    monkeypatch.setattr(module, 'current_account_key', lambda: 'account-b')
    assert store.read('account-b', first['action_id']) is None
    other = claim(store, account_key='account-b')
    assert other['new']
    with pytest.raises(ValueError):
        store.finish('account-a', first['action_id'], state='verified')
    calls = iter(['account-b', 'account-b', 'account-b', 'account-a'])
    monkeypatch.setattr(module, 'current_account_key', lambda: next(calls, 'account-a'))
    with pytest.raises(ValueError):
        claim(store, account_key='account-b', action_id='action_' + 'b' * 25)
    monkeypatch.setattr(module, 'current_account_key', lambda: 'account-b')
    assert store.read('account-b', 'action_' + 'b' * 25) is None


@pytest.mark.parametrize('changes', [
    {'action_id': 'short'}, {'chat_id': ''}, {'response_id': ' '}, {'action': 'move'},
    {'action': []}, {'expected_frame_sha256': 'secret'}, {'account_key': 'other'},
])
def test_invalid_claim_fails_before_file_creation(store, changes):
    with pytest.raises(ValueError):
        claim(store, **changes)
    assert not store.path.exists() and not store.path.parent.exists()


def test_metadata_only_and_no_schema_version_change(store):
    original = claim(store)
    assert store.finish('account-a', original['action_id'], state='dispatched')
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        columns = {row[1] for row in db.execute('PRAGMA table_info(browser_actions)')}
    assert columns == {'account_key', 'action_id', 'chat_id', 'response_id', 'action',
                       'expected_frame_sha256', 'state', 'artifact_id', 'created_at', 'updated_at', 'candidate_id'}
    with pytest.raises(TypeError):
        claim(store, text='SECRET', x=100)
    assert b'SECRET' not in store.path.read_bytes()
