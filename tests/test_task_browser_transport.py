"""Private signaling, identity and RTC lifecycle checks without sockets."""

import asyncio
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.account_identity import current_account_key
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.infrastructure.task_browser import browser_identity
from src.services import task_browser_transport as transport

CID, RID, RCID = "c_fixture", "r_fixture", "rc_fixture"
VM = "projects/private-project/regions/private-region/microVms/private-vm"
TOKEN = "private-browser-relay-token"
SESSION = "private-browser-session-id"
SERVERS = [
    [["stun:stun.example.invalid"], None, None],
    [["turn:relay.example.invalid?transport=udp", "turn:relay.example.invalid?transport=tcp"], "private-user", TOKEN],
]
REMOTE = "1 1 UDP 2122260223 192.0.2.1 54400 typ host"
LOCAL = "2 1 TCP 1518280447 192.0.2.2 9 typ host tcptype passive"
ANSWER = ["v=0\r\nsynthetic-answer", "answer", SESSION, [["candidate:" + REMOTE, "ignored-field-two", "server-mid", 1]]]
IDENTITY = [VM, "private-browser-context", CID, RID, "private-browser-handle"]


def _observed():
    metadata = [None] * 12
    metadata[4], metadata[5], metadata[11] = VM, IDENTITY[1], IDENTITY[4]
    candidate = [None] * 13
    candidate[0], candidate[12] = RCID, [{"62": metadata}]
    return {"chat_id": CID, "response_id": RID, "candidate_id": RCID, "candidate": candidate}


def _response(key, body, *, status=200, reject=None):
    return SimpleNamespace(status_code=status, text=json.dumps([
        ["wrb.fr", get_contract(key).rpc_id, json.dumps(body), None, None, [reject] if reject is not None else None],
    ]))


@pytest.fixture(autouse=True)
def _account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-transport-account-a")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")


def test_browser_identity_is_exact_and_extraction_does_not_modify_the_native_turn():
    observed = _observed()
    original = deepcopy(observed)
    assert browser_identity(observed) == IDENTITY
    assert observed == original


@pytest.mark.parametrize("problem", [
    "not_object", "missing_chat", "wrong_response", "short_candidate", "wrong_candidate", "missing_candidate_id",
    "non_rc_candidate", "wrong_requested_candidate", "missing_rich", "wrong_extension", "missing_vm",
    "invalid_vm", "missing_context", "missing_handle", "oversize_identity",
])
def test_invalid_browser_identity_is_rejected_without_echoing_private_metadata(problem):
    observed = _observed()
    metadata = observed["candidate"][12][0]["62"]
    if problem == "not_object":
        observed = [TOKEN]
    elif problem == "missing_chat":
        observed["chat_id"] = "c_"
    elif problem == "wrong_response":
        observed["response_id"] = "bad-response-" + TOKEN
    elif problem == "short_candidate":
        observed["candidate"] = [RCID]
    elif problem == "wrong_candidate":
        observed["candidate_id"] = "rc_other"
    elif problem == "missing_candidate_id":
        observed.pop("candidate_id")
        observed["candidate"][0] = None
    elif problem == "non_rc_candidate":
        observed["candidate"][0] = observed["candidate_id"] = "not-an-rc-id"
    elif problem == "wrong_requested_candidate":
        observed["candidate_id"] = TOKEN
    elif problem == "missing_rich":
        observed["candidate"][12] = []
    elif problem == "wrong_extension":
        observed["candidate"][12] = [{"unknown": TOKEN}]
    elif problem == "missing_vm":
        metadata[4] = None
    elif problem == "invalid_vm":
        metadata[4] = "https://private.invalid/" + TOKEN
    elif problem == "missing_context":
        metadata[5] = ""
    elif problem == "missing_handle":
        metadata[11] = None
    else:
        metadata[11] = TOKEN * 200
    with pytest.raises(ValueError) as caught:
        browser_identity(observed)
    assert TOKEN not in str(caught.value) and VM not in str(caught.value)


@pytest.mark.parametrize("key,body,arguments", [
    ("tasks.browser_credentials", [SERVERS], {}),
    ("tasks.browser_offer", ANSWER, {"identity": IDENTITY, "ice_servers": SERVERS, "sdp": "v=0\r\nsynthetic-offer"}),
    ("tasks.browser_ice", [], {"identity": IDENTITY, "candidates": [[SESSION, "candidate:" + LOCAL, "local-mid", 0]]}),
])
def test_browser_rpc_uses_the_registry_and_disables_sdk_replay(key, body, arguments):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response(key, body)))
    result = asyncio.run(transport._rpc(client, current_account_key(), key, **arguments))
    if key == "tasks.browser_credentials":
        assert result == SERVERS
    elif key == "tasks.browser_offer":
        assert result["session_id"] == SESSION and result["candidates"] == ANSWER[3]
    else:
        assert result is None
    client._batch_execute.assert_awaited_once()
    call = client._batch_execute.await_args
    assert call.kwargs["current_retry"] == 0 and call.kwargs["source_path"] == "/app"
    assert call.args[0][0].rpcid == get_contract(key).rpc_id
    assert json.loads(call.args[0][0].payload) == json.loads(get_contract(key).build_payload(**arguments))


@pytest.mark.parametrize("problem", ["http", "missing", "wrong_rpc", "rejected", "malformed_json", "changed_shape", "multiple"])
def test_untrusted_rpc_envelopes_fail_without_disclosing_private_response_content(problem, caplog):
    key = "tasks.browser_credentials"
    response = _response(key, [SERVERS])
    if problem == "http":
        response.status_code = 403
    elif problem == "missing":
        response.text = "[]"
    elif problem == "wrong_rpc":
        response = _response("tasks.browser_offer", ANSWER)
    elif problem == "rejected":
        response = _response(key, [SERVERS], reject=7)
    elif problem == "malformed_json":
        response.text = TOKEN
    elif problem == "changed_shape":
        response = _response(key, {"unexpected": TOKEN})
    else:
        response.text = json.dumps(json.loads(response.text) * 2)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=response))
    with pytest.raises(transport.TaskBrowserTransportError) as caught:
        asyncio.run(transport._rpc(client, current_account_key(), key))
    assert TOKEN not in str(caught.value) and VM not in str(caught.value)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("key,body,expected", [
    ("tasks.browser_credentials", [[]], []),
    ("tasks.browser_offer", [], None),
])
def test_empty_signaling_values_are_preserved_without_inventing_a_connection(key, body, expected):
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_response(key, body)))
    arguments = {"identity": IDENTITY, "ice_servers": SERVERS, "sdp": "v=0"} if key.endswith("offer") else {}
    assert asyncio.run(transport._rpc(client, current_account_key(), key, **arguments)) == expected


@pytest.mark.parametrize("body,key", [
    ([[[["https://private.invalid/" + TOKEN]]]], "tasks.browser_credentials"),
    ([[[["turn:relay.invalid"]], TOKEN, TOKEN, TOKEN]], "tasks.browser_credentials"),
    (["v=0", "offer", SESSION, []], "tasks.browser_offer"),
    (["v=0", "answer", SESSION, [["candidate:x", None, 42, 0]]], "tasks.browser_offer"),
    (["v=0", "answer", SESSION, [["candidate:x", None, "mid", True]]], "tasks.browser_offer"),
])
def test_malformed_private_signaling_metadata_has_no_accepted_projection(body, key):
    parsed = parse_contract_body(key, body)
    assert not parsed.ok and parsed.value is None
    assert TOKEN not in repr(parsed) and VM not in repr(parsed)


def test_account_change_during_browser_rpc_discards_the_response(monkeypatch):
    owner = current_account_key()

    async def switch(*args, **kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-transport-account-b")
        return _response("tasks.browser_credentials", [SERVERS])

    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=switch))
    with pytest.raises(transport.TaskBrowserTransportError):
        asyncio.run(transport._rpc(client, owner, "tasks.browser_credentials"))
    client._batch_execute.assert_awaited_once()


class Runtime:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.switch_at = None
        self.fail_at = None
        self.stages = []
        self.peers = []
        self.rfbs = []
        self.rpc_calls = []
        self.remote_candidates = []
        self.servers = deepcopy(SERVERS)
        self.answer = deepcopy(ANSWER)
        self.client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=self.batch))
        runtime = self

        class Channel:
            def __init__(self):
                self.readyState = "connecting"
                self.callbacks = {}
                self.sent = []

            def on(self, event):
                def register(callback):
                    self.callbacks[event] = callback
                    return callback
                return register

            def send(self, data):
                self.sent.append(data)

            def close(self):
                self.readyState = "closed"
                self.callbacks["close"]()

        class Peer:
            def __init__(self, configuration):
                self.configuration = configuration
                self.localDescription = None
                self.close_calls = 0
                self.channel = Channel()
                runtime.peers.append(self)

            def createDataChannel(self, label):
                assert label == "data"
                return self.channel

            async def createOffer(self):
                await runtime.step("create_offer")
                return SimpleNamespace(sdp="v=0\r\nsynthetic-local", type="offer")

            async def setLocalDescription(self, offer):
                await runtime.step("local_description")
                self.localDescription = offer

            async def setRemoteDescription(self, description):
                assert description.sdp == ANSWER[0] and description.type == "answer"
                await runtime.step("remote_description")
                self.channel.readyState = "open"
                self.channel.callbacks["open"]()
                if runtime.switch_at != "read_exact":
                    self.channel.callbacks["message"](b"RFB")

            async def addIceCandidate(self, candidate):
                runtime.remote_candidates.append(candidate)
                await runtime.step("remote_candidate")

            async def close(self):
                self.close_calls += 1
                await runtime.step("close")

        class RFB:
            def __init__(self, read_exact, send):
                self.read_exact, self.send = read_exact, send
                self.initialized = False
                self.user_inputs = []
                runtime.rfbs.append(self)

            async def initialize(self):
                await runtime.step("rfb_initialize")
                self.send(b"synthetic RFB handshake")
                if runtime.switch_at == "read_exact":
                    def switch_and_feed():
                        runtime.monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-transport-account-b")
                        runtime.peers[0].channel.callbacks["message"](b"RFB")
                    asyncio.get_running_loop().call_soon(switch_and_feed)
                assert await self.read_exact(3) == b"RFB"
                self.initialized = True

            def click(self, *args):
                self.user_inputs.append(args)
                raise AssertionError("Connecting must not send browser input")

            key = type_text = scroll = click

        aiortc = ModuleType("aiortc")
        aiortc.RTCPeerConnection = Peer
        aiortc.RTCConfiguration = lambda **kwargs: SimpleNamespace(**kwargs)
        aiortc.RTCIceServer = lambda **kwargs: SimpleNamespace(**kwargs)
        aiortc.RTCSessionDescription = lambda **kwargs: SimpleNamespace(**kwargs)
        ice = ModuleType("aiortc.rtcicetransport")

        def parse_uri(uri):
            if "unsupported" in uri:
                raise ValueError("Unsupported synthetic relay")
            return {"url": uri}

        ice.parse_stun_turn_uri = parse_uri
        sdp = ModuleType("aiortc.sdp")
        media = [SimpleNamespace(rtp=SimpleNamespace(muxId="local-mid"),
                                 ice_candidates=[SimpleNamespace(value=LOCAL)])]
        sdp.SessionDescription = SimpleNamespace(parse=lambda value: SimpleNamespace(media=media))
        sdp.candidate_from_sdp = lambda value: SimpleNamespace(value=value)
        sdp.candidate_to_sdp = lambda candidate: candidate.value
        rfb = ModuleType("src.infrastructure.rfb")
        rfb.RFBClient = RFB
        for name, module in [("aiortc", aiortc), ("aiortc.rtcicetransport", ice), ("aiortc.sdp", sdp),
                             ("src.infrastructure.rfb", rfb)]:
            monkeypatch.setitem(sys.modules, name, module)
        original_deadline = transport.await_before_deadline

        async def deadline(awaitable, *, timeout):
            value = await original_deadline(awaitable, timeout=timeout)
            if timeout == 35:
                await runtime.step("opened_wait")
            return value

        monkeypatch.setattr(transport, "await_before_deadline", deadline)

    async def step(self, stage):
        self.stages.append(stage)
        await asyncio.sleep(0)
        if stage == self.switch_at:
            self.monkeypatch.setenv("GEMINI_PSID", "synthetic-browser-transport-account-b")
        if stage == self.fail_at:
            raise RuntimeError("Synthetic RTC failure")

    async def batch(self, calls, **kwargs):
        keys = ("tasks.browser_credentials", "tasks.browser_offer", "tasks.browser_ice")
        key = next(key for key in keys if get_contract(key).rpc_id == calls[0].rpcid)
        self.rpc_calls.append((key, json.loads(calls[0].payload), kwargs))
        await self.step({keys[0]: "credentials", keys[1]: "offer_rpc", keys[2]: "ice_rpc"}[key])
        body = [self.servers] if key == keys[0] else self.answer if key == keys[1] else []
        return _response(key, body)


@pytest.fixture
def runtime(monkeypatch):
    return Runtime(monkeypatch)


def test_rtc_connection_uses_server_relays_tcp_first_and_the_distinct_candidate_field_layouts(runtime):
    async def run():
        async with transport.task_browser_connection(runtime.client, _observed()) as rfb:
            assert rfb.initialized and not rfb.user_inputs
            return rfb

    rfb = asyncio.run(run())
    peer = runtime.peers[0]
    assert peer.close_calls == 1 and not rfb.user_inputs
    servers = peer.configuration.iceServers
    assert servers[0].urls == [SERVERS[1][0][1], SERVERS[1][0][0]]
    assert servers[0].username == SERVERS[1][1] and servers[0].credential == TOKEN
    assert {url for server in servers for url in server.urls} == {url for server in SERVERS for url in server[0]}
    remote = runtime.remote_candidates[0]
    assert (remote.value, remote.sdpMid, remote.sdpMLineIndex) == (REMOTE, "server-mid", 1)
    assert [key for key, _, _ in runtime.rpc_calls] == ["tasks.browser_credentials", "tasks.browser_offer", "tasks.browser_ice"]
    offer = runtime.rpc_calls[1][1]
    assert offer == [IDENTITY, SERVERS, "v=0\r\nsynthetic-local", "offer"]
    assert runtime.rpc_calls[2][1] == [IDENTITY, None, [[SESSION, "candidate:" + LOCAL, "local-mid", 0]]]
    assert all(options["current_retry"] == 0 for _, _, options in runtime.rpc_calls)
    assert peer.channel.sent == [b"synthetic RFB handshake"]


@pytest.mark.parametrize("stage", ["credentials", "create_offer", "local_description", "offer_rpc", "remote_description",
                                  "remote_candidate", "ice_rpc", "opened_wait", "rfb_initialize", "read_exact", "close"])
def test_rtc_account_is_fenced_after_each_await_and_created_peers_are_closed(runtime, stage):
    runtime.switch_at = stage

    async def run():
        async with transport.task_browser_connection(runtime.client, _observed()):
            pass

    with pytest.raises(transport.TaskBrowserTransportError):
        asyncio.run(run())
    assert all(peer.close_calls == 1 for peer in runtime.peers)
    assert all(not rfb.user_inputs for rfb in runtime.rfbs)


@pytest.mark.parametrize("stage", ["create_offer", "local_description", "offer_rpc", "remote_description",
                                  "remote_candidate", "ice_rpc", "opened_wait", "rfb_initialize"])
def test_rtc_failure_always_closes_the_constructed_peer_without_extra_mutations(runtime, stage):
    runtime.fail_at = stage

    async def run():
        async with transport.task_browser_connection(runtime.client, _observed()):
            raise AssertionError("The failed connection must not be yielded")

    with pytest.raises(RuntimeError, match="Synthetic RTC failure"):
        asyncio.run(run())
    assert len(runtime.peers) == 1 and runtime.peers[0].close_calls == 1
    assert all(not rfb.user_inputs for rfb in runtime.rfbs)
    assert all(key in {"tasks.browser_credentials", "tasks.browser_offer", "tasks.browser_ice"} for key, _, _ in runtime.rpc_calls)


def test_caller_failure_still_closes_the_viewer_without_saving_a_vm_profile(runtime):
    async def run():
        async with transport.task_browser_connection(runtime.client, _observed()):
            raise RuntimeError("Caller stopped observing")

    with pytest.raises(RuntimeError, match="Caller stopped observing"):
        asyncio.run(run())
    assert runtime.peers[0].close_calls == 1
    assert len(runtime.rpc_calls) == 3 and not runtime.rfbs[0].user_inputs


@pytest.mark.parametrize("servers", [[], [[["turn:unsupported.example.invalid"], "user", TOKEN]]])
def test_no_supported_server_declared_relay_never_constructs_a_peer(runtime, servers):
    runtime.servers = servers

    async def run():
        async with transport.task_browser_connection(runtime.client, _observed()):
            raise AssertionError("No relay must not yield a connection")

    with pytest.raises(transport.TaskBrowserTransportError):
        asyncio.run(run())
    assert not runtime.peers and len(runtime.rpc_calls) == 1
