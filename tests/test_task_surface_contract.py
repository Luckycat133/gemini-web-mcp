"""Public Task adapters preserve rejected state in their compatibility text."""

import asyncio

import src.skill_server as compact


def test_invalid_compact_task_session_does_not_claim_creation():
    content = asyncio.run(compact._session_create("flash", "standard", "quiz", True))
    result = content[0].meta["domain_result"]
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "INVALID_ARGUMENT" in content[0].text
    assert "Session created" not in content[0].text
