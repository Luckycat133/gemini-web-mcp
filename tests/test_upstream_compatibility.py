"""Compatibility contracts for supported gemini-webapi minor versions."""

from __future__ import annotations

from src import _install_gemini_webapi_compatibility_aliases


def test_read_chat_rpc_enum_rename_is_normalized_idempotently() -> None:
    from gemini_webapi.constants import GRPC

    _install_gemini_webapi_compatibility_aliases()
    _install_gemini_webapi_compatibility_aliases()

    assert hasattr(GRPC, "READ_CHAT")
    assert hasattr(GRPC, "LIST_CONVERSATION_TURNS")
    assert GRPC.READ_CHAT == GRPC.LIST_CONVERSATION_TURNS
