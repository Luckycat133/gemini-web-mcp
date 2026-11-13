"""Parse the observed generated-file card from one exact Task turn."""

from typing import Any
from urllib.parse import urlsplit

from ..domain.task_files import TaskFile
from .rpc_parsers import RPCParseResult


def _get(value: Any, *indices: int) -> Any:
    for index in indices:
        if not isinstance(value, list) or index >= len(value):
            return None
        value = value[index]
    return value


def parse_task_files(body: Any, *, rid: str, rcid: str) -> RPCParseResult:
    """Require one completed exact candidate and an intact observed file-card shape."""
    if not rid or not rcid or not isinstance(body, list) or not body or not isinstance(body[0], list):
        return RPCParseResult("changed_shape")
    matches: list[Any] = []
    for turn in body[0]:
        if _get(turn, 0, 1) != rid:
            continue
        candidates = _get(turn, 3, 0)
        if not isinstance(candidates, list):
            return RPCParseResult("changed_shape")
        matches.extend(c for c in candidates if _get(c, 0) == rcid)
    if len(matches) != 1 or _get(matches[0], 8, 0) != 2:
        return RPCParseResult("changed_shape")
    container = _get(matches[0], 12)
    if container is not None and not isinstance(container, list):
        return RPCParseResult("changed_shape")
    rich = _get(container, 0)
    if rich is None:
        return RPCParseResult("empty", value=())
    if not isinstance(rich, dict):
        return RPCParseResult("changed_shape")
    cards = rich.get("59")
    if cards is None or cards == []:
        return RPCParseResult("empty", value=())
    if not isinstance(cards, list):
        return RPCParseResult("changed_shape")
    files = []
    for group in cards:
        if not isinstance(group, list):
            return RPCParseResult("changed_shape")
        for card in group:
            card_id, data = _get(card, 0), _get(card, 2)
            name, mime, url = _get(data, 2), _get(data, 11), _get(data, 7, 1)
            if not all(isinstance(v, str) and v.strip() for v in (card_id, name, mime, url)):
                return RPCParseResult("changed_shape")
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == "https" and parsed.hostname == "contribution.usercontent.google.com"
                         and parsed.path == "/download" and not parsed.username and not parsed.password
                         and parsed.port is None)
            except ValueError:
                valid = False
            if not valid:
                return RPCParseResult("changed_shape")
            files.append(TaskFile(card_id, name, mime, url))
    if not files or len({f.card_id for f in files}) != len(files):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=tuple(files))
