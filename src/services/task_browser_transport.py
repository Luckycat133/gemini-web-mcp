"""Ephemeral WebRTC/RFB connections to an exact observed Gemini Task VM."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from ..infrastructure.account_identity import current_account_key
from ..infrastructure.task_browser import browser_identity
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


class TaskBrowserTransportError(RuntimeError):
    code = 'UPSTREAM_CHANGED'


class BrowserDependencyUnavailable(TaskBrowserTransportError):
    code = 'CAPABILITY_UNAVAILABLE'


def _fence(owner: str | None) -> None:
    if not owner or current_account_key() != owner:
        raise TaskBrowserTransportError('The Task browser account changed or is unavailable.')


async def _rpc(client: Any, owner: str, key: str, **arguments: Any) -> Any:
    _fence(owner)
    contract = get_contract(key)
    response = await await_before_deadline(execute_contract(client, key, retry_count=0, **arguments), timeout=30)
    _fence(owner)
    if getattr(response, 'status_code', None) != 200:
        raise TaskBrowserTransportError('The browser signaling HTTP response was not accepted.')
    envelope = parse_rpc_envelope(getattr(response, 'text', '') or '', contract.rpc_id)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise TaskBrowserTransportError('The browser signaling envelope was not accepted.')
    parsed = parse_contract_body(contract, envelope.bodies[0])
    if not parsed.ok:
        raise TaskBrowserTransportError('The browser signaling shape changed.')
    return parsed.value


@asynccontextmanager
async def task_browser_connection(client: Any, observed: dict[str, Any]) -> AsyncIterator[Any]:
    """Connect one shared viewer; disconnect without saving profiles or resuming Tasks."""
    try:
        from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, RTCSessionDescription
        from aiortc.rtcicetransport import parse_stun_turn_uri
        from aiortc.sdp import SessionDescription, candidate_from_sdp, candidate_to_sdp
        from ..infrastructure.rfb import RFBClient
    except ImportError:
        raise BrowserDependencyUnavailable('Install gemini-web-mcp[browser-control] for Task browser access.') from None

    owner = current_account_key()
    _fence(owner)
    assert owner is not None
    try:
        identity = browser_identity(observed)
    except ValueError:
        raise TaskBrowserTransportError('The exact observed Task browser identity is unavailable.') from None
    servers = await _rpc(client, owner, 'tasks.browser_credentials')
    ice: list[Any] = []
    declared_turn, supported_turn = 0, 0
    for item in servers:
        valid = []
        for uri in item[0]:
            turn = uri.startswith(('turn:', 'turns:'))
            declared_turn += turn
            try:
                parse_stun_turn_uri(uri)
            except (ValueError, TypeError):
                continue
            valid.append(uri)
            supported_turn += turn
        # aiortc selects one TURN route. Prefer the server-declared TCP route;
        # UDP may be unavailable behind the user's ordinary network proxy.
        valid.sort(key=lambda uri: 0 if 'transport=tcp' in uri or uri.startswith('turns:') else 1)
        if valid:
            ice.append(RTCIceServer(urls=valid, username=item[1] if len(item) > 1 else None,
                                   credential=item[2] if len(item) > 2 else None))
    if not ice or declared_turn and not supported_turn:
        raise TaskBrowserTransportError('No supported server-declared browser relay is available.')
    ice.sort(key=lambda server: 0 if any('transport=tcp' in uri or uri.startswith('turns:') for uri in server.urls) else 1)
    peer = RTCPeerConnection(RTCConfiguration(iceServers=ice))
    reader = asyncio.StreamReader()
    opened = asyncio.Event()
    received_bytes = 0
    try:
        channel = peer.createDataChannel('data')

        @channel.on('open')
        def on_open() -> None:
            opened.set()

        @channel.on('message')
        def on_message(data: Any) -> None:
            nonlocal received_bytes
            if not isinstance(data, bytes):
                reader.set_exception(TaskBrowserTransportError('Unexpected browser stream data.'))
                return
            received_bytes += len(data)
            if received_bytes > 256 * 1024 * 1024:
                reader.set_exception(TaskBrowserTransportError('The browser stream exceeded its byte budget.'))
                channel.close()
                return
            reader.feed_data(data)

        @channel.on('close')
        def on_close() -> None:
            reader.feed_eof()
            opened.set()

        async def read_exact(size: int) -> bytes:
            _fence(owner)
            value = await await_before_deadline(reader.readexactly(size), timeout=20)
            _fence(owner)
            return bytes(value)

        def send(value: bytes) -> None:
            _fence(owner)
            if channel.readyState != 'open':
                raise TaskBrowserTransportError('The browser connection is closed.')
            channel.send(value)

        offer = await await_before_deadline(peer.createOffer(), timeout=10)
        _fence(owner)
        await await_before_deadline(peer.setLocalDescription(offer), timeout=40)
        _fence(owner)
        local = peer.localDescription
        if local is None:
            raise TaskBrowserTransportError('The browser offer was not created.')
        answer = await _rpc(client, owner, 'tasks.browser_offer', identity=identity, ice_servers=servers, sdp=local.sdp)
        if not isinstance(answer, dict):
            raise TaskBrowserTransportError('The browser signaling answer is unavailable.')
        await await_before_deadline(peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer['sdp'], type=answer['type'])), timeout=10)
        _fence(owner)
        for row in answer['candidates']:
            candidate = candidate_from_sdp(row[0].removeprefix('candidate:'))
            candidate.sdpMid = row[2] if len(row) > 2 and row[2] is not None else ''
            candidate.sdpMLineIndex = row[3] if len(row) > 3 and row[3] is not None else 0
            await await_before_deadline(peer.addIceCandidate(candidate), timeout=5)
            _fence(owner)
        local_rows: list[Any] = []
        for index, media in enumerate(SessionDescription.parse(local.sdp).media):
            mid = media.rtp.muxId
            if not isinstance(mid, str) or not mid:
                raise TaskBrowserTransportError('The browser ICE media identity is unavailable.')
            local_rows.extend([answer['session_id'], 'candidate:' + candidate_to_sdp(candidate), mid, index]
                              for candidate in media.ice_candidates)
        await _rpc(client, owner, 'tasks.browser_ice', identity=identity, candidates=local_rows)
        await await_before_deadline(opened.wait(), timeout=35)
        _fence(owner)
        if channel.readyState != 'open':
            raise TaskBrowserTransportError('The browser data channel did not open.')
        rfb = RFBClient(read_exact, send)
        await await_before_deadline(rfb.initialize(), timeout=25)
        _fence(owner)
        yield rfb
    finally:
        await await_before_deadline(peer.close(), timeout=5)
        _fence(owner)
