"""Fresh native-history selection for a private Task browser turn; no RPC."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.task_browser import browser_identity
from src.services import web_context_transport as transport
from src.services.web_context_transport import WebContextUnavailable, fetch_task_browser_turn

CID, RID, RCID = "c_native_browser", "r_original", "rc_original"
VM = "projects/synthetic/regions/synthetic/microVms/synthetic"


def _candidate(rcid=RCID, opaque="synthetic-current-opaque"):
    metadata = [None] * 12
    metadata[4], metadata[5], metadata[11] = VM, "synthetic-context", opaque
    value = [None] * 13
    value[0], value[12] = rcid, [{"62": metadata}]
    return value


def _turn(rid=RID, cid=CID, candidates=None):
    return {"chat_id": cid, "response_id": rid,
            "candidates": [_candidate()] if candidates is None else candidates}


@pytest.fixture
def native(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-native-browser-account-a")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    value = SimpleNamespace(client=object(), task={"chat_id": CID, "upstream_status": 5},
                            items=[_turn()], events=[], switch_at=None)

    async def pause(stage):
        value.events.append(stage)
        await asyncio.sleep(0)
        if value.switch_at == stage:
            monkeypatch.setenv("GEMINI_PSID", "synthetic-native-browser-account-b")

    async def metadata(client, cid):
        await pause("task")
        return value.task

    async def history(client, key, **arguments):
        assert key == "tasks.progress_latest", "Browser observation must not use the single-turn VM path"
        await pause("history")
        return value.items

    value.metadata = AsyncMock(side_effect=metadata)
    value.history = AsyncMock(side_effect=history)
    monkeypatch.setattr(transport, "fetch_task_metadata", value.metadata)
    monkeypatch.setattr(transport, "_batch", value.history)
    return value


def test_exact_browser_identity_uses_a_bounded_native_window_and_never_selects_a_later_turn(native):
    selected = _candidate()
    native.items = [_turn("r_newer"), _turn(candidates=[_candidate("rc_other"), selected]), _turn("r_older")]

    result = asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))

    assert native.events == ["task", "history"]
    native.metadata.assert_awaited_once_with(native.client, CID)
    native.history.assert_awaited_once_with(native.client, "tasks.progress_latest", chat_id=CID, limit=100)
    assert (result["chat_id"], result["response_id"], result["candidate_id"]) == (CID, RID, RCID)
    assert result["candidate"] is selected and result["latest_verified"] is False
    assert browser_identity(result) == [VM, "synthetic-context", CID, RID, "synthetic-current-opaque"]


def test_latest_browser_observation_requires_one_native_turn_and_one_model_candidate(native):
    result = asyncio.run(fetch_task_browser_turn(native.client, CID))
    native.history.assert_awaited_once_with(native.client, "tasks.progress_latest", chat_id=CID, limit=1)
    assert result["response_id"] == RID and result["candidate_id"] == RCID
    assert result["latest_verified"] is True


def test_every_browser_read_refreshes_the_original_native_candidate_metadata(native):
    native.items = [_turn(candidates=[_candidate(opaque="synthetic-first-opaque")])]
    first = asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))
    native.items = [_turn(candidates=[_candidate(opaque="synthetic-second-opaque")])]
    second = asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))

    assert first["candidate_id"] == second["candidate_id"] == RCID
    assert browser_identity(first)[-1] == "synthetic-first-opaque"
    assert browser_identity(second)[-1] == "synthetic-second-opaque"
    assert native.metadata.await_count == native.history.await_count == 2
    assert all(call.args[1] == "tasks.progress_latest" for call in native.history.await_args_list)


@pytest.mark.parametrize("task", [None, {}, {"chat_id": "c_other", "upstream_status": 5}])
def test_missing_or_wrong_task_identity_stops_before_any_turn_read(native, task):
    native.task = task
    with pytest.raises(WebContextUnavailable):
        asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))
    native.metadata.assert_awaited_once_with(native.client, CID)
    native.history.assert_not_awaited()


def test_unknown_account_stops_before_task_and_turn_reads(native, monkeypatch):
    monkeypatch.delenv("GEMINI_PSID")
    with pytest.raises(WebContextUnavailable):
        asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))
    native.metadata.assert_not_awaited()
    native.history.assert_not_awaited()


@pytest.mark.parametrize("stage", ["task", "history"])
def test_account_change_at_each_read_discards_the_private_native_candidate(native, stage):
    native.switch_at = stage
    with pytest.raises(WebContextUnavailable) as caught:
        asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))
    assert VM not in str(caught.value) and "synthetic-current-opaque" not in str(caught.value)
    if stage == "task":
        native.history.assert_not_awaited()
    else:
        native.history.assert_awaited_once()


@pytest.mark.parametrize("items", [
    [],
    None,
    {"changed": True},
    [_turn("r_newer")],
    [_turn(cid="c_other")],
    [_turn(), _turn()],
    [_turn(candidates=[])],
    [_turn(candidates=[_candidate("rc_other")])],
    [_turn(candidates=[_candidate(), _candidate()])],
])
def test_exact_browser_read_has_no_fallback_for_absent_ambiguous_or_changed_identity(native, items):
    native.items = items
    with pytest.raises(WebContextUnavailable):
        asyncio.run(fetch_task_browser_turn(native.client, CID, RID, RCID))
    native.history.assert_awaited_once_with(native.client, "tasks.progress_latest", chat_id=CID, limit=100)


@pytest.mark.parametrize("items", [
    [],
    [_turn(cid="c_other")],
    [_turn(), _turn("r_other")],
    [_turn(cid="c_other"), _turn()],
    [_turn(candidates=[])],
    [_turn(candidates=[_candidate(), _candidate("rc_other")])],
])
def test_latest_browser_read_refuses_extra_turns_or_multiple_model_candidates(native, items):
    native.items = items
    with pytest.raises(WebContextUnavailable):
        asyncio.run(fetch_task_browser_turn(native.client, CID))
    native.history.assert_awaited_once_with(native.client, "tasks.progress_latest", chat_id=CID, limit=1)


def test_browser_adapter_routes_through_the_native_history_callback(native):
    from src.tools import task_browser

    service = task_browser._service()
    assert service.dependencies.read_turn is fetch_task_browser_turn
    result = asyncio.run(service.dependencies.read_turn(native.client, CID, RID, RCID))
    assert result["candidate_id"] == RCID
    native.history.assert_awaited_once_with(native.client, "tasks.progress_latest", chat_id=CID, limit=100)
