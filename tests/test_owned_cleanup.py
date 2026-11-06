import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import CleanupObservation, CleanupState
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
from src.services.history import observe_chat_absence
from src.services.owned_cleanup import owned_cleanup_result


@pytest.fixture(autouse=True)
def _bind_synthetic_account(monkeypatch):
    from src.services import history
    original = history.delete_chat_result
    async def delete(client, cid, *, expected_account, account_key_provider):
        assert expected_account == account_key_provider() == "account"
        return await original(client, cid, expected_account=expected_account, account_key_provider=account_key_provider)
    monkeypatch.setattr(history, "delete_chat_result", delete)
    monkeypatch.setattr("src.thinking_client.current_account_key", lambda: "account")


class Client:
    def __init__(self, *, present=(), bad_body=None, rejection=5):
        self.present = set(present)
        self.bad_body = bad_body
        self.rejection = rejection
        self.delete_calls = []
        self.read_calls = 0

    async def delete_chat(self, cid):
        self.delete_calls.append(cid)
        self.present.discard(cid)

    async def _batch_execute(self, calls, **kwargs):
        self.read_calls += 1
        call, = calls
        if call.rpcid == get_contract("history.page").rpc_id:
            # Project-only chats are missing from the ordinary history index.
            frame = ["wrb.fr", call.rpcid, json.dumps([None, None, []])]
        else:
            assert call.rpcid == get_contract("history.exact").rpc_id
            cid, limit, *_ = json.loads(call.payload)
            assert limit == 1
            if self.bad_body is not None or cid in self.present:
                body = self.bad_body if self.bad_body is not None else [[[[cid, "r_fixture"]]]]
                frame = ["wrb.fr", call.rpcid, json.dumps(body)]
            else:
                frame = ["wrb.fr", call.rpcid, None, None, None, [self.rejection]]
        return SimpleNamespace(status_code=200, text=json.dumps([frame]))


@pytest.mark.parametrize("body", [
    [None, None, [None]], [None, None, [{}]], [None, None, [[]]], [None, None, [[""]]],
    [None, None, [[42]]], [None, 1, []], [None, False, []], [None, ["cursor"], []],
    [None, {"cursor": "x"}, []],
])
def test_malformed_history_rows_or_cursors_are_rejected(body):
    assert parse_contract_body("history.page", body).status == "changed_shape"


def manager(tmp_path):
    return RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                    account_key_provider=lambda: "account", cleanup_guard=lambda cid: False)


def test_owned_cleanup_rejects_unknown_chat_before_client_initialization(tmp_path):
    instance = manager(tmp_path)
    initialize = AsyncMock()
    client = Client(present={"c_user"})
    result = asyncio.run(owned_cleanup_result("recheck", chat_id="c_user", manager=instance,
                                             initializer=initialize, client_provider=lambda: client))
    assert not result.ok and result.error.code.value == "INVALID_ARGUMENT"
    initialize.assert_not_awaited()
    assert client.delete_calls == []


def test_retry_honors_retention_and_leaves_unrelated_chats_alone(tmp_path):
    instance = manager(tmp_path)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)
    instance._store.schedule("account", "c_retained", 0, "mcp", retain=True)
    client = Client(present={"c_owned", "c_retained", "c_user"})
    result = asyncio.run(owned_cleanup_result("retry", manager=instance, initializer=AsyncMock(),
                                             client_provider=lambda: client))
    assert result.ok and result.data["verified_absent_count"] == 1
    assert client.delete_calls == ["c_owned"] and client.present == {"c_retained", "c_user"}


def test_recheck_repairs_a_false_completed_record_for_a_project_only_chat(tmp_path):
    instance = manager(tmp_path)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)
    token = instance._store.claim("account", "c_owned", force=True)
    instance._store.finish("account", "c_owned", token, verified=True, diagnostic_id=None)
    instance._completed_cleanup["c_owned"] = CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_owned")
    client = Client(present={"c_owned", "c_user"})
    result = asyncio.run(owned_cleanup_result("recheck", chat_id="c_owned", manager=instance,
                                             initializer=AsyncMock(), client_provider=lambda: client))
    assert result.ok and result.data["verified_absent_count"] == 1
    assert client.delete_calls == ["c_owned"] and client.present == {"c_user"}
    assert instance._store.get("account", "c_owned")["state"] == "completed"


def test_unverifiable_completed_record_is_never_reported_freshly_deleted(tmp_path):
    instance = manager(tmp_path)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)
    token = instance._store.claim("account", "c_owned", force=True)
    instance._store.finish("account", "c_owned", token, verified=True, diagnostic_id=None)
    client = Client(bad_body=[None, None, [None]])
    result = asyncio.run(owned_cleanup_result("recheck", chat_id="c_owned", manager=instance,
                                             initializer=AsyncMock(), client_provider=lambda: client))
    assert result.ok and result.data["verified_absent_count"] == 0 and result.data["unverified_count"] == 1
    assert result.meta.operation_state.value == "partial" and client.delete_calls == []


def test_empty_turns_stay_unverified():
    client = Client(bad_body=[[]])
    absent, details = asyncio.run(observe_chat_absence(client, "c_owned"))
    assert absent is None and details["reason"] == "empty_turns"
    assert client.read_calls == 1


def test_exact_read_rejects_a_turn_for_another_chat():
    with pytest.raises(RuntimeError, match="changed shape"):
        asyncio.run(observe_chat_absence(Client(bad_body=[[[["c_other", "r_other"]]]]), "c_owned"))


def test_permission_rejection_does_not_prove_absence():
    with pytest.raises(RuntimeError, match="usable RPC envelope"):
        asyncio.run(observe_chat_absence(Client(rejection=7), "c_owned"))


def test_exact_read_does_not_dispatch_under_another_account():
    client = Client()
    with pytest.raises(PermissionError):
        asyncio.run(observe_chat_absence(client, "c_owned", expected_account="a", account_key_provider=lambda: "b"))
    assert client.read_calls == 0


def test_retry_after_a_claim_without_dispatch_deletes_a_project_only_chat(tmp_path):
    instance = manager(tmp_path)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)
    token = instance._store.claim("account", "c_owned", force=True)
    instance._store.finish("account", "c_owned", token, verified=False, diagnostic_id=None)
    client = Client(present={"c_owned"})

    observation = asyncio.run(instance.delete_chat_result("c_owned", client=client))

    assert observation.state is CleanupState.COMPLETED
    assert client.delete_calls == ["c_owned"]
    assert client.read_calls == 2


def test_empty_retry_does_not_initialize_client(tmp_path):
    initialize = AsyncMock()
    result = asyncio.run(owned_cleanup_result("retry", manager=manager(tmp_path), initializer=initialize))
    assert result.ok and result.data["count"] == 0
    initialize.assert_not_awaited()


def test_account_switch_during_initialization_keeps_all_targets_unverified(tmp_path):
    account = ["account-a"]
    instance = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: account[0], cleanup_guard=lambda cid: False)
    instance._store.schedule("account-a", "c_owned", 0, "mcp", retain=False)
    client = Client(present={"c_owned"})

    async def initialize():
        account[0] = "account-b"

    result = asyncio.run(owned_cleanup_result("retry", manager=instance, initializer=initialize,
                                             client_provider=lambda: client))
    assert result.meta.operation_state.value == "partial"
    assert result.meta.verification_status == "account_changed_cleanup_unverified"
    assert result.data["count"] == result.data["verified_absent_count"] == 0
    assert result.data["target_count"] == result.data["unverified_count"] == result.data["unprocessed_count"] == 1
    assert client.read_calls == 0 and client.delete_calls == []
    assert instance._store.get("account-a", "c_owned")["state"] == "pending"


def test_recheck_honors_retention_changed_while_absence_read_is_in_flight(tmp_path):
    instance = manager(tmp_path)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)

    class RetainingClient(Client):
        async def _batch_execute(self, *args, **kwargs):
            instance.schedule_cleanup_result("c_owned", retain_chat=True)
            return await super()._batch_execute(*args, **kwargs)

    observation = asyncio.run(instance.recheck_cleanup_result("c_owned", client=RetainingClient()))
    assert observation.state is CleanupState.RETAINED
    assert instance._store.get("account", "c_owned")["state"] == "retained"
    assert "c_owned" not in instance._completed_cleanup


def test_recheck_never_completes_a_protected_operation_missing_from_history(tmp_path):
    instance = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: "account", cleanup_guard=lambda cid: True)
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)
    client = Client()
    observation = asyncio.run(instance.recheck_cleanup_result("c_owned", client=client))
    assert observation.state is CleanupState.PENDING
    assert instance._store.get("account", "c_owned")["state"] == "pending"
    assert client.read_calls == 0 and client.delete_calls == []


def test_recheck_revalidates_protection_after_the_absence_read(tmp_path):
    protected = [False]
    instance = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: "account", cleanup_guard=lambda cid: protected[0])
    instance._store.schedule("account", "c_owned", 0, "mcp", retain=False)

    class ProtectingClient(Client):
        async def _batch_execute(self, *args, **kwargs):
            protected[0] = True
            return await super()._batch_execute(*args, **kwargs)

    observation = asyncio.run(instance.recheck_cleanup_result("c_owned", client=ProtectingClient()))
    assert observation.state is CleanupState.PENDING
    assert instance._store.get("account", "c_owned")["state"] == "pending"


def test_late_cleanup_result_cannot_poison_the_new_accounts_cache(tmp_path, monkeypatch):
    from src.services import history
    from src.domain import DomainResult

    account = ["account-a"]
    instance = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: account[0], cleanup_guard=lambda cid: False)
    instance.schedule_cleanup_result("c_same", delete_after_seconds=0, dispatch=False)

    async def delete(*args, **kwargs):
        account[0] = "account-b"
        instance._account()
        return DomainResult.success({}, verification_status="verified_absent")

    monkeypatch.setattr(history, "delete_chat_result", delete)
    observation = asyncio.run(instance.delete_chat_result("c_same", client=object()))
    assert observation.state is CleanupState.FAILED
    assert not instance._completed_cleanup and not instance._cleanup_observations
    assert instance._store.get("account-b", "c_same") is None
    assert instance._store.get("account-a", "c_same")["state"] == "pending"


def test_late_frame_registration_stays_in_the_original_accounts_queue(tmp_path):
    account = ["account-a"]
    instance = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: account[0], cleanup_guard=lambda cid: False)
    instance._account()
    account[0] = "account-b"
    observation = instance.schedule_cleanup_result("c_owned", delete_after_seconds=0, _owner_account="account-a")
    assert observation.state is CleanupState.PENDING
    assert instance._store.get("account-a", "c_owned")["state"] == "pending"
    assert instance._store.get("account-b", "c_owned") is None
    assert not instance._pending_cleanup and not instance._inflight_cleanup
