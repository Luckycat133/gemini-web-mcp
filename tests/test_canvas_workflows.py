"""Native output/read-back and exact-version export govern Canvas completion."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.services.canvas as canvas
from src.domain import DomainErrorCode, DomainResult, OperationState
from src.thinking_client import ThinkingLevelGeminiClient


def item(text="Original", artifact_id="artifact_fixture"):
    return {"artifact_id": artifact_id, "text": text, "kind": "document", "title": "Fixture",
            "latest_version_id": "version_fixture", "turn_id": "r_fixture", "response_id": "rc_fixture"}


def client_with_output(monkeypatch, *, failure=False):
    client = ThinkingLevelGeminiClient("fixture", "fixture")
    captured = []

    async def generate(**kwargs):
        captured.append(kwargs)
        kwargs["chat"].cid = "c_canvas_fixture"
        if failure:
            kwargs["chat"].metadata = [""]
            raise TimeoutError("private provider fixture")
        return SimpleNamespace(metadata=["c_canvas_fixture", "r_fixture"])

    monkeypatch.setattr(ThinkingLevelGeminiClient, "generate_content", lambda self, **kw: generate(**kw))
    return client, captured


def test_canvas_creation_requires_real_native_artifact_readback(monkeypatch):
    client, captured = client_with_output(monkeypatch)
    read = AsyncMock(return_value=DomainResult.success({"items": [item()]}))
    monkeypatch.setattr(canvas, "list_canvas_chat_artifacts_result", read)
    result = asyncio.run(canvas.generate_canvas_result(client, prompt="Fixture"))
    assert result.ok and result.meta.verification_status == "native_canvas_observed"
    assert result.data["retained"] and result.data["source_chat_id"] == "c_canvas_fixture"
    assert captured[0]["tool_mode"] == "canvas"
    read.assert_awaited_once_with(client, "c_canvas_fixture")


@pytest.mark.parametrize("readback", [None, [], [item("")], [{**item(), "kind": None}]])
def test_prose_or_failed_readback_cannot_complete_canvas_creation(monkeypatch, readback):
    client, _ = client_with_output(monkeypatch)
    read = (AsyncMock(side_effect=TimeoutError("private read")) if readback is None else
            AsyncMock(return_value=DomainResult.success({"items": readback})))
    monkeypatch.setattr(canvas, "list_canvas_chat_artifacts_result", read)
    result = asyncio.run(canvas.generate_canvas_result(client, prompt="Fixture"))
    assert result.ok and result.meta.operation_state is OperationState.ACCEPTED
    assert result.data["chat_id"] == "c_canvas_fixture" and result.data["items"] == []


def test_canvas_timeout_preserves_id_after_sdk_metadata_rollback(monkeypatch):
    client, captured = client_with_output(monkeypatch, failure=True)
    result = asyncio.run(canvas.generate_canvas_result(client, prompt="Fixture"))
    assert not result.ok and result.error.code is DomainErrorCode.TIMED_OUT
    assert result.error.retryable is False
    assert result.data["source_chat_id"] == "c_canvas_fixture" and result.data["retained"]
    assert len(captured) == 1


def test_canvas_export_verifies_utf8_and_preserves_existing_file(monkeypatch, tmp_path):
    read = AsyncMock(return_value=DomainResult.success(item("测试\nExact version")))
    monkeypatch.setattr(canvas, "get_canvas_artifact_result", read)
    path = tmp_path / "canvas.md"
    result = asyncio.run(canvas.export_canvas_result(None, artifact_id="artifact_fixture", output_path=str(path)))
    assert result.ok and path.read_text() == "测试\nExact version"
    assert result.data["artifacts"][0].local_path == str(path)
    again = asyncio.run(canvas.export_canvas_result(None, artifact_id="artifact_fixture", output_path=str(path)))
    assert not again.ok and path.read_text() == "测试\nExact version"
    assert read.await_count == 1


def test_canvas_export_refuses_relative_or_empty_content(monkeypatch, tmp_path):
    read = AsyncMock(return_value=DomainResult.success(item("")))
    monkeypatch.setattr(canvas, "get_canvas_artifact_result", read)
    relative = asyncio.run(canvas.export_canvas_result(None, artifact_id="artifact_fixture", output_path="relative.md"))
    assert not relative.ok and read.await_count == 0
    path = tmp_path / "empty.md"
    empty = asyncio.run(canvas.export_canvas_result(None, artifact_id="artifact_fixture", output_path=str(path)))
    assert not empty.ok and not path.exists()
