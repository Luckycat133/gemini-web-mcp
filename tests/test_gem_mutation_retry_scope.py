import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import APIError

from src.services.gems import create_gem, delete_gem, update_gem
from src.thinking_client import ThinkingLevelGeminiClient


@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_sdk_gem_mutation_does_not_retry_ambiguous_failure(action, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    client = ThinkingLevelGeminiClient(secure_1psid="synthetic")
    client._running = True
    client.account_status = AccountStatus.AVAILABLE
    client.client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=500)))
    post = client.client.post
    client.close = AsyncMock()
    if action == "create":
        operation = create_gem(client, name="synthetic", description="synthetic", instructions="synthetic")
    elif action == "update":
        operation = update_gem(client, gem_id="fixture", name="synthetic", description="synthetic", instructions="synthetic")
    else:
        operation = delete_gem(client, gem_id="fixture")
    with pytest.raises(APIError):
        asyncio.run(operation)
    assert post.await_count == 1


def test_scope_does_not_change_other_concurrent_sdk_requests(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-test-account")
    from gemini_webapi import GeminiClient
    from src.thinking_client import gem_mutation_scope
    observed = []
    async def parent(_client, *_args, **kwargs):
        await asyncio.sleep(0)
        observed.append(kwargs.get("current_retry"))
    monkeypatch.setattr(GeminiClient, "_batch_execute", parent)
    client = ThinkingLevelGeminiClient(secure_1psid="synthetic")
    async def scoped():
        with gem_mutation_scope():
            await client._batch_execute([])
    async def unscoped():
        await client._batch_execute([])
    async def run():
        await asyncio.gather(scoped(), unscoped())
    asyncio.run(run())
    assert sorted(observed, key=lambda value: value is None) == [0, None]
