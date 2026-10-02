"""Explicit verified RPC observations for adapter branch tests."""

import json
from types import SimpleNamespace

from src.infrastructure.rpc_contracts import get_contract


def scheduled_ack_response():
    return SimpleNamespace(
        status_code=200,
        text=json.dumps([["wrb.fr", get_contract("scheduled.delete").rpc_id, "[]", None, None, None, "generic"]]),
    )


def scheduled_read(value):
    return value, {
        "status_code": 200,
        "body_present": True,
        "parser_status": "success" if value else "empty",
        "parser_warnings": [],
        "reject_code": None,
        "read_back_valid": True,
    }
