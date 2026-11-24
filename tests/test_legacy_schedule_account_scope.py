"""A prior install's same-credential schedule guards remain protective."""
from contextlib import contextmanager

import pytest

from src.infrastructure.account_identity import current_account_keys
from src.infrastructure.task_schedule_store import TaskScheduleGuardStore


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-schedule-legacy-owner')
    current, legacy = current_account_keys()
    store = TaskScheduleGuardStore(tmp_path / 'operations.sqlite3')
    with store._connect() as db:
        db.executemany('INSERT INTO task_schedule_guards VALUES(?,?,?,?)', [
            (legacy, 'schedule-old', 'c_old', 1),
            (current, 'schedule-new', 'c_new', 2),
            ('other-account', 'schedule-old', 'c_foreign', 3),
        ])
    return store, current, legacy


def test_old_schedule_guard_is_read_without_rewriting_its_binding(guarded):
    store, current, legacy = guarded
    assert store.protects('c_old', current)
    assert store.protects('c_new', legacy)
    assert not store.protects('c_foreign', current)
    assert store.chats_for_schedule(current, 'schedule-old') == ('c_old',)
    with store._connect() as db:
        assert db.execute('SELECT account_key FROM task_schedule_guards WHERE chat_id=?', ('c_old',)).fetchone() == (legacy,)


def test_verified_absence_removes_both_same_owner_guard_formats_only(guarded):
    store, current, legacy = guarded
    with store._connect() as db:
        db.execute('INSERT INTO task_schedule_guards VALUES(?,?,?,?)', (current, 'schedule-old', 'c_old', 4))
    store.forget_verified_absent(legacy, 'schedule-old')
    assert not store.protects('c_old', current)
    assert store.protects('c_new', legacy)
    assert store.protects('c_foreign', 'other-account')


def test_fresh_chat_references_replace_stale_alias_rows_and_keep_other_chats(guarded):
    store, current, legacy = guarded
    store.replace_chat(legacy, 'c_moved', ['schedule-old'])
    assert store.chats_for_schedule(current, 'schedule-old') == ('c_moved',)
    assert not store.protects('c_old', current)
    assert store.protects('c_new', current)
    assert store.protects('c_foreign', 'other-account')
    with store._connect() as db:
        assert db.execute('SELECT account_key FROM task_schedule_guards WHERE chat_id=?', ('c_moved',)).fetchone() == (current,)


def test_fresh_complete_account_references_replace_only_same_credential_rows(guarded):
    store, current, legacy = guarded
    store.replace_account(legacy, [('schedule-fresh', 'c_fresh')])
    assert store.protects('c_fresh', current)
    assert not store.protects('c_old', current)
    assert not store.protects('c_new', legacy)
    assert store.protects('c_foreign', 'other-account')


@pytest.mark.parametrize('supplied', ['', 'other-account'])
def test_unknown_scope_cannot_remove_current_or_legacy_guards(guarded, supplied):
    store, current, _ = guarded
    for mutate in (lambda: store.replace_chat(supplied, 'c_old', []),
                   lambda: store.replace_account(supplied, []),
                   lambda: store.forget_verified_absent(supplied, 'schedule-old')):
        with pytest.raises(ValueError):
            mutate()
    assert store.protects('c_old', current)


def test_account_switch_before_transaction_does_not_erase_old_guard(guarded, monkeypatch):
    store, current, _ = guarded
    connect = store._connect

    @contextmanager
    def switched():
        monkeypatch.setenv('GEMINI_PSID', 'different-synthetic-schedule-owner')
        with connect() as db:
            yield db

    monkeypatch.setattr(store, '_connect', switched)
    with pytest.raises(ValueError):
        store.replace_account(current, [])
    with connect() as db:
        assert db.execute('SELECT COUNT(*) FROM task_schedule_guards').fetchone()[0] == 3
