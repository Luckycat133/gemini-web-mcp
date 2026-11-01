"""Focused behavior tests for the NotebookLM service.

These tests exercise the Notebook source picker through injected doubles: validation before auth,
account fences, unknowns, malformed data, and errors.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from src.domain import DomainErrorCode
from src.domain.notebooklm import (
    NotebookLMListData,
    NotebookLMNotebook,
    NotebookLMSelectionData,
)
from src.services.notebooklm import NotebookLMService, NotebookLMServiceDependencies


def test_auto_auth_uses_the_client_created_during_initialization():
    state = {"owner": None, "client": None}
    initialized_client = object()
    async def initialize():
        state.update(owner="owner", client=initialized_client)
    async def fetch(client):
        assert client is initialized_client
        return _BOOKS
    service = NotebookLMService(NotebookLMServiceDependencies(
        client_initializer=initialize, client_provider=lambda: state["client"],
        fetch_notebooks=fetch, account_key_provider=lambda: state["owner"]))
    assert asyncio.run(service.list_notebooks()).ok


def test_duplicate_fetched_notebook_ids_are_not_silently_collapsed():
    service, _ = _make_service(fetched=(_BOOKS[0], _BOOKS[0]))
    result = asyncio.run(service.resolve_notebooks([_BOOKS[0].notebook_id]))
    assert not result.ok and result.error_code == DomainErrorCode.UPSTREAM_CHANGED.value


def _book(
    notebook_id: str,
    title: str,
    *,
    source_count: int | None = 3,
    created_at: str | None = "2024-01-01T00:00:00Z",
) -> NotebookLMNotebook:
    return NotebookLMNotebook(
        notebook_id=notebook_id, title=title, source_count=source_count, created_at=created_at
    )


_BOOKS = (
    _book("n_alpha", "Alpha Notes"),
    _book("n_beta", "Beta notes"),
    _book("n_gamma", "Gamma"),
)


class _State:
    def __init__(self, account: str | None = "owner-1") -> None:
        self.account = account
        self.init_called = False
        self.fetch_called = False


def _make_service(
    *,
    fetched: tuple[NotebookLMNotebook, ...] = _BOOKS,
    account: str | None = "owner-1",
    fetch_error: Exception | None = None,
    flip_after_fetch: bool = False,
    flip_in_initializer: bool = False,
    raise_on_init: Exception | None = None,
) -> tuple[NotebookLMService, _State]:
    state = _State(account)

    async def client_initializer() -> None:
        state.init_called = True
        if flip_in_initializer:
            state.account = "other-owner"
        if raise_on_init is not None:
            raise raise_on_init

    def client_provider() -> object:
        return object()

    async def fetch_notebooks(client: object) -> tuple[NotebookLMNotebook, ...]:
        state.fetch_called = True
        if flip_after_fetch:
            state.account = "other-owner"
        if fetch_error is not None:
            raise fetch_error
        return fetched

    def account_key_provider() -> str | None:
        return state.account

    service = NotebookLMService(
        NotebookLMServiceDependencies(
            client_initializer=client_initializer,
            client_provider=client_provider,
            fetch_notebooks=fetch_notebooks,
            account_key_provider=account_key_provider,
        )
    )
    return service, state


async def _result(coro: Any) -> Any:
    return await coro


def run(coro: Any) -> Any:
    return asyncio.run(_result(coro))


# ---------------------------------------------------------------------------
# domain shape
# ---------------------------------------------------------------------------


def test_notebook_notebook_is_frozen() -> None:
    book = _book("n1", "Title")
    with pytest.raises(FrozenInstanceError):
        book.title = "Changed"  # type: ignore[misc]
    assert _book("n1", "Title") == _book("n1", "Title")
    assert _book("n1", "Title", source_count=None, created_at=None).source_count is None


def test_list_data_defaults_source() -> None:
    data = NotebookLMListData()
    assert data.source == "notebooklm"
    assert data.items == ()
    assert data.count == 0 and data.total_count == 0
    assert data.has_more is False and data.next_offset is None
    assert data.query == ""


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


def test_list_returns_typed_compact_page() -> None:
    service, state = _make_service()
    result = run(service.list_notebooks())

    assert result.ok is True
    assert result.error is None
    assert isinstance(result.data, NotebookLMListData)
    assert result.data.source == "notebooklm"
    assert result.data.query == ""
    assert result.data.count == 3
    assert result.data.total_count == 3
    assert result.data.has_more is False
    assert result.data.next_offset is None
    assert [item.notebook_id for item in result.data.items] == ["n_alpha", "n_beta", "n_gamma"]
    assert state.init_called is True
    assert state.fetch_called is True
    for item in result.data.items:
        assert isinstance(item, NotebookLMNotebook)
        assert item.source_count is not None
        assert item.created_at is not None


def test_list_query_matches_title_casefold_insensitive_substring() -> None:
    service, _state = _make_service()

    by_alpha = run(service.list_notebooks(query="alpha"))
    assert by_alpha.ok is True
    assert [item.notebook_id for item in by_alpha.data.items] == ["n_alpha"]

    by_notes = run(service.list_notebooks(query="NOTES"))
    assert [item.notebook_id for item in by_notes.data.items] == ["n_alpha", "n_beta"]

    by_zeta = run(service.list_notebooks(query="zeta"))
    assert by_zeta.ok is True
    assert by_zeta.data.count == 0
    assert by_zeta.data.total_count == 0
    assert by_zeta.data.items == ()
    assert by_zeta.data.has_more is False
    assert by_zeta.data.next_offset is None


def test_list_pages_next_offset_and_bounds() -> None:
    service, _state = _make_service()

    first = run(service.list_notebooks(limit=2, offset=0))
    assert first.ok is True
    assert first.data.count == 2
    assert first.data.total_count == 3
    assert first.data.has_more is True
    assert first.data.next_offset == 2
    assert [item.notebook_id for item in first.data.items] == ["n_alpha", "n_beta"]

    second = run(service.list_notebooks(limit=2, offset=2))
    assert second.data.count == 1
    assert second.data.next_offset is None
    assert second.data.has_more is False
    assert [item.notebook_id for item in second.data.items] == ["n_gamma"]

    past_end = run(service.list_notebooks(limit=2, offset=3))
    assert past_end.data.count == 0
    assert past_end.data.total_count == 3
    assert past_end.data.has_more is False
    assert past_end.data.next_offset is None

    max_offset = run(service.list_notebooks(offset=10000))
    assert max_offset.ok is True
    assert max_offset.data.count == 0


def test_list_empty_when_no_notebooks() -> None:
    service, state = _make_service(fetched=())
    result = run(service.list_notebooks())
    assert result.ok is True
    assert result.data.count == 0
    assert result.data.total_count == 0
    assert result.data.items == ()
    assert state.fetch_called is True


def test_list_unknown_counts_and_times_are_preserved() -> None:
    fetched = (_book("n1", "Alpha", source_count=None, created_at=None),)
    service, _state = _make_service(fetched=fetched)
    result = run(service.list_notebooks())
    assert result.ok is True
    item = result.data.items[0]
    assert item.source_count is None
    assert item.created_at is None


# ---------------------------------------------------------------------------
# validation before init / auth
# ---------------------------------------------------------------------------


def _assert_invalid_before_init(result: Any, label: str) -> None:
    assert result.ok is False, label
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value, label
    assert result.error_code != DomainErrorCode.AUTH_REQUIRED.value, label


@pytest.mark.parametrize("limit", [0, 101, True, "20", 3.5, None])
def test_limit_rejected_before_init(limit: Any) -> None:
    service, state = _make_service(account=None)
    result = run(service.list_notebooks(limit=limit))
    _assert_invalid_before_init(result, f"limit={limit!r}")
    assert state.init_called is False
    assert state.fetch_called is False


@pytest.mark.parametrize("offset", [-1, 10001, True, "0", None])
def test_offset_rejected_before_init(offset: Any) -> None:
    service, state = _make_service(account=None)
    result = run(service.list_notebooks(offset=offset))
    _assert_invalid_before_init(result, f"offset={offset!r}")
    assert state.init_called is False
    assert state.fetch_called is False


@pytest.mark.parametrize("query", [123, None, b"query"])
def test_query_rejected_before_init(query: Any) -> None:
    service, state = _make_service(account=None)
    result = run(service.list_notebooks(query=query))
    _assert_invalid_before_init(result, f"query={query!r}")
    assert state.init_called is False
    assert state.fetch_called is False


def test_defaults_are_valid() -> None:
    service, _state = _make_service()
    result = run(service.list_notebooks())
    assert result.ok is True


# ---------------------------------------------------------------------------
# auto-auth binding during init
# ---------------------------------------------------------------------------


def test_auto_auth_binds_owner_during_init() -> None:
    """Pre-init owner is None; a nonempty post-init owner binds the account."""
    state = _State(account=None)

    async def client_initializer() -> None:
        state.init_called = True
        state.account = "bound-owner"

    def client_provider() -> object:
        return object()

    async def fetch_notebooks(client: object) -> tuple[NotebookLMNotebook, ...]:
        state.fetch_called = True
        return _BOOKS

    def account_key_provider() -> str | None:
        return state.account

    service = NotebookLMService(
        NotebookLMServiceDependencies(
            client_initializer=client_initializer,
            client_provider=client_provider,
            fetch_notebooks=fetch_notebooks,
            account_key_provider=account_key_provider,
        )
    )
    result = run(service.list_notebooks())
    assert result.ok is True
    assert state.init_called is True
    assert state.fetch_called is True


def test_pre_init_owner_mismatch_refuses() -> None:
    service, state = _make_service(flip_in_initializer=True)
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert state.init_called is True
    assert state.fetch_called is False


def test_no_owner_after_init_refuses() -> None:
    state = _State(account="owner-1")

    async def client_initializer() -> None:
        state.init_called = True
        state.account = None

    def client_provider() -> object:
        return object()

    async def fetch_notebooks(client: object) -> tuple[NotebookLMNotebook, ...]:
        state.fetch_called = True
        return _BOOKS

    def account_key_provider() -> str | None:
        return state.account

    service = NotebookLMService(
        NotebookLMServiceDependencies(
            client_initializer=client_initializer,
            client_provider=client_provider,
            fetch_notebooks=fetch_notebooks,
            account_key_provider=account_key_provider,
        )
    )
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert state.fetch_called is False


# ---------------------------------------------------------------------------
# account fences around the fetch
# ---------------------------------------------------------------------------


def test_account_switch_after_fetch_refuses() -> None:
    service, state = _make_service(flip_after_fetch=True)
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value
    assert state.fetch_called is True


def test_fetch_failure_is_classified_not_raw() -> None:
    service, state = _make_service(fetch_error=ConnectionError("upstream gone"))
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == "NETWORK_ERROR"
    assert "upstream gone" not in str(result.error.message) if result.error else True
    assert result.error is not None and result.error.retryable is True
    assert state.init_called is True


def test_initializer_failure_is_classified_not_raw() -> None:
    service, _state = _make_service(raise_on_init=RuntimeError("boom"))
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == "INTERNAL_ERROR"
    assert result.error is not None


# ---------------------------------------------------------------------------
# malformed fetched data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fetched",
    [
        "not-a-list",
        (_book("n1", "Alpha"), object()),  # type: ignore[list-item]
        (_book("", "Alpha"),),  # type: ignore[arg-type]
        (_book("n1", None),),  # type: ignore[arg-type]
        (_book("n1", "Alpha", source_count=-1),),
        (_book("n1", "Alpha", source_count=True),),  # type: ignore[arg-type]
        (_book("n1", "Alpha", created_at=123),),  # type: ignore[arg-type]
    ],
)
def test_malformed_fetched_data_refused(fetched: Any) -> None:
    service, state = _make_service(fetched=fetched)  # type: ignore[arg-type]
    result = run(service.list_notebooks())
    assert result.ok is False
    assert result.error_code == DomainErrorCode.UPSTREAM_CHANGED.value
    assert state.fetch_called is True


def test_list_is_a_fresh_read_every_call() -> None:
    service, state = _make_service()
    run(service.list_notebooks())
    run(service.list_notebooks())
    assert state.fetch_called is True
    # fetch_notebooks is invoked once per call by counting via a fresh service
    counter = {"calls": 0}

    async def counting_fetch(client: object) -> tuple[NotebookLMNotebook, ...]:
        counter["calls"] += 1
        return _BOOKS

    state2 = _State()

    async def init2() -> None:
        state2.init_called = True

    service2 = NotebookLMService(
        NotebookLMServiceDependencies(
            client_initializer=init2,
            client_provider=lambda: object(),
            fetch_notebooks=counting_fetch,
            account_key_provider=lambda: "owner-1",
        )
    )
    run(service2.list_notebooks())
    run(service2.list_notebooks(query="alpha", limit=1, offset=0))
    assert counter["calls"] == 2


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_exact_matches_in_request_order() -> None:
    fetched = (_book("n_c", "Gamma"), _book("n_a", "Alpha"), _book("n_b", "Beta notes"))
    service, state = _make_service(fetched=fetched)
    result = run(service.resolve_notebooks(["n_a", "n_c"]))

    assert result.ok is True
    assert isinstance(result.data, NotebookLMSelectionData)
    assert result.data.count == 2
    assert [item.notebook_id for item in result.data.items] == ["n_a", "n_c"]
    assert result.data.items[0].title == "Alpha"
    assert result.data.items[1].title == "Gamma"
    assert state.fetch_called is True


def test_resolve_single_id() -> None:
    service, _state = _make_service()
    result = run(service.resolve_notebooks(["n_beta"]))
    assert result.ok is True
    assert result.data.count == 1
    assert result.data.items[0].notebook_id == "n_beta"


def test_resolve_unknown_id_refused() -> None:
    service, state = _make_service()
    result = run(service.resolve_notebooks(["n_a", "n_missing"]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.CAPABILITY_UNAVAILABLE.value
    assert state.fetch_called is True


def test_resolve_all_unknown_refused() -> None:
    service, _state = _make_service()
    result = run(service.resolve_notebooks(["nope"]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.CAPABILITY_UNAVAILABLE.value


def test_resolve_duplicate_refused_before_init() -> None:
    service, state = _make_service()
    result = run(service.resolve_notebooks(["n_alpha", "n_alpha"]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


def test_resolve_empty_container_refused() -> None:
    service, state = _make_service()
    for notebook_ids in ([], ()):
        result = run(service.resolve_notebooks(notebook_ids))
        assert result.ok is False
        assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


def test_resolve_count_bound_refused() -> None:
    service, state = _make_service()
    too_many = [f"id-{i}" for i in range(11)]
    result = run(service.resolve_notebooks(too_many))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


def test_resolve_container_must_be_list_or_tuple() -> None:
    service, state = _make_service()
    for notebook_ids in ("n_alpha", {"n_alpha"}, 7, None):
        result = run(service.resolve_notebooks(notebook_ids))
        assert result.ok is False
        assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


@pytest.mark.parametrize("bad_id", ["", "   ", 7, "x" * 257])
def test_resolve_invalid_ids_refused_before_init(bad_id: Any) -> None:
    service, state = _make_service()
    result = run(service.resolve_notebooks([bad_id]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


def test_resolve_validation_before_auth() -> None:
    service, state = _make_service(account=None)
    result = run(service.resolve_notebooks(["n_alpha", ""]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.INVALID_ARGUMENT.value
    assert state.init_called is False
    assert state.fetch_called is False


def test_resolve_account_switch_after_fetch_refuses() -> None:
    service, _state = _make_service(flip_after_fetch=True)
    result = run(service.resolve_notebooks(["n_alpha"]))
    assert result.ok is False
    assert result.error_code == DomainErrorCode.AUTH_REQUIRED.value


def test_resolve_selection_typed() -> None:
    service, _state = _make_service()
    result = run(service.resolve_notebooks(["n_alpha"]))
    assert result.ok is True
    assert isinstance(result.data, NotebookLMSelectionData)
    assert isinstance(result.data.items, tuple)
    assert all(isinstance(item, NotebookLMNotebook) for item in result.data.items)
    assert result.data.count == len(result.data.items)
