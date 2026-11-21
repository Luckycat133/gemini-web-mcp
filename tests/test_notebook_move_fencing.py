import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.notebooks import move_chat_to_notebook


def test_move_stops_before_dispatch_if_account_changes_during_catalog():
    account = ["a"]
    async def fetch(_client, _locale):
        account[0] = "b"
        return ([{"id": "notebook", "title": "Synthetic"}], {})
    client = SimpleNamespace(_batch_execute=AsyncMock())
    with pytest.raises(PermissionError):
        asyncio.run(move_chat_to_notebook(client, chat_id="chat", notebook_id="notebook",
                                         fetch_notebooks=fetch, account_key_provider=lambda: account[0]))
    assert client._batch_execute.await_count == 0


def test_move_one_dispatch_no_sdk_retry_and_stops_on_account_change():
    account = ["a"]
    async def move(*_args, **_kwargs):
        account[0] = "b"
        return SimpleNamespace(status_code=200, text="synthetic")
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=move))
    fetch = AsyncMock(return_value=([{"id": "notebook", "title": "Synthetic"}], {}))
    verify = AsyncMock()
    with pytest.raises(PermissionError):
        asyncio.run(move_chat_to_notebook(client, chat_id="chat", notebook_id="notebook",
                                         fetch_notebooks=fetch, fetch_chats=verify,
                                         account_key_provider=lambda: account[0]))
    assert client._batch_execute.await_count == 1
    assert client._batch_execute.await_args.kwargs["current_retry"] == 0
    assert verify.await_count == 0
