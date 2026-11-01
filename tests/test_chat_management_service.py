"""Deterministic offline tests for ChatManagementService: rename, pin, unpin.

Every transport and identity callback is a fake. No network, browser, account,
cookie, or upstream RPC is involved.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from src.domain.chat_management import (
    ChatMetadata,
    ChatMutationRequest,
    MAX_CHAT_TITLE_LENGTH,
    STATUS_ACCOUNT_MISMATCH,
    STATUS_ACCOUNT_REQUIRED,
    STATUS_EXPECTED_TITLE_CONFLICT,
    STATUS_INVALID_REQUEST,
    STATUS_NO_OP,
    STATUS_PRE_READ_CHAT_MISSING,
    STATUS_PRE_READ_ERROR,
    STATUS_PRE_READ_WRONG_CHAT,
    STATUS_READ_BACK_CHAT_MISSING,
    STATUS_READ_BACK_ERROR,
    STATUS_READ_BACK_FIELD_MISSING,
    STATUS_READ_BACK_MISMATCH,
    STATUS_READ_BACK_WRONG_CHAT,
    STATUS_VERIFIED,
    STATUS_VERIFIED_AFTER_MUTATION_EXCEPTION,
)
from src.services.chat_management import ChatManagementDependencies, ChatManagementService

CHAT_ID = "c_123abc"


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _metadata(chat_id: str = CHAT_ID, title: str | None = "Existing", is_pinned: bool | None = False) -> ChatMetadata:
    return ChatMetadata(chat_id=chat_id, title=title, is_pinned=is_pinned)


def _make_request(action: Any, chat_id: str = CHAT_ID, **kwargs: Any) -> ChatMutationRequest:
    return ChatMutationRequest(action=action, chat_id=chat_id, **kwargs)


class FakeTransport:
    def __init__(
        self,
        account: str | None = "acct",
        account_sequence: list[str | None] | None = None,
        metadata: dict[str, ChatMetadata] | None = None,
        read_values: list[ChatMetadata | None] | None = None,
        mutate_return: Any = None,
        mutate_side_effect: BaseException | None = None,
        initializer_side_effect: BaseException | None = None,
        read_side_effect: BaseException | None = None,
    ) -> None:
        self.account = account
        self._account_iterator = iter(account_sequence) if account_sequence is not None else None
        self._metadata = dict(metadata or {})
        self._read_values = read_values
        self.mutate_return = mutate_return
        self.mutate_side_effect = mutate_side_effect
        self.initializer_side_effect = initializer_side_effect
        self.read_side_effect = read_side_effect
        self.client = object()
        self.provider_calls = 0
        self.initializer_calls = 0
        self.account_key_calls = 0
        self.read_calls: list[tuple[Any, str]] = []
        self.mutate_calls: list[tuple[str, str, dict[str, Any]]] = []

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

    async def read_metadata(self, client: Any, chat_id: str) -> ChatMetadata | None:
        self.read_calls.append((client, chat_id))
        if self.read_side_effect is not None:
            raise self.read_side_effect
        if self._read_values is not None:
            return self._read_values.pop(0)
        return self._metadata.get(chat_id)

    async def mutate(self, client: Any, action: str, chat_id: str, changes: dict[str, Any]) -> Any:
        self.mutate_calls.append((action, chat_id, dict(changes)))
        if self.mutate_side_effect is not None:
            raise self.mutate_side_effect
        return self.mutate_return


def _service(transport: FakeTransport) -> ChatManagementService:
    return ChatManagementService(
        ChatManagementDependencies(
            client_provider=transport.client_provider,
            client_initializer=transport.client_initializer,
            read_metadata=transport.read_metadata,
            mutate=transport.mutate,
            account_key_provider=transport.account_key,
        )
    )


def test_rename_is_verified_after_exactly_one_mutation_and_read_back():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(title="Existing")},
        read_values=[_metadata(title="Existing"), _metadata(title="Renamed")],
    )
    result = _run(_service(transport).update(_make_request("rename", title="Renamed")))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.action == "rename"
    assert data.chat_id == CHAT_ID
    assert data.changed is True
    assert data.verified is True
    assert data.requested_title == "Renamed"
    assert data.observed_title == "Renamed"
    assert data.requested_pinned is None
    assert result.meta.verification_status == STATUS_VERIFIED
    assert transport.mutate_calls == [("rename", CHAT_ID, {"title": "Renamed"})]
    assert len(transport.read_calls) == 2
    assert all(call[0] is transport.client and call[1] == CHAT_ID for call in transport.read_calls)
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1
    assert "secret" not in str(result.to_dict())


def test_pin_is_verified_after_exactly_one_mutation_and_read_back():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=True)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.changed is True and data.verified is True
    assert data.requested_pinned is True
    assert data.observed_pinned is True
    assert result.meta.verification_status == STATUS_VERIFIED
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]
    assert len(transport.read_calls) == 2


def test_unpin_is_verified_after_exactly_one_mutation_and_read_back():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=True)},
        read_values=[_metadata(is_pinned=True), _metadata(is_pinned=False)],
    )
    result = _run(_service(transport).update(_make_request("unpin")))
    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.changed is True and data.verified is True
    assert data.requested_pinned is False
    assert data.observed_pinned is False
    assert result.meta.verification_status == STATUS_VERIFIED
    assert transport.mutate_calls == [("unpin", CHAT_ID, {"is_pinned": False})]
    assert len(transport.read_calls) == 2


def test_pre_read_missing_field_does_not_block_pin_when_read_back_proves_it():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=None)},
        read_values=[_metadata(is_pinned=None), _metadata(is_pinned=True)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]


def test_rename_no_op_when_pre_read_already_matches_sends_no_mutation():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(title="Renamed")})
    result = _run(_service(transport).update(_make_request("rename", title="Renamed")))
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert result.data.changed is False
    assert result.meta.verification_status == STATUS_NO_OP
    assert transport.mutate_calls == []
    assert len(transport.read_calls) == 1


def test_pin_no_op_when_already_pinned_sends_no_mutation():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(is_pinned=True)})
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert result.data.changed is False
    assert result.meta.verification_status == STATUS_NO_OP
    assert transport.mutate_calls == []


def test_unpin_no_op_when_already_unpinned_sends_no_mutation():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(is_pinned=False)})
    result = _run(_service(transport).update(_make_request("unpin")))
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert result.data.changed is False
    assert result.meta.verification_status == STATUS_NO_OP
    assert transport.mutate_calls == []


def test_expected_title_guard_that_matches_proceeds_to_mutation():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(title="Existing")},
        read_values=[_metadata(title="Existing"), _metadata(title="Renamed")],
    )
    result = _run(
        _service(transport).update(_make_request("rename", title="Renamed", expected_title="Existing"))
    )
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert transport.mutate_calls == [("rename", CHAT_ID, {"title": "Renamed"})]


@pytest.mark.parametrize(
    "invalid_request",
    [
        _make_request("delete"),
        _make_request("rename", chat_id="not-a-chat", title="New"),
        _make_request("rename", chat_id="c_", title="New"),
        _make_request("rename", chat_id=CHAT_ID, title=""),
        _make_request("rename", chat_id=CHAT_ID, title="   "),
        _make_request("rename", chat_id=CHAT_ID, title="x" * (MAX_CHAT_TITLE_LENGTH + 1)),
        _make_request("pin", chat_id=CHAT_ID, title="New"),
        _make_request("unpin", chat_id=CHAT_ID, title="x"),
        _make_request("rename", chat_id=CHAT_ID, title="New", expected_title=""),
        _make_request("rename", chat_id=CHAT_ID, title="New", expected_title="   "),
    ],
)
def test_invalid_requests_are_rejected_before_any_transport_call(invalid_request: ChatMutationRequest):
    transport = FakeTransport()
    result = _run(_service(transport).update(invalid_request))
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    assert result.meta.verification_status == STATUS_INVALID_REQUEST
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 0
    assert transport.read_calls == []
    assert transport.mutate_calls == []


def test_title_at_server_limit_is_not_rejected_as_invalid():
    transport = FakeTransport()
    result = _run(
        _service(transport).update(_make_request("rename", title="x" * MAX_CHAT_TITLE_LENGTH))
    )
    assert result.meta.verification_status != STATUS_INVALID_REQUEST


def test_missing_account_is_refused_before_initialization():
    transport = FakeTransport(account=None)
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_REQUIRED
    # Automatic authentication is still allowed to run: the initializer and
    # provider run, then the still-empty identity refuses the mutation.
    assert transport.provider_calls == 1
    assert transport.initializer_calls == 1
    assert transport.read_calls == []
    assert transport.mutate_calls == []


def test_account_key_provider_failure_is_refused():
    transport = FakeTransport(account="acct")

    def failing_identity() -> str | None:
        raise RuntimeError("identity down")

    transport.account_key = failing_identity
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_REQUIRED
    # The snapshot failed to an empty identity, so initialization is still
    # allowed to run, but no discovered identity appears and the mutation is refused.
    assert transport.provider_calls == 1
    assert transport.initializer_calls == 1


def test_account_switch_after_initialization_is_refused():
    transport = FakeTransport(account_sequence=["acct", "other"])
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.initializer_calls == 1
    assert transport.read_calls == []
    assert transport.mutate_calls == []


def test_account_switch_after_pre_read_is_refused():
    transport = FakeTransport(account_sequence=["acct", "acct", "other"])
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert len(transport.read_calls) == 1
    assert transport.mutate_calls == []


def test_account_switch_before_mutation_is_refused():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata()},
        account_sequence=["acct", "acct", "acct", "other"],
    )
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.mutate_calls == []


def test_account_switch_after_mutation_is_refused_without_read_back():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        account_sequence=["acct", "acct", "acct", "acct", "other", "other"],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]
    assert len(transport.read_calls) == 1  # pre-read only; post-mutation read-back not trusted


def test_account_switch_after_read_back_is_refused():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=True)],
        account_sequence=["acct"] * 5 + ["other"],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_ACCOUNT_MISMATCH
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]
    assert len(transport.read_calls) == 2


def test_expected_title_conflict_blocks_mutation():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(title="Existing")})
    result = _run(
        _service(transport).update(_make_request("rename", title="New", expected_title="Other"))
    )
    assert result.ok is False
    assert result.meta.verification_status == STATUS_EXPECTED_TITLE_CONFLICT
    assert transport.mutate_calls == []


def test_absent_pre_read_metadata_blocks_mutation():
    transport = FakeTransport(metadata={})
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_PRE_READ_CHAT_MISSING
    assert transport.mutate_calls == []


def test_wrong_chat_pre_read_metadata_blocks_mutation():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(chat_id="c_999", title="New")})
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_PRE_READ_WRONG_CHAT
    assert transport.mutate_calls == []


def test_pre_read_failure_is_unverified_failure():
    transport = FakeTransport(read_side_effect=RuntimeError("down"))
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_PRE_READ_ERROR
    assert transport.mutate_calls == []


def test_read_back_missing_is_pinned_is_unverified_and_not_coerced_to_false():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=None)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_FIELD_MISSING
    data = result.data
    assert data is not None and data.verified is False
    assert data.changed is None
    assert data.mutation_sent is True
    assert data.observed_pinned is None
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]


def test_read_back_missing_chat_is_unverified():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), None],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_CHAT_MISSING
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_read_back_wrong_chat_is_unverified():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(chat_id="c_999", is_pinned=True)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_WRONG_CHAT
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_read_back_conflicting_values_are_unverified():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=False)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_MISMATCH
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_read_back_failure_is_unverified():
    transport = FakeTransport(metadata={CHAT_ID: _metadata(is_pinned=False)})
    original_read = transport.read_metadata
    call_count = {"n": 0}

    async def read_once_then_fail(client: Any, chat_id: str) -> ChatMetadata | None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("gone")
        observed = await original_read(client, chat_id)
        return cast(ChatMetadata | None, observed)

    transport.read_metadata = read_once_then_fail
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_ERROR
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_mutation_response_lost_but_proving_read_back_is_verified_without_replay():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=True)],
        mutate_side_effect=RuntimeError("response lost"),
    )

    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is True
    assert result.data is not None and result.data.verified is True
    assert result.data.changed is True
    assert result.meta.verification_status == STATUS_VERIFIED_AFTER_MUTATION_EXCEPTION
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]


def test_mutation_response_lost_and_unproving_read_back_is_unverified_without_replay():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=False)],
        mutate_side_effect=RuntimeError("response lost"),
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]


def test_client_initializer_failure_blocks_before_mutation():
    transport = FakeTransport(initializer_side_effect=RuntimeError("down"))
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    # The initializer runs first and fails; the synchronous provider is never reached.
    assert transport.provider_calls == 0
    assert transport.initializer_calls == 1
    assert transport.read_calls == []
    assert transport.mutate_calls == []


def test_metadata_reads_pass_only_client_and_chat_id():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(title="Existing")},
        read_values=[_metadata(title="Existing"), _metadata(title="Renamed")],
    )
    result = _run(_service(transport).update(_make_request("rename", title="Renamed")))
    assert result.ok is True
    assert all(len(call) == 2 for call in transport.read_calls)
    assert all(call[1] == CHAT_ID for call in transport.read_calls)


def test_no_raw_upstream_response_leaks_into_public_result():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(title="Existing")},
        read_values=[_metadata(title="Existing"), _metadata(title="Renamed")],
        mutate_return={"raw": {"secret_payload": [1, 2, 3], "internal": True}},
    )
    result = _run(_service(transport).update(_make_request("rename", title="Renamed")))
    assert result.ok is True
    serialized = str(result.to_dict())
    assert "secret_payload" not in serialized
    assert "internal" not in serialized
    assert result.data is not None
    assert set(result.data.__dataclass_fields__) == {
        "action",
        "chat_id",
        "changed",
        "mutation_sent",
        "requested_title",
        "requested_pinned",
        "observed_title",
        "observed_pinned",
        "verified",
    }


def test_changed_is_none_when_mutation_sent_but_unverified():
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=False)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_initializer_binds_unbound_account_then_mutation_succeeds():
    # Regression: starts unbound, the initializer discovers/binds the synthetic
    # account, and the mutation proceeds with read-back verification.
    transport = FakeTransport(
        account=None,
        account_sequence=[None, "discovered-acct"] + ["discovered-acct"] * 4,
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=True)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is True
    data = result.data
    assert data is not None and data.verified is True
    assert data.changed is True
    assert data.mutation_sent is True
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1
    assert transport.mutate_calls == [("pin", CHAT_ID, {"is_pinned": True})]


def test_initializer_runs_but_absent_identity_blocks_read_and_mutation():
    # Lack of identity after initialization prevents read/mutate even though
    # initialization was allowed to run.
    transport = FakeTransport(account=None, account_sequence=[None, None])
    result = _run(_service(transport).update(_make_request("rename", title="New")))
    assert result.ok is False
    assert result.error_code == "AUTH_REQUIRED"
    assert result.meta.verification_status == STATUS_ACCOUNT_REQUIRED
    assert transport.initializer_calls == 1
    assert transport.provider_calls == 1
    assert transport.read_calls == []
    assert transport.mutate_calls == []


def test_integer_one_is_not_verified_as_pinned():
    # Strict boolean identity: integer 1 cannot be treated as a verified
    # boolean despite Python coercion/equality.
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=False)},
        read_values=[_metadata(is_pinned=False), _metadata(is_pinned=1)],
    )
    result = _run(_service(transport).update(_make_request("pin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_MISMATCH
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True


def test_string_false_is_not_verified_as_unpinned():
    # Strict boolean identity: the string "false" cannot be treated as a
    # verified boolean despite Python coercion/equality.
    transport = FakeTransport(
        metadata={CHAT_ID: _metadata(is_pinned=True)},
        read_values=[_metadata(is_pinned=True), _metadata(is_pinned="false")],
    )
    result = _run(_service(transport).update(_make_request("unpin")))
    assert result.ok is False
    assert result.meta.verification_status == STATUS_READ_BACK_MISMATCH
    assert result.data is not None and result.data.verified is False
    assert result.data.changed is None
    assert result.data.mutation_sent is True
