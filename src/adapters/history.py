"""Compact history presentation with the original typed state preserved."""

import json

from .mcp_sdk import TextContent
from ..services.history import compact_history_payload


def compact_history_content(action: str, content: list[TextContent]) -> list[TextContent]:
    if not content:
        return content
    first = content[0]
    meta = dict(first.meta or {})
    result = meta.get("domain_result")
    if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(result.get("data"), dict):
        return content
    payload = compact_history_payload(action, result["data"])
    meta["domain_result"] = {**result, "data": payload}
    return [first.model_copy(update={"text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                                     "meta": meta}), *content[1:]]
