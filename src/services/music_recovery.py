"""Recover music cards belonging to the generated response's own turn."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from gemini_webapi.types.video import GeneratedMedia

from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .artifacts import response_chat_id


class MusicReadbackUnverified(RuntimeError):
    """A bounded music read did not establish the target turn's cards."""


class MusicErrorCardObserved(RuntimeError):
    """The exact completed candidate has the observed music error-card shape."""


def media_from_music_card(card_data: Mapping[str, str], *, client: Any, cid: str) -> GeneratedMedia | None:
    title = card_data.get("title") or "[Media]"
    url = card_data.get("url", "")
    if not url:
        return None
    name = title.lower()
    if not name.endswith((".mp3", ".mp4")):
        try:
            parsed = urlsplit(url)
            url_name = parse_qs(parsed.query).get("filename", [""])[0] or unquote(parsed.path)
            name = url_name.lower()
        except ValueError:
            pass
    is_mp4 = name.endswith(".mp4")
    return GeneratedMedia(
        url=url if is_mp4 else "", thumbnail="", mp3_url="" if is_mp4 else url,
        mp3_thumbnail="", title=title, cid=cid, rid=card_data.get("rid", ""),
        rcid=card_data.get("rcid", ""), client_ref=client, proxy=getattr(client, "proxy", None),
    )


async def fetch_music_media_from_chat(
    client: Any, cid: str, *, rid: str = "", rcid: str = "", strict: bool = False,
) -> list[GeneratedMedia]:
    """Read only cards tied to this response; never claim another turn's music."""
    if not cid or not rid or not hasattr(client, "_batch_execute") or (strict and not rcid):
        if strict:
            raise MusicReadbackUnverified("Music readback identity or capability unavailable")
        return []
    contract = get_contract("media.music_chat")
    response = await execute_contract(client, contract.key, chat_id=cid)
    envelope = parse_rpc_envelope(response.text, contract.rpc_id)
    if strict:
        from ..infrastructure.rpc_parsers import parse_music_chat_turn_evidence

        if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
            raise MusicReadbackUnverified("Music readback envelope was not verified")
        parsed = parse_music_chat_turn_evidence(envelope.bodies[0], rid=rid, rcid=rcid)
        if parsed.status == "rejected":
            raise MusicErrorCardObserved("Music error card observed")
        if parsed.status not in {"success", "empty"}:
            raise MusicReadbackUnverified("Music readback target was not verified")
        return [item for card in (parsed.value or ())
                if (item := media_from_music_card(card, client=client, cid=cid)) is not None]
    media = []
    for body in envelope.bodies:
        parsed = parse_contract_body(contract, body, reject_code=envelope.reject_code)
        if parsed.status != "success":
            continue
        for card in parsed.value or ():
            if card.get("rid") != rid or (rcid and card.get("rcid") != rcid):
                continue
            item = media_from_music_card(card, client=client, cid=cid)
            if item:
                media.append(item)
    return media


def music_response_identity(response: Any) -> tuple[str, str, str]:
    metadata = getattr(response, "metadata", None)
    values = metadata if isinstance(metadata, (list, tuple)) else ()
    cid = response_chat_id(response) or ""
    rid = values[1] if len(values) > 1 and isinstance(values[1], str) else ""
    rcid = getattr(response, "rcid", "")
    if not isinstance(rcid, str) or not rcid:
        rcid = values[2] if len(values) > 2 and isinstance(values[2], str) else ""
    return cid, rid, rcid
