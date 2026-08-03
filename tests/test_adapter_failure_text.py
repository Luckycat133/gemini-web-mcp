"""Regression contracts for compatibility text and typed MCP failures."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import src.skill_server as skill_server
from src.adapters import attach_domain_result, domain_failure_text, domain_text
from src.adapters.mcp_sdk import TextContent
from src.domain import DomainErrorCode, DomainResult


def _payload(content: TextContent) -> dict:
    assert content.meta is not None
    payload = content.meta["domain_result"]
    assert isinstance(payload, dict)
    return payload


def _auth_failure() -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.AUTH_REQUIRED,
        "Gemini credentials are required.",
        suggested_action="Configure Gemini credentials and retry.",
        verification_status="authentication_required",
    )


def test_domain_failure_text_is_derived_from_the_typed_error() -> None:
    text = domain_failure_text(_auth_failure())

    assert text == (
        "AUTH_REQUIRED: Gemini credentials are required.\n"
        "Suggested action: Configure Gemini credentials and retry."
    )


def test_domain_text_replaces_only_a_contradictory_known_error_prefix() -> None:
    failure = _auth_failure()

    corrected = domain_text(
        failure,
        "SESSION_NOT_FOUND: Invalid session: sess_existing",
    )[0]
    ordinary_legacy = domain_text(
        failure,
        "Could not send the message.",
    )[0]

    assert corrected.text.startswith("AUTH_REQUIRED:")
    assert "SESSION_NOT_FOUND" not in corrected.text
    assert ordinary_legacy.text == "Could not send the message."
    assert _payload(corrected)["error"]["code"] == "AUTH_REQUIRED"


def test_attach_domain_result_corrects_contradictory_text_too() -> None:
    content = attach_domain_result(
        [TextContent(type="text", text="SESSION_NOT_FOUND: Invalid session")],
        _auth_failure(),
    )

    assert content[0].text.startswith("AUTH_REQUIRED:")
    assert _payload(content[0])["error"]["code"] == "AUTH_REQUIRED"


def test_matching_error_code_prefix_remains_backward_compatible() -> None:
    result = DomainResult.failure(
        DomainErrorCode.SESSION_NOT_FOUND,
        "The requested session does not exist.",
    )
    text = "SESSION_NOT_FOUND: Invalid session: sess_missing"

    content = domain_text(result, text)

    assert content[0].text == text
    assert _payload(content[0])["error"]["code"] == "SESSION_NOT_FOUND"


def test_compact_session_chat_does_not_mislabel_auth_failure_as_missing_session(monkeypatch) -> None:
    async def fail_send(_request):
        return _auth_failure()

    monkeypatch.setattr(
        skill_server,
        "_chat_service",
        SimpleNamespace(send_session=fail_send),
    )

    content = asyncio.run(
        skill_server.chat(
            message="continue",
            session_id="sess_existing",
        )
    )

    assert content[0].text.startswith("AUTH_REQUIRED:")
    assert "SESSION_NOT_FOUND" not in content[0].text
    payload = _payload(content[0])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "AUTH_REQUIRED"
    assert payload["meta"]["operation_state"] == "failed"
