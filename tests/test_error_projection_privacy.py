"""Private exception text must not be projected into tool results."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.error_handler import format_error_response, handle_error, wrap_tool_error
from src.services.cleanup import _CleanupScanOptions, _cleanup_matching_chats, _cleanup_matching_scheduled
from src.services.doctor import _collect_profiles
from src.services.history import export_chat_result, search_chats_result

SECRET = 'https://example.test/download?signature=synthetic-secret'


def test_legacy_error_decorator_keeps_exception_text_out_of_logs(caplog):
    @wrap_tool_error
    async def fail():
        raise RuntimeError(SECRET)

    result = asyncio.run(fail())
    assert "RuntimeError" in caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in result[0].text


def test_history_search_error_exposes_type_only():
    client = SimpleNamespace(read_chat=AsyncMock(side_effect=RuntimeError(SECRET)))
    result = asyncio.run(search_chats_result(client, [{'id': 'c_owned', 'title': 'needle'}],
                                              'needle', 10, 0, scan_turns=True))
    assert result.data['matches'][0]['snippets'] == [{'error': 'RuntimeError'}]
    assert SECRET not in json.dumps(result.data)


def test_history_export_metadata_warning_exposes_type_only():
    client = SimpleNamespace(read_chat=AsyncMock(return_value=SimpleNamespace(turns=[])))
    loader = AsyncMock(side_effect=RuntimeError(SECRET))
    result = asyncio.run(export_chat_result(client, 'c_owned', 10, 200, metadata_loader=loader))
    assert result.data['metadata']['metadata_warning'] == 'RuntimeError'
    assert SECRET not in json.dumps(result.data)


def test_doctor_profile_failure_exposes_type_only():
    def provider(*_args, **_kwargs):
        raise RuntimeError(SECRET)
    profiles = _collect_profiles('chrome', False, provider)
    assert profiles == [{'browser': 'chrome', 'error': 'RuntimeError'}]
    assert SECRET not in json.dumps(profiles)


def test_cleanup_chat_errors_preserve_target_and_type(monkeypatch):
    import src.services.cleanup as cleanup
    client = SimpleNamespace(list_chats=lambda: [{'id': 'c_owned', 'title': 'marker'}],
                             read_chat=AsyncMock(side_effect=RuntimeError(SECRET)), delete_chat=AsyncMock())
    monkeypatch.setattr(cleanup, 'delete_chat_result', AsyncMock(side_effect=RuntimeError(SECRET)))
    options = _CleanupScanOptions(markers=['marker'], chat_limit=10, scan_turns=True, dry_run=False)
    matches, errors = asyncio.run(_cleanup_matching_chats(client, options))
    assert errors == [{'target': 'chat:c_owned', 'error': 'RuntimeError'}]
    assert matches[0]['delete_error'] == 'RuntimeError' and not matches[0]['deleted']
    assert SECRET not in json.dumps([matches, errors])


def test_cleanup_schedule_failures_expose_type_only(monkeypatch):
    import src.services.cleanup as cleanup
    client = SimpleNamespace(_batch_execute=AsyncMock())
    options = _CleanupScanOptions(markers=['marker'], chat_limit=10, scan_turns=False, dry_run=False)
    failed_registry = AsyncMock(side_effect=RuntimeError(SECRET))
    matches, errors = asyncio.run(_cleanup_matching_scheduled(client, options, fetch_registry=failed_registry,
        fetch_by_id=AsyncMock(), extract_bodies=lambda *_args: []))
    assert not matches and errors == [{'target': 'scheduled', 'error': 'RuntimeError'}]
    registry = AsyncMock(return_value=([{'id': 'task_1', 'title': 'marker'}], {}))
    monkeypatch.setattr(cleanup, 'delete_action', AsyncMock(side_effect=RuntimeError(SECRET)))
    matches, errors = asyncio.run(_cleanup_matching_scheduled(client, options, fetch_registry=registry,
        fetch_by_id=AsyncMock(), extract_bodies=lambda *_args: []))
    assert matches[0]['delete_error'] == 'RuntimeError' and not errors
    assert SECRET not in json.dumps(matches)


def test_unknown_exception_response_does_not_expose_exception_text():
    secret = 'synthetic-private-value'
    result = handle_error(RuntimeError(secret))
    assert result['error_type'] == 'RuntimeError' and not result['actionable']
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in format_error_response(result).text
