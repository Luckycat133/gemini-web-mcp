import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.scheduled import create_daily_action, delete_action


def test_unverified_scheduled_mutations_are_not_reported_successful_or_replayed(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic")))
    registry = AsyncMock(return_value=([], {}))
    by_id = AsyncMock(return_value=(None, {}))
    create = asyncio.run(create_daily_action(
        client, title="Synthetic", instructions="Synthetic", hour=9,
        timezone_name="UTC", locale="en", fetch_registry=registry, fetch_by_id=by_id,
        extract_bodies=lambda *_: [["synthetic"]],
        parse_create=lambda _: {"id": "fixture"},
    ))
    assert not create["ok"] and create["id"] == "fixture"
    assert client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs["current_retry"] == 0
    client._batch_execute.reset_mock()
    deleted = asyncio.run(delete_action(client, action_id="fixture", fetch_registry=registry, fetch_by_id=by_id,
                                        extract_bodies=lambda *_: [["synthetic"]]))
    assert not deleted["ok"] and deleted["deleted_by_id_after_delete"] is False
    assert client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs["current_retry"] == 0


def test_scheduled_mutation_fences_account_after_write_before_readback():
    account = ["a"]
    async def write(*_args, **_kwargs):
        account[0] = "b"
        return SimpleNamespace(status_code=200, text="synthetic")
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=write))
    read = AsyncMock()
    async def run():
        import pytest
        with pytest.raises(PermissionError):
            await delete_action(client, action_id="fixture", fetch_registry=read,
                                account_key_provider=lambda: account[0])
    asyncio.run(run())
    assert client._batch_execute.await_count == 1 and read.await_count == 0


def test_scheduled_create_fences_account_after_readback_before_next_read():
    account = ["a"]
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic")))
    async def registry(*_args):
        account[0] = "b"
        return ([{"id": "fixture"}], {})
    read = AsyncMock()
    async def run():
        import pytest
        with pytest.raises(PermissionError):
            await create_daily_action(client, title="Synthetic", instructions="Synthetic", hour=9,
                                      timezone_name="UTC", locale="en", fetch_registry=registry, fetch_by_id=read,
                                      extract_bodies=lambda *_: [["synthetic"]],
                                      parse_create=lambda _: {"id": "fixture"},
                                      account_key_provider=lambda: account[0])
    asyncio.run(run())
    assert client._batch_execute.await_count == 1 and read.await_count == 0


def test_scheduled_reads_reject_missing_rejected_and_changed_envelopes(monkeypatch):
    from src.infrastructure.rpc_parsers import RPCEnvelope
    from src.services import scheduled
    import pytest

    monkeypatch.setattr(scheduled, "execute_contract", AsyncMock(return_value=SimpleNamespace(status_code=200, text="synthetic")))
    for envelope in (RPCEnvelope(True, 1, ()), RPCEnvelope(True, 1, (), reject_code=403),
                     RPCEnvelope(True, 1, ({"unexpected": True},))):
        monkeypatch.setattr(scheduled, "parse_rpc_envelope", lambda *_args: envelope)
        with pytest.raises(ValueError):
            asyncio.run(scheduled.fetch_scheduled_registry(object(), 100))
        with pytest.raises(ValueError):
            asyncio.run(scheduled.fetch_scheduled_task_by_id(object(), "fixture", 100))
