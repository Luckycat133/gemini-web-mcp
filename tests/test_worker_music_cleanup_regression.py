"""Regression tests for music/video worker retention, media identity recovery, and cookie reload."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src import client_manager, operation_worker
from src.services import media_jobs


def test_music_artifact_not_observed_is_not_cleaned_up_after_three_polls(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("GEMINI_PSID", "synthetic-psid")
    store = OperationStore(tmp_path / "ops.sqlite3")
    handle = "op_" + "1" * 32
    store.create_or_get(handle, "gemini_generate_music", media_type="music", output_dir=str(tmp_path))
    assert store.claim_worker(handle, "worker-1")
    assert store.checkpoint_media(handle, worker_token="worker-1", chat_id="c_" + "a" * 16)
    store.retire_worker(handle, "worker-1")

    polls = 0
    cleanup_calls = []

    async def fake_recover(record, *, store, worker_token):
        nonlocal polls
        polls += 1
        if polls < 5:
            store.finish_worker(
                record.operation_id,
                worker_token=worker_token,
                state="partial",
                phase="media_artifact_not_observed",
                verification_status="not_observed",
            )
        else:
            store.release_media_cleanup(record.operation_id, worker_token=worker_token)
            store.update_cleanup(record.operation_id, "completed")
            store.finish_worker(
                record.operation_id,
                worker_token=worker_token,
                state="completed",
                phase="completed",
                verification_status="local_artifact_verified",
            )

    async def fake_finalize(chat_id, **kwargs):
        cleanup_calls.append((chat_id, kwargs))
        return SimpleNamespace(state=SimpleNamespace(value="completed"))

    monkeypatch.setattr(media_jobs, "recover_media_job", fake_recover)
    monkeypatch.setattr("src.client_wrapper.finalize_remote_chat_cleanup", fake_finalize)
    monkeypatch.setattr("src.services.media_cleanup.reconcile_owned_media_cleanup", lambda *_a, **_k: False)

    assert store.claim_worker(handle, "worker-2")
    asyncio.run(
        operation_worker.run_worker(
            {"kind": "media_resume", "operation_id": handle, "worker_token": "worker-2", "request": {}},
            store=store,
            observation_seconds=30.0,
            poll_seconds=0.0,
        )
    )
    saved = store.get(handle)
    assert polls == 5
    assert saved is not None and saved.state == "completed"
    assert cleanup_calls == []


def test_media_resume_partial_does_not_immediately_delete_remote_chat(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("GEMINI_PSID", "synthetic-psid")
    store = OperationStore(tmp_path / "ops.sqlite3")
    handle = "op_" + "2" * 32
    store.create_or_get(handle, "gemini_generate_music", media_type="music", output_dir=str(tmp_path))
    assert store.claim_worker(handle, "worker-1")
    assert store.checkpoint_media(handle, worker_token="worker-1", chat_id="c_" + "b" * 16)
    store.retire_worker(handle, "worker-1")

    cleanup_calls = []

    async def fake_recover(record, *, store, worker_token):
        store.finish_worker(
            record.operation_id,
            worker_token=worker_token,
            state="partial",
            phase="media_artifact_not_observed",
            verification_status="not_observed",
        )

    async def fake_finalize(chat_id, **kwargs):
        cleanup_calls.append((chat_id, kwargs))
        return SimpleNamespace(state=SimpleNamespace(value="completed"))

    monkeypatch.setattr(media_jobs, "recover_media_job", fake_recover)
    monkeypatch.setattr("src.client_wrapper.finalize_remote_chat_cleanup", fake_finalize)
    monkeypatch.setattr("src.services.media_cleanup.reconcile_owned_media_cleanup", lambda *_a, **_k: False)

    # Trigger 2 polls (< 12 limit for music) and exit via observation_seconds timeout.
    ticks = iter([0.0, 0.0, 0.0, 0.0, 999.0])
    monkeypatch.setattr(operation_worker.time, "monotonic", lambda: next(ticks, 999.0))

    assert store.claim_worker(handle, "worker-2")
    asyncio.run(
        operation_worker.run_worker(
            {"kind": "media_resume", "operation_id": handle, "worker_token": "worker-2", "request": {}},
            store=store,
            observation_seconds=10.0,
            poll_seconds=0.0,
        )
    )
    saved = store.get(handle)
    assert saved is not None and saved.state == "partial" and saved.cleanup_protected
    assert cleanup_calls == []


def test_recover_media_job_recovers_missing_rcid_from_single_turn_read_chat(monkeypatch, tmp_path):
    cid = "c_" + "c" * 16
    record = SimpleNamespace(
        operation_id="op_" + "3" * 32,
        operation="gemini_generate_image",
        state="partial",
        phase="media_outcome_unverified",
        upstream_chat_id=cid,
        upstream_response_id="r_1",
        upstream_candidate_id=None,
        media_type="image",
        output_dir=str(tmp_path),
        filename="recovered",
        retain_chat=True,
        delete_after_seconds=None,
        requested_model="flash",
        request_model="gemini-3-flash",
        effective_backend="Gemini Web image mode",
        account_key="acct_1",
        account_binding_pending=False,
    )
    candidate = SimpleNamespace(rcid="rc_1", text="", generated_images=[object()], generated_videos=[], generated_media=[])
    turn = SimpleNamespace(
        role="model",
        model_output=SimpleNamespace(metadata=[cid, "r_1", "rc_1"], candidates=[candidate]),
    )
    client = SimpleNamespace(read_chat=AsyncMock(return_value=SimpleNamespace(turns=[turn])))
    saved_calls = []

    async def fake_save(cli, response, rec, *, store, token, readback_timeout=60.0):
        saved_calls.append((response.metadata, response.rcid))
        store.finish_worker(rec.operation_id, worker_token=token, state="completed", phase="completed")

    store = SimpleNamespace(
        is_worker_owner=lambda *_a: True,
        checkpoint_media=lambda *_a, **_k: True,
        get=lambda *_a: SimpleNamespace(**{**record.__dict__, "upstream_candidate_id": "rc_1"}),
        finish_worker=lambda *_a, **_k: True,
    )
    monkeypatch.setattr(media_jobs, "operation_account_matches", lambda *_a: True)
    monkeypatch.setattr(media_jobs, "reconcile_owned_media_cleanup", lambda *_a: False)
    monkeypatch.setattr(media_jobs, "_save_response", fake_save)
    monkeypatch.setattr("src.client_wrapper.get_gemini_client", lambda: client)
    monkeypatch.setattr("src.client_wrapper.initialize_client", AsyncMock())

    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="w1"))
    assert saved_calls == [([cid, "r_1", "rc_1"], "rc_1")]


def test_prepare_browser_cookie_cache_clears_explicit_cookie_dir(monkeypatch, tmp_path):
    stale = tmp_path / ".cached_cookies_test.json"
    stale.write_text('{"__Secure-1PSID": "stale"}', encoding="utf-8")
    keep = tmp_path / "other.json"
    keep.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(tmp_path))

    client_manager.prepare_browser_cookie_cache(force=True)
    assert not stale.exists()
    assert keep.exists()


def test_client_manager_hot_reloads_browser_cookies_once_when_session_unauthenticated(monkeypatch, tmp_path):
    from gemini_webapi.constants import AccountStatus

    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(tmp_path))
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "true")
    manager = client_manager.ClientManager()

    clients = []
    init_calls = 0

    class FakeClient:
        def __init__(self, psid, psidts="", **_k):
            self.cookies = {"__Secure-1PSID": psid, "__Secure-1PSIDTS": psidts}
            self.running = True
            self.account_status = None

        async def init(self, **_k):
            nonlocal init_calls
            init_calls += 1
            self.running = True
            self.account_status = None

        async def close(self):
            self.running = False

    class FakeCookieMgr:
        def __init__(self):
            self.calls = 0
            self._data = SimpleNamespace(source="browser_chrome:Default", extra_cookies={})

        def get_cookie(self):
            return self._data

        def discover_browser_cookies(self, **_k):
            self.calls += 1
            return SimpleNamespace(
                cookies={"__Secure-1PSID": f"psid_{self.calls}", "__Secure-1PSIDTS": "ts"},
                source="browser_chrome:Default",
                reason=None,
            )

        def update_cookie(self, psid, psidts="", *, source="", extra_cookies=None, **_k):
            self._data = SimpleNamespace(source=source, extra_cookies=extra_cookies or {})
            return True

    fake_mgr = FakeCookieMgr()
    import src.thinking_client as tc
    monkeypatch.setattr(tc, "ThinkingLevelGeminiClient", lambda p, t="", **kw: clients.append(FakeClient(p, t)) or clients[-1])
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(client_manager, "get_cookie_manager", lambda: fake_mgr)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    monkeypatch.delenv("GEMINI_PSIDTS", raising=False)

    first = asyncio.run(manager.initialize())
    assert first is clients[0] and first.cookies["__Secure-1PSID"] == "psid_1"
    # Simulate session becoming unauthenticated later.
    first.account_status = AccountStatus.UNAUTHENTICATED
    second = asyncio.run(manager.initialize())
    assert second is clients[1] and second.cookies["__Secure-1PSID"] == "psid_2"
    assert init_calls == 2
