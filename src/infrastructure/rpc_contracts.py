"""Central registry for observed Gemini Web RPC contracts.

The Web RPC layer is deliberately kept below MCP handlers.  A contract owns the
RPC id, source path, payload builder, parser name, and the evidence describing
where it was observed.  Services refer to a stable contract key rather than
embedding private RPC ids or payload shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import re
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping
import unicodedata
from urllib.parse import urlsplit
# --- ported from main (v0.2.2) for the rebased branch ---
def _canvas_chat_payload(*, chat_id: str, limit: int = 10, next_page_token: str | None = None) -> str:
    return _compact([chat_id, limit, next_page_token, 1, [1], [4], None, 1])


# --- ported from main (v0.2.2) for the rebased branch ---
def _canvas_get_payload(*, artifact_id: str) -> str:
    return _compact([artifact_id])


# --- ported from main (v0.2.2) for the rebased branch ---
def _public_link_delete_payload(*, token: str) -> str:
    # Field 3 is the unrelated delete-all switch; never populate it.
    return _compact([None, token])


# --- ported from main (v0.2.2) for the rebased branch ---
def _public_link_create_payload(*, chat_id: str, response_id: str, title: str,
                                full_conversation: bool = True, locale: str = "zh-CN") -> str:
    # f10=1 requests a public link. Drive export and selected artifact expansion
    # have separate UI inputs and are deliberately absent from this contract.
    return _compact([None, chat_id, None, response_id, [full_conversation, title],
                     [locale], False, None, None, 1])


# --- ported from main (v0.2.2) for the rebased branch ---
def _library_delete_payload(*, target: list[Any]) -> str:
    return _compact([[target]])


# --- ported from main (v0.2.2) for the rebased branch ---
def _library_page_payload(*, section: str = "all", page_size: int = 30,
                          next_page_token: str | None = None) -> str:
    filters = {
        "all": [True, True, True, True, True, False, True, True, True, True],
        "documents": [False, False, False, True, True, False, False, True, False, False],
        "media": [True, True, True, False, False, False, True, False, True, True],
        "purchases": [False, False, False, False, False, True, False, False, False, False],
    }
    value: list[Any] = [filters[section], page_size]
    if next_page_token:
        value.append(next_page_token)
    return _compact(value)


# --- ported from main (v0.2.2) for the rebased branch ---
def _native_skill_save_payload(
    *, upload_reference: str = "", skill_id: str = "", mode: int, enabled: bool,
    external_resources: list[list[Any]] | None = None,
) -> str:
    if mode not in {2, 3} or type(enabled) is not bool:
        raise ValueError("Native Skill save requires mode 2 (update) or 3 (create) and a boolean enabled flag.")
    payload: list[Any] = [upload_reference, skill_id or None, mode, enabled]
    if external_resources:
        payload.append(external_resources)
    return _compact(payload)


# --- ported from main (v0.2.2) for the rebased branch ---
def _native_skill_id_payload(*, skill_id: str) -> str:
    return _compact([skill_id])


# --- ported from main (v0.2.2) for the rebased branch ---
def _notebook_source_payload(*, notebook_id: str, source: list[Any]) -> str:
    return _compact([notebook_id, source, [1, 3]])


# --- ported from main (v0.2.2) for the rebased branch ---
def _notebook_id_payload(*, notebook_id: str) -> str:
    return _compact([notebook_id])


# --- ported from main (v0.2.2) for the rebased branch ---
def _notebook_metadata_payload(*, metadata: list[Any], notebook_id: str | None = None) -> str:
    return _compact([metadata] if notebook_id is None else [notebook_id, metadata])


# --- ported from main (v0.2.2) for the rebased branch ---
def _notebook_get_payload(*, notebook_id: str, locale: str = "zh-CN") -> str:
    return _compact([notebook_id, [locale or "zh-CN"], False])


# --- ported from main (v0.2.2) for the rebased branch ---
def _update_chat_payload(*, chat_id: str, title: str | None = None, pinned: bool | None = None) -> str:
    """Update only the requested metadata fields (UpdateConversation FieldMask)."""
    fields: list[str] = []
    conversation: list[Any] = [chat_id]
    if title is not None:
        fields.append("title")
        conversation.append(title)
    if pinned is not None:
        fields.append("pinned")
        conversation.extend([None] * (3 - len(conversation)))
        conversation[2] = pinned
    if not fields:
        raise ValueError("A title or pinned value is required.")
    return _compact([None, [fields], conversation])


# --- ported from main (v0.2.2) for the rebased branch ---
def native_notebook_source(
    *, display_name: str, mime_type: str, text: str | None = None,
    url: str | None = None, upload_reference: str | None = None,
) -> list[Any]:
    """lbYGFc jyh source oneof (URL 4 / uploaded file 5 / text 6)."""
    if sum(value is not None for value in (text, url, upload_reference)) != 1:
        raise ValueError("Exactly one Notebook source input is required.")
    source: list[Any] = [None] * 9
    source[1], source[8] = display_name, mime_type
    if text is not None:
        source[5] = [text, 2 if mime_type == "text/markdown" else 1]
    elif url is not None:
        source[3] = [url]
    else:
        source[4] = [upload_reference, mime_type]
    return source


# --- ported from main (v0.2.2) for the rebased branch ---
def native_notebook_metadata(*, title: str, description: str, instructions: str, subtype: int) -> list[Any]:
    """ScPWJf xwh -> Vjd; business subtype 4 uses a different creation flow."""
    if type(subtype) is not int or subtype not in {1, 2, 3}:
        raise ValueError("Supported native Notebook subtype must be 1, 2 or 3.")
    metadata: list[Any] = [None] * 17
    metadata[0], metadata[1], metadata[2] = title, description, instructions
    metadata[4], metadata[7], metadata[9], metadata[10], metadata[11] = [], [], False, 1, []
    metadata[16] = [2, None, None, None, subtype, []]
    return metadata


# --- ported from main (v0.2.2) for the rebased branch ---
def native_skill_upload_url_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname in _NATIVE_SKILL_UPLOAD_HOSTS
                    and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        return False

_NATIVE_SKILL_UPLOAD_HOSTS = {
    "content-push.googleapis.com", "contribution.usercontent.google.com", "push.clients6.google.com",
}


# --- ported from main (v0.2.2) for the rebased branch ---
def native_skill_upload_headers(
    *, phase: Literal["start", "finalize"], content_size: int, push_id: str, client_pctx: str = "",
) -> dict[str, str]:
    """main:4353-4361 tM resumable protocol and 6771 ZIP File MIME."""
    if type(content_size) is not int or content_size <= 0 or not push_id:
        raise ValueError("Native Skill upload requires a nonempty ZIP and Push-ID.")
    headers = {"Push-ID": push_id, "X-Tenant-Id": "agent_skills"}
    if client_pctx:
        headers["X-Client-Pctx"] = client_pctx
    if phase == "start":
        headers.update({"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                        "X-Goog-Upload-Header-Content-Length": str(content_size)})
    elif phase == "finalize":
        headers.update({"Content-Type": "application/zip", "X-Goog-Upload-Command": "upload, finalize",
                        "X-Goog-Upload-Offset": "0"})
    else:
        raise ValueError("Unknown native Skill upload phase.")
    return headers


# --- ported from main (v0.2.2) for the rebased branch ---
def native_skill_name_valid(name: str) -> bool:
    """lNX4ad:5293/5296 J5m name rule, including non-Latin letters."""
    if not name or name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    if any(char != "-" and unicodedata.category(char) not in {"Ll", "Lo", "Nd", "Nl", "No", "Mn", "Mc", "Me"}
           for char in name):
        return False
    return len(name.encode("utf-16-le")) // 2 <= 64


# --- ported from main (v0.2.2) for the rebased branch ---
NATIVE_SKILL_UPLOAD_URL = "https://content-push.googleapis.com/upload/"


# --- ported from main (v0.2.2) for the rebased branch ---
NATIVE_SKILL_PUSH_ID = "feeds/or3baakvdgsg4i"




RPCMode = Literal["read", "mutation"]
RPCStability = Literal["stable", "preview", "experimental"]
PayloadBuilder = Callable[..., str]


class RawRPCData:
    """Small payload object compatible with ``gemini-webapi`` batch RPCs."""

    def __init__(self, rpcid: str, payload: str, identifier: str = "generic"):
        self.rpcid = rpcid
        self.payload = payload
        self.identifier = identifier

    def serialize(self) -> list[Any]:
        return [self.rpcid, self.payload, None, self.identifier]


@dataclass(frozen=True, slots=True)
class RPCContract:
    """Evidence-backed description of one upstream RPC use."""

    key: str
    surface: str
    name: str
    rpc_id: str
    source_path: str
    mode: RPCMode
    payload_builder: PayloadBuilder
    parser: str
    observed: str
    stability: RPCStability = "experimental"
    verified_dependency: str = "gemini-webapi>=2.0.0"
    verification_strategy: str = "fixture_contract"

    def build_payload(self, **arguments: Any) -> str:
        return self.payload_builder(**arguments)

    def as_probe(self, **arguments: Any) -> dict[str, str]:
        return {
            "surface": self.surface,
            "name": self.name,
            "rpcid": self.rpc_id,
            "payload": self.build_payload(**arguments),
            "source_path": self.source_path,
            "observed": self.observed,
        }


@dataclass(frozen=True, slots=True)
class GraphQLReadContract:
    """A separately observed persisted query, never a synthetic batch RPC."""

    key: str
    endpoint: str
    query_signature: str
    parser: str
    observed: str


GRAPHQL_READ_CONTRACTS: Mapping[str, GraphQLReadContract] = MappingProxyType({
    "notebooklm.picker": GraphQLReadContract(
        key="notebooklm.picker",
        endpoint="https://gemini.google.com/_/BardChatUi/graphql/schemas/GEMINI_WEB_GRAPHQL/executeQuery",
        query_signature="2//7i/6hNce8+k21vSBk6MXemEMhzGDBdQT11Mb+ykkII=",
        parser="notebooklm_picker",
        observed="2026-11-01 public NotebooksDialogContent query and authenticated metadata read",
    ),
})


async def execute_graphql_read(client: Any, contract_key: str) -> Any:
    """Execute one account-authenticated metadata query without generation."""
    contract = GRAPHQL_READ_CONTRACTS[contract_key]
    return await client.client.get(contract.endpoint, params={"query_signature": contract.query_signature},
                                   headers={"Accept": "application/json"})


class WebWorkflowTransportChanged(RuntimeError):
    code = "UPSTREAM_CHANGED"


def apply_task_generation_transport(inner_request: Any, *, conversation_id: str | None = None,
                                    action_choice_id: str | None = None) -> None:
    """Observed Task feature50=40 and agent84=true on the SDK nonce scaffold."""
    if not isinstance(inner_request, list) or len(inner_request) <= 55:
        raise WebWorkflowTransportChanged("Task generation request shape changed.")
    if not isinstance(inner_request[0], list) or len(inner_request[0]) < 7:
        raise WebWorkflowTransportChanged("Task generation query shape changed.")
    if not isinstance(inner_request[3], str) or not inner_request[3].startswith("!"):
        raise WebWorkflowTransportChanged("Task generation nonce is unavailable.")
    if not isinstance(inner_request[4], str) or not inner_request[4]:
        raise WebWorkflowTransportChanged("Task generation context is unavailable.")
    if inner_request[49] != 1 or inner_request[54] != [[[[[1]]]]] or inner_request[55] != [[1]]:
        raise WebWorkflowTransportChanged("Task generation scaffolding changed.")
    if action_choice_id is not None and (conversation_id is None or not isinstance(action_choice_id, str)
            or not action_choice_id.strip() or len(action_choice_id) > 512
            or any(ord(char) < 32 for char in action_choice_id)):
        raise WebWorkflowTransportChanged("Task confirmation choice identity is unavailable.")
    inner_request[49], inner_request[54], inner_request[55] = 40, [], None
    if len(inner_request) < 84:
        inner_request.extend([None] * (84 - len(inner_request)))
    inner_request[83] = 1
    if conversation_id is not None:
        if not isinstance(conversation_id, str) or not re.fullmatch(r"c_[0-9a-f]{16}", conversation_id):
            raise WebWorkflowTransportChanged("Task continuation requires its observed conversation identity.")
        # Native confirmation query: FK.CONTINUATION_IDENTIFIERS (field 72),
        # rather than the legacy turn metadata (field 3). No context token is
        # fabricated or persisted. Observed in the controlled confirmation HAR.
        inner_request[2] = None
        inner_request[71] = [conversation_id, None, None, None, None, None, None, ""]
    if action_choice_id is not None:
        if len(inner_request) > 89 and inner_request[89] is not None:
            raise WebWorkflowTransportChanged("Task confirmation context changed.")
        inner_request.extend([None] * max(0, 90 - len(inner_request)))
        # Public RVe -> Fc.OS.nBa, encoded by _.bVd as ZUd.field3
        # within FK.field90. Subagent routing is intentionally unsupported.
        inner_request[89] = [None, None, action_choice_id]


def _varint_bytes(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _proto_bytes(field: int, value: bytes) -> bytes:
    return _varint_bytes((field << 3) | 2) + _varint_bytes(len(value)) + value


def notebook_file_reference(notebook_id: str, title: str) -> str:
    """Public picker File builder, independently byte-matched to the Web request.

    This is a reversible reference to a notebook, not an authentication token.
    """
    payload = (_proto_bytes(1, _proto_bytes(15, _proto_bytes(1, notebook_id.encode("utf-8"))))
               + b"\x10\x14" + _proto_bytes(3, title.encode("utf-8")) + b"\x48\x0e\x62\x00")
    return base64.b64encode(payload).decode("ascii")


def notebook_attachment(notebook_id: str, title: str, *, as_file_reference: bool = False) -> list[Any]:
    """Observed Query.attachments notebookFile oneof; unrelated to project moves."""
    if not isinstance(notebook_id, str) or not notebook_id.strip() or not isinstance(title, str):
        raise ValueError("A resolved notebook ID and title are required.")
    if as_file_reference:
        return [[None, 20, 14, ""], title, notebook_file_reference(notebook_id, title)]
    attachment: list[Any] = [None] * 12
    attachment[1] = title
    attachment[11] = [[notebook_id]]
    return attachment


def apply_notebook_attachments(inner_request: Any, notebooks: tuple[tuple[str, str], ...],
                               *, use_file_reference: bool = True) -> None:
    """Append explicit notebook references to the observed generation query."""
    if not isinstance(inner_request, list) or not inner_request or not isinstance(inner_request[0], list):
        raise WebWorkflowTransportChanged("Notebook generation request shape changed.")
    query = inner_request[0]
    if len(query) < 4 or (query[3] is not None and not isinstance(query[3], list)):
        raise WebWorkflowTransportChanged("Notebook attachment request shape changed.")
    additions = [notebook_attachment(identifier, title, as_file_reference=use_file_reference)
                 for identifier, title in notebooks]
    query[3] = [*(query[3] or []), *additions]


@dataclass(frozen=True, slots=True)
class GenerationTransportContract:
    """Observed StreamGenerate mode fields, separate from batch RPC probes."""

    key: str
    mode_index: int
    mode_value: int
    content_options_index: int
    observed: str


VIDEO_GENERATION_TRANSPORT = GenerationTransportContract(
    key="media.video_generation",
    mode_index=49,
    mode_value=11,
    content_options_index=9,
    observed="2026-10-31 Web request and video artifact; 2026-11-06 public Create video route confirms mode 11",
)


class VideoTransportChanged(RuntimeError):
    """The observed video request contract cannot be safely applied."""

    code = "UPSTREAM_CHANGED"


def apply_video_generation_transport(inner_request: Any) -> None:
    """Apply observed video fields only to a validated SDK nonce/context request.

    The SDK's existing research-mode request builder supplies nonce and context;
    these are transport scaffolding only, not a request to start Deep Research.
    Reject changed shapes rather than issuing an unverified plain-chat request.
    """
    if not isinstance(inner_request, list) or len(inner_request) <= 55:
        raise VideoTransportChanged("Video generation transport request shape changed")
    content = inner_request[0]
    if not isinstance(content, list) or len(content) < 7:
        raise VideoTransportChanged("Video generation content request shape changed")
    if not isinstance(inner_request[3], str) or not inner_request[3].startswith("!"):
        raise VideoTransportChanged("Video generation transport nonce is unavailable")
    if not isinstance(inner_request[4], str) or not inner_request[4]:
        raise VideoTransportChanged("Video generation transport context is unavailable")
    if inner_request[49] != 1 or inner_request[54] != [[[[[1]]]]] or inner_request[55] != [[1]]:
        raise VideoTransportChanged("Video generation transport scaffolding changed")
    if len(content) > VIDEO_GENERATION_TRANSPORT.content_options_index and content[9] is not None:
        raise VideoTransportChanged("Video generation content options already present")
    while len(content) <= VIDEO_GENERATION_TRANSPORT.content_options_index:
        content.append(None)
    inner_request[VIDEO_GENERATION_TRANSPORT.mode_index] = VIDEO_GENERATION_TRANSPORT.mode_value
    inner_request[54] = []
    inner_request[55] = [[1]]
    content[VIDEO_GENERATION_TRANSPORT.content_options_index] = [None] * 6 + [[[None, None, None, 1]]]


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _fixed(value: Any) -> PayloadBuilder:
    payload = value if isinstance(value, str) else _compact(value)

    def build() -> str:
        return payload

    return build


def _conversation_payload(
    *,
    filter_payload: list[Any],
    page_size: int,
    next_page_token: str | None = None,
) -> str:
    return _compact([page_size, next_page_token, filter_payload])


def _rename_chat_payload(*, chat_id: str, title: str) -> str:
    return _compact([None, [["title"]], [chat_id, title]])


def _pin_chat_payload(*, chat_id: str, is_pinned: bool) -> str:
    return _compact([None, [["pinned"]], [chat_id, None, is_pinned]])


def _remy_goals_payload(*, page_size: int = 13, next_page_token: str | None = None) -> str:
    return _compact([page_size, next_page_token] if next_page_token else [page_size])


def _native_notebooks_payload(*, locale: str = "zh-CN") -> str:
    return _compact([2, [locale or "zh-CN"], False, None, [2]])


def _notebook_chats_payload(
    *,
    notebook_id: str,
    page_size: int,
    next_page_token: str | None = None,
) -> str:
    return _compact([page_size, next_page_token, [None, None, True, notebook_id, True]])


def _move_chat_payload(*, chat_id: str, notebook_id: str, project_type: int = 2) -> str:
    conversation: list[Any] = [None] * 14
    conversation[0] = chat_id
    conversation[7] = notebook_id
    conversation[13] = [project_type]
    return _compact([None, [["bot_id", "bot_project_metadata"]], conversation])


def _scheduled_get_payload(*, action_id: str) -> str:
    return _compact([action_id])


def _scheduled_daily_payload(
    *,
    title: str,
    instructions: str,
    hour: int,
    timezone_name: str,
    locale: str,
) -> str:
    return _compact(
        [
            [
                [instructions, None, title, 1],
                None,
                [[[[hour]], None, None, None, [1, 4], None, None, [timezone_name]]],
                [None, locale],
                None,
                [1],
            ]
        ]
    )


def _scheduled_delete_payload(*, action_id: str) -> str:
    return _compact([None, [action_id]])


def _music_chat_payload(*, chat_id: str) -> str:
    return _compact([chat_id, 10, None, 1, [1], [4], None, 1])


def _research_chat_payload(*, chat_id: str) -> str:
    return _compact([chat_id, 10, None, 1, [1], [4], None, 1])


def _research_chat_plan_payload(*, chat_id: str, limit: int = 100) -> str:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Research chat read limit must be between 1 and 100")
    return _compact([chat_id, limit, None, 1, [1], [4], None, 1])


def _task_id_payload(*, chat_id: str) -> str:
    return _compact([chat_id])


def _task_metadata_update_payload(*, chat_id: str, field: str, value: str | bool) -> str:
    from ..domain.conversations import is_valid_remote_chat_id
    from ..domain.web_tasks import MAX_TASK_TITLE_LENGTH

    if not is_valid_remote_chat_id(chat_id):
        raise ValueError("An exact Task chat ID is required.")
    goal: list[Any] = [None] * 14
    goal[13] = chat_id
    if field == "description" and isinstance(value, str) and value.strip() and len(value) <= MAX_TASK_TITLE_LENGTH:
        goal[1] = value
    elif field == "pinned" and type(value) is bool:
        goal.extend([None] * 5)
        goal.append(int(value))
    else:
        raise ValueError("An observed Task metadata field and value are required.")
    return _compact([goal, [[field]]])


def _single_turn_payload(*, chat_id: str, rid: str) -> str:
    if not isinstance(chat_id, str) or not chat_id.startswith('c_') or not isinstance(rid, str) or not rid.startswith('r_'):
        raise ValueError('An exact conversation and turn are required')
    return _compact([chat_id, rid])


def _browser_identity(identity: Any) -> None:
    if (not isinstance(identity, list) or len(identity) != 5
            or any(not isinstance(value, str) or not value or len(value) > 16384 for value in identity)
            or not identity[0].startswith('projects/') or not identity[2].startswith('c_')
            or not identity[3].startswith('r_')):
        raise ValueError('An exact observed browser identity is required')


def _browser_offer_payload(*, identity: list[Any], ice_servers: list[Any], sdp: str) -> str:
    _browser_identity(identity)
    if not isinstance(sdp, str) or not sdp.startswith('v=0') or len(sdp) > 262144:
        raise ValueError('Invalid browser SDP offer')
    if not isinstance(ice_servers, list) or len(ice_servers) > 32:
        raise ValueError('Invalid browser ICE configuration')
    return _compact([identity, ice_servers, sdp, 'offer'])


def _browser_ice_payload(*, identity: list[Any], candidates: list[Any]) -> str:
    _browser_identity(identity)
    if not isinstance(candidates, list) or len(candidates) > 256:
        raise ValueError('Invalid browser ICE candidates')
    for row in candidates:
        if (not isinstance(row, list) or len(row) != 4
                or any(not isinstance(value, str) or len(value) > 8192 for value in row[:3])
                or type(row[3]) is not int or not 0 <= row[3] <= 128):
            raise ValueError('Invalid browser ICE candidate')
    return _compact([identity, None, candidates])


def _task_page_payload(*, page_size: int = 20, next_page_token: str | None = None) -> str:
    return _compact([page_size, next_page_token])


def _preference_list_payload(*, tool_name: str | None = None, function_name: str | None = None) -> str:
    """List only persistent user preferences, optionally by an exact function."""
    for value in (tool_name, function_name):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Invalid preference function filter.")
    if function_name is not None and tool_name is None:
        raise ValueError("A function filter requires its tool.")
    if tool_name is None:
        return _compact([2])
    return _compact([2, None, None, None, [[tool_name, function_name or ""]]])


def _preference_update_payload(*, grant_id: str, native_permission_id: list[Any], state: int) -> str:
    """Observed UKqEL: granted ID, complete PermissionId, and requested state."""
    if not isinstance(grant_id, str) or type(state) is not int or state not in (1, 2, 3):
        raise ValueError("Invalid preference identity or state.")
    if (not isinstance(native_permission_id, list) or not native_permission_id
            or not isinstance(native_permission_id[0], list) or len(native_permission_id[0]) < 2
            or any(not isinstance(value, str) or not value.strip() for value in native_permission_id[0][:2])):
        raise ValueError("An exact preference function identity is required.")
    attributes = native_permission_id[9] if len(native_permission_id) > 9 else None
    if attributes is not None and (not isinstance(attributes, list) or any(
            not isinstance(entry, list) or len(entry) < 2
            or not isinstance(entry[0], str) or not isinstance(entry[1], str) for entry in attributes)):
        raise ValueError("Invalid preference identity attributes.")
    return json.dumps([grant_id, native_permission_id, state], ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _preference_catalog_payload(*, locale: str = "zh-CN") -> str:
    """Fresh normal-Web tool consent metadata; no synthetic tool identities."""
    if not isinstance(locale, str) or not locale.strip():
        raise ValueError("A preference catalog locale is required.")
    return _compact([locale, None, None, None, True])


def _preference_revoke_payload(*, grant_id: str) -> str:
    """Revoke one observed granted ID in persistent wire scope 2 only."""
    if not isinstance(grant_id, str) or not grant_id.strip():
        raise ValueError("An observed nonempty permission grant ID is required.")
    return _compact([[grant_id, 2]])


def _contract(
    key: str,
    surface: str,
    name: str,
    rpc_id: str,
    source_path: str,
    payload_builder: PayloadBuilder,
    parser: str,
    observed: str,
    *,
    mode: RPCMode = "read",
    stability: RPCStability = "experimental",
    verification_strategy: str = "fixture_contract",
) -> RPCContract:
    return RPCContract(
        key=key,
        surface=surface,
        name=name,
        rpc_id=rpc_id,
        source_path=source_path,
        mode=mode,
        payload_builder=payload_builder,
        parser=parser,
        observed=observed,
        stability=stability,
        verification_strategy=verification_strategy,
    )


def _remy_proto(fields: Mapping[int, Any]) -> list[Any]:
    values: list[Any] = [None] * max(fields, default=0)
    for index, value in fields.items():
        values[index - 1] = value
    while values and values[-1] is None:
        values.pop()
    return values


def _remy_id_payload(*, schedule_id: str) -> str:
    if not isinstance(schedule_id, str) or not schedule_id.strip() or len(schedule_id) > 256:
        raise ValueError('Invalid schedule ID')
    return json.dumps([schedule_id], ensure_ascii=False)


def _remy_encode_timestamp(value: Any) -> list[Any]:
    from datetime import datetime, timezone

    if not isinstance(value, str):
        raise ValueError('Timestamp must be an aware ISO string')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('Timestamp must include timezone')
    dt = dt.astimezone(timezone.utc)
    import calendar

    seconds = calendar.timegm(dt.utctimetuple())
    match = re.search(r'[Tt ]\d{2}:\d{2}:\d{2}\.(\d+)', value)
    digits = match.group(1) if match else ''
    if len(digits) > 9:
        raise ValueError('Timestamp exceeds nanosecond precision')
    nanos = int(digits.ljust(9, '0')) if digits else 0
    return _remy_proto({1: seconds, 2: nanos})


def _remy_encode_trigger(trigger: Any) -> tuple[int, list[Any]]:
    from datetime import datetime

    if not isinstance(trigger, Mapping):
        raise ValueError('Trigger required')
    frequency = trigger.get('frequency')
    if frequency not in ('daily', 'weekly', 'hourly', 'monthly', 'yearly', 'once'):
        raise ValueError('Unsupported trigger')
    if any(k not in {'frequency', 'time', 'weekdays', 'days_of_month', 'month', 'day', 'minute', 'at', 'window'} for k in trigger):
        raise ValueError('Unknown trigger field')
    window = trigger.get('window')
    if window is not None and window not in ('overnight', 'daytime', 'evening'):
        raise ValueError('Invalid time window')
    window_code = {'overnight': 1, 'daytime': 2, 'evening': 3}.get(window) if isinstance(window, str) else None
    if frequency == 'hourly':
        minute = trigger.get('minute')
        if type(minute) is not int or not 0 <= minute <= 59 or window:
            raise ValueError('Invalid hourly trigger')
        return 18, _remy_proto({1: minute})
    if frequency == 'once':
        return 21, _remy_proto({1: _remy_encode_timestamp(trigger.get('at')), 2: window_code})
    clock = trigger.get('time')
    if not isinstance(clock, str) or not re.fullmatch(r'\d{2}:\d{2}', clock):
        raise ValueError('Time must be HH:MM')
    hour, minute = map(int, clock.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Invalid time')
    time_value = [hour, minute]
    if frequency == 'daily':
        return 4, _remy_proto({1: time_value, 2: window_code})
    if frequency in ('weekly', 'monthly'):
        days = trigger.get('weekdays' if frequency == 'weekly' else 'days_of_month')
        maximum = 7 if frequency == 'weekly' else 31
        if not isinstance(days, list) or not days or any(type(n) is not int or not 1 <= n <= maximum for n in days) or len(set(days)) != len(days):
            raise ValueError('Invalid days')
        return (5 if frequency == 'weekly' else 19), _remy_proto({1: sorted(days), 2: time_value, 3: window_code})
    month, day = trigger.get('month'), trigger.get('day')
    if type(month) is not int or type(day) is not int:
        raise ValueError('Invalid year date')
    datetime(2000, month, day)
    return 20, _remy_proto({1: [None, month, day], 2: time_value, 3: window_code})


def build_remy_schedule_create(*, spec: Mapping[str, Any]) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    title, instructions, tz = spec.get('title'), spec.get('instructions'), spec.get('timezone')
    if not isinstance(title, str) or not title.strip() or not isinstance(instructions, str) or not instructions.strip() or not isinstance(tz, str) or not tz.strip():
        raise ValueError('Title, instructions and timezone required')
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError('Invalid timezone') from exc
    if type(spec.get('kind', 1)) is not int or spec.get('kind', 1) != 1 or type(spec.get('status', 1)) is not int or spec.get('status', 1) != 1:
        raise ValueError('New Task schedules must be active')
    number, trigger = _remy_encode_trigger(spec.get('trigger'))
    fields: dict[int, Any] = {1: '', 2: title, 7: tz, 8: instructions, 9: '', 10: 1, 25: 1, number: trigger}
    for name, field in (('starts_at', 14), ('ends_at', 15)):
        if spec.get(name) is not None:
            fields[field] = _remy_encode_timestamp(spec[name])
    if spec.get('project_id'):
        if not isinstance(spec['project_id'], str):
            raise ValueError('Invalid project ID')
        fields[27] = spec['project_id']
    return json.dumps([_remy_proto(fields)], ensure_ascii=False, separators=(',', ':'))


def build_remy_schedule_update(*, raw_schedule: list[Any], patch: Mapping[str, Any]) -> str:
    from .rpc_parsers import _REMY_TRIGGER_FIELDS, _REMY_WRITABLE_FIELDS, parse_remy_schedule_object
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    parsed = parse_remy_schedule_object(raw_schedule)
    if not parsed.ok or not parsed.value['editable']:
        raise ValueError('Schedule is not safely editable')
    allowed = {'title', 'instructions', 'timezone', 'starts_at', 'ends_at', 'status', 'trigger'}
    identity = {'schedule_id', 'kind', 'conversation_id', 'project_id', 'revision', 'editable'}
    if not isinstance(patch, Mapping) or not set(patch) <= allowed | identity:
        raise ValueError('Unsupported patch')
    if any(name in patch and patch[name] != parsed.value[name] for name in identity):
        raise ValueError('Schedule identity or revision changed')
    fields = {n: raw_schedule[n - 1] for n in _REMY_WRITABLE_FIELDS if n <= len(raw_schedule) and raw_schedule[n - 1] is not None}
    for name, number in (('title', 2), ('instructions', 8), ('timezone', 7)):
        if name in patch:
            value = patch[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Invalid string patch')
            if name == 'timezone':
                try:
                    ZoneInfo(value)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError('Invalid timezone') from exc
            fields[number] = value
    if 'status' in patch:
        if type(patch['status']) is not int or patch['status'] not in (1, 2):
            raise ValueError('Invalid status')
        fields[10] = patch['status']
    for name, number in (('starts_at', 14), ('ends_at', 15)):
        if name in patch:
            if patch[name] is None:
                fields.pop(number, None)
            else:
                fields[number] = _remy_encode_timestamp(patch[name])
    if 'trigger' in patch:
        for number in _REMY_TRIGGER_FIELDS:
            fields.pop(number, None)
        number, value = _remy_encode_trigger(patch['trigger'])
        fields[number] = value
    return json.dumps([_remy_proto(fields)], ensure_ascii=False, separators=(',', ':'))


def _managed_skill_get_payload(*, skill_id: str) -> str:
    from .managed_skills import get_payload
    return get_payload(skill_id=skill_id)


def _managed_skill_save_payload(*, skill_id: str | None, upload_handle: str, mode: int, enabled: bool) -> str:
    if (type(mode) is not int or mode not in (2, 3) or type(enabled) is not bool
            or not isinstance(upload_handle, str) or not upload_handle.strip() or len(upload_handle) > 65536
            or (mode == 3 and skill_id is not None)):
        raise ValueError("Invalid native Skill save request.")
    if mode == 2:
        _managed_skill_get_payload(skill_id=skill_id or "")
    return _compact([upload_handle, skill_id, mode, enabled])


def _managed_skill_enable_payload(*, skill_id: str, enabled: bool) -> str:
    _managed_skill_get_payload(skill_id=skill_id)
    if type(enabled) is not bool:
        raise ValueError("Invalid Skill enabled state.")
    return _compact([skill_id, enabled])


def _custom_gems_payload(*, locale: str = "zh-CN") -> str:
    if not isinstance(locale, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", locale):
        raise ValueError("Invalid locale")
    return json.dumps([2, [locale], 0], separators=(",", ":"))


def _notebook_admin_create_payload(**arguments: Any) -> str:
    from .notebook_admin import create_payload
    return create_payload(**arguments)


def _notebook_admin_get_payload(**arguments: Any) -> str:
    from .notebook_admin import get_payload
    return get_payload(**arguments)


def _notebook_admin_source_create_payload(**arguments: Any) -> str:
    from .notebook_admin import source_create_payload
    return source_create_payload(**arguments)


def _notebook_admin_source_url_create_payload(**arguments: Any) -> str:
    from .notebook_admin import source_url_create_payload
    return source_url_create_payload(**arguments)


def _notebook_admin_source_file_create_payload(**arguments: Any) -> str:
    from .notebook_admin import source_file_create_payload
    return source_file_create_payload(**arguments)


def _notebook_admin_source_delete_payload(**arguments: Any) -> str:
    from .notebook_admin import source_delete_payload
    return source_delete_payload(**arguments)


_CONTRACT_LIST = [
    _contract("gems.custom_list", "gems", "custom_list", "CNgdBe", "/app",
              _custom_gems_payload, "custom_gems",
              "gemini-webapi 2.1.1 GemMixin.fetch_gems custom LIST_BOTS request"),
    _contract("notebook_admin.create", "notebook_admin", "create", "oMH3Zd", "/app",
              _notebook_admin_create_payload, "notebook_admin_created",
              "2026-11-01 public Byd/CreateBot personal Notebook metadata, exact returned project ID",
              mode="mutation", verification_strategy="independent_exact_notebook_readback"),
    _contract("notebook_admin.get", "notebook_admin", "get", "HcT8bb", "/app",
              _notebook_admin_get_payload, "notebook_admin_project",
              "2026-11-01 public GetBot and controlled synthetic project/source-map read"),
    _contract("notebook_admin.delete", "notebook_admin", "delete_and_conversations", "Nwkn9", "/app",
              _notebook_admin_get_payload, "mutation_ack",
              "2026-11-01 native Notebook UI DeleteBotAndConversations, exact observed project ID",
              mode="mutation", verification_strategy="independent_exact_notebook_absence"),
    _contract("notebook_admin.source_create", "notebook_admin", "add_text_source", "ko3zcd", "/app",
              _notebook_admin_source_create_payload, "mutation_ack",
              "2026-11-01 public CJ.E3/Bsf notebook text source oneof and partner surface [1,3]",
              mode="mutation", verification_strategy="independent_exact_source_readback"),
    _contract("notebook_admin.source_create_url", "notebook_admin", "add_url_source", "ko3zcd", "/app",
              _notebook_admin_source_url_create_payload, "notebook_admin_source_created",
              "2026-11-06 public lbYGFc bYe/OI.e5 URL source oneof field 4 and exact created source name",
              mode="mutation", stability="preview", verification_strategy="independent_exact_url_source_readback"),
    _contract("notebook_admin.source_create_file", "notebook_admin", "add_file_source", "ko3zcd", "/app",
              _notebook_admin_source_file_create_payload, "notebook_admin_source_created",
              "2026-10-18 indexed file source; 2026-11-06 public lbYGFc bYe/OI.e5 upload oneof field 5 and assigned name",
              mode="mutation", stability="preview", verification_strategy="independent_exact_file_source_readback"),
    _contract("notebook_admin.source_delete", "notebook_admin", "delete_source", "AptDmf", "/app",
              _notebook_admin_source_delete_payload, "mutation_ack",
              "2026-11-01 public native source deletion with exact observed resource key and partner surface [1,3]",
              mode="mutation", verification_strategy="independent_exact_source_absence"),
    _contract("managed_skills.list", "managed_skills", "list", "cCSu7", "/app",
              _fixed([]), "managed_skills_list",
              "2026-11-01 public Web ListManagedSkills request empty and response field 1 Skill rows"),
    _contract("managed_skills.get", "managed_skills", "get", "pvBRnd", "/app",
              _managed_skill_get_payload, "managed_skills_get",
              "2026-11-01 public Web GetManagedSkill request exact Skill ID field 1"),
    _contract("managed_skills.save", "managed_skills", "save", "SMuXte", "/app",
              _managed_skill_save_payload, "managed_skills_saved",
              "2026-11-01 lNX4ad SHA256 63560704 editor saves ZIP upload handle with mode 3 create or 2 update",
              mode="mutation", verification_strategy="independent_exact_skill_readback"),
    _contract("managed_skills.enable", "managed_skills", "enable", "pdelZc", "/app",
              _managed_skill_enable_payload, "mutation_ack",
              "2026-11-01 public core lyd sets exact Skill ID and enabled bool",
              mode="mutation", verification_strategy="independent_exact_skill_readback"),
    _contract("managed_skills.delete", "managed_skills", "delete", "SL49ud", "/app",
              _managed_skill_get_payload, "mutation_ack",
              "2026-11-01 public Web DeleteManagedSkill exact Skill ID field 1",
              mode="mutation", verification_strategy="independent_complete_skill_list_absence"),
    _contract("preferences.list", "preferences", "list_permissions", "eCfoKd", "/app",
              _preference_list_payload, "preference_list",
              "2026-11-01 public UcF00e ListPermissions scope wire 2; no pagination guarantee"),
    _contract("preferences.update", "preferences", "update_permission", "UKqEL", "/app",
              _preference_update_payload, "preference_update",
              "2026-11-01 public UcF00e Vdn and mRNCI MWb preserve native PermissionId",
              mode="mutation", verification_strategy="independent_exact_permission_read_back"),
    _contract("preferences.catalog", "preferences", "permission_action_catalog", "uPDUsc", "/app",
              _preference_catalog_payload, "preference_catalog",
              "2026-11-01 public qT.l0 fresh Web consent metadata; app field18 children and field23 permissions"),
    _contract("preferences.revoke", "preferences", "revoke_exact_global_permission", "kQnpyd", "/app",
              _preference_revoke_payload, "preference_revoke",
              "2026-11-01 public core ES.Aa RevokePermission: granted ID and wire scope 2; opaque ack only",
              mode="mutation", verification_strategy="independent_exact_grant_absence"),
    _contract("tasks.browser_credentials", "tasks", "browser_ice_credentials", "AUWn6c", "/app", _fixed([]),
              "task_browser_credentials", "Public fc09ca111cd6fb79 GetWebRtcCredentials; synthetic RFB connection"),
    _contract("tasks.browser_offer", "tasks", "browser_sdp_offer", "G1wmmd", "/app", _browser_offer_payload,
              "task_browser_answer", "Public SendWebRtcSdpOffer and synthetic data-channel RFB handshake",
              mode="mutation", verification_strategy="rfb_handshake"),
    _contract("tasks.browser_ice", "tasks", "browser_ice_candidates", "YjPFC", "/app", _browser_ice_payload,
              "mutation_ack", "Public AddWebRtcIceCandidate and synthetic TCP TURN RFB connection",
              mode="mutation", verification_strategy="rfb_handshake"),
    _contract("tasks.schedule_refs", "tasks", "associated_schedules", "mXV10b", "/app", _task_id_payload,
              "task_schedule_refs", "Public GetRemyGoal response field2 related schedules; exact Task-bound cleanup guard"),
    _contract("tasks.progress_turn", "tasks", "exact_turn_activity", "EqPOKe", "/app", _single_turn_payload,
              "task_progress_turn", "Public GetConversationTurn field1 turn; synthetic exact-turn readback"),
    _contract("tasks.progress_latest", "tasks", "latest_turn_activity", "hNvQHb", "/app", _research_chat_plan_payload,
              "task_progress_turn_list", "Public ListConversationTurns and synthetic Task activity field38"),
    _contract("task_schedules.list", "task_schedules", "list", "Ey90Ac", "/scheduled", _fixed([]), "remy_schedule_list", "Public ListRemySchedules"),
    _contract("task_schedules.read", "task_schedules", "read", "Mfr55e", "/scheduled", _remy_id_payload, "remy_schedule_reply", "Public ReadRemySchedule and synthetic readback"),
    _contract("task_schedules.create", "task_schedules", "create", "mDJkAd", "/scheduled", build_remy_schedule_create, "remy_schedule_reply", "Public CreateRemySchedule", mode="mutation", verification_strategy="read_back"),
    _contract("task_schedules.update", "task_schedules", "update", "xrJXie", "/scheduled", build_remy_schedule_update, "remy_schedule_reply", "Public UpdateRemySchedule", mode="mutation", verification_strategy="read_back"),
    _contract("task_schedules.delete", "task_schedules", "delete", "CK9lTc", "/scheduled", _remy_id_payload, "mutation_ack", "Public DeleteRemySchedule", mode="mutation", verification_strategy="read_back"),
    _contract(
        "web_workflow.turn", "workflow", "exact_completed_reply", "hNvQHb", "/app",
        _research_chat_plan_payload, "workflow_turn",
        "2026-11-01 observed ReadChat exact RID/candidate and public candidate completion state",
    ),
    _contract(
        "notebooklm.list", "notebooklm", "notebook_source_picker", "NXpLKc", "/app", _fixed([]),
        "notebooklm_list", "2026-11-01 public HwBxOc ListNotebooks schema and controlled Web picker request",
    ),
    _contract(
        "tasks.get", "tasks", "task_goal_get", "mXV10b", "/app", _task_id_payload, "task_goal",
        "2026-11-01 public GetRemyGoal builder and independent synthetic Task read-back",
    ),
    _contract(
        "tasks.list", "tasks", "task_goal_list", "GS7W1", "/app", _task_page_payload, "task_goal_page",
        "Public ListRemyGoals metadata and authenticated 2026-11-06 empty final cursor",
    ),
    _contract(
        "tasks.update_metadata", "tasks", "task_goal_update_metadata", "Dxg8ke", "/app",
        _task_metadata_update_payload, "task_goal_update",
        "2026-11-06 public Web UpdateRemyGoal: goal fields 2/14/20, one description or pinned field mask",
        mode="mutation", verification_strategy="independent_exact_task_metadata_read_back",
    ),
    _contract(
        "tasks.delete", "tasks", "task_goal_delete", "w4yNDb", "/app", _task_id_payload, "mutation_ack",
        "2026-11-01 public DeleteRemyGoal builder; fresh GetRemyGoal required for verification",
        mode="mutation", verification_strategy="read_back_exact_task_absence",
    ),
    _contract(
        "research.chat_progress", "research", "research_chat_progress", "hNvQHb", "/app",
        _research_chat_payload, "research_chat_progress",
        "2026-10-31 synthetic research chat readback and gemini-webapi research document parser",
        stability="experimental",
    ),
    _contract(
        "research.chat_plan", "research", "research_chat_plan", "hNvQHb", "/app",
        _research_chat_plan_payload, "research_chat_plan",
        "2026-10-31 research plan from selected completed model turn in chat readback",
        stability="experimental",
    ),
    _contract(
        "media.music_chat",
        "media",
        "music_chat_readback",
        "hNvQHb",
        "/app",
        _music_chat_payload,
        "music_chat",
        "2026-09-26 gemini-webapi READ_CHAT response and maintainer music recovery",
        stability="preview",
    ),
    _contract(
        "history.recent",
        "remy",
        "conversation_history_recent",
        "MaZiqc",
        "/app",
        _fixed([13, None, [False, None, True]]),
        "conversation_page",
        "2026-07-04 Pro UI / Conversation history recent bucket",
        stability="preview",
    ),
    _contract(
        "history.pinned",
        "history",
        "conversation_history_pinned",
        "MaZiqc",
        "/app",
        _fixed([13, None, [True, None, True]]),
        "conversation_page",
        "2026-07-04 Pro UI / Conversation history pinned bucket",
        stability="preview",
    ),
    _contract(
        "history.page",
        "history",
        "conversation_history_page",
        "MaZiqc",
        "/app",
        _conversation_payload,
        "conversation_page",
        "2026-07-04 Pro UI / paginated conversation metadata",
        stability="preview",
    ),
    _contract(
        "history.metadata_page",
        "history",
        "chat_control_metadata_page",
        "MaZiqc",
        "/app",
        _conversation_payload,
        "chat_metadata_page",
        "2026-11-01 public ListConversations metadata; preserve unknown title and pin fields",
        stability="preview",
    ),
    _contract(
        "history.exact", "history", "conversation_presence", "hNvQHb", "/app",
        _research_chat_plan_payload, "conversation_presence",
        "2026-11-06 authenticated ListConversationTurns exact identity and bodyless NOT_FOUND read-back",
    ),
    _contract(
        "history.rename",
        "history",
        "rename_chat",
        "MUAZcd",
        "/app",
        _rename_chat_payload,
        "conversation_update",
        "2026-11-01 public UpdateConversation builder: title field mask, conversation fields 1/2",
        mode="mutation",
        stability="preview",
        verification_strategy="fresh_chat_metadata_readback",
    ),
    _contract(
        "history.pin",
        "history",
        "set_chat_pinned",
        "MUAZcd",
        "/app",
        _pin_chat_payload,
        "conversation_update",
        "2026-11-01 public UpdateConversation builder: pinned field mask, conversation fields 1/3",
        mode="mutation",
        stability="preview",
        verification_strategy="fresh_chat_metadata_readback",
    ),
    _contract(
        "history.remy_goals",
        "history",
        "remy_goals",
        "GS7W1",
        "/app",
        _remy_goals_payload,
        "remy_goals_page",
        "2026-07-04 Pro UI / Remy goals with conversation references",
    ),
    _contract(
        "library.index",
        "library",
        "library_index",
        "sJBwce",
        "/app/library",
        _fixed([[1, 2]]),
        "opaque",
        "2026-06-18 Pro UI / Library",
    ),
    _contract(
        "library.assets",
        "library",
        "library_assets",
        "VxUbXb",
        "/app/library",
        _fixed([]),
        "opaque",
        "2026-06-18 Pro UI / Library",
    ),
    _contract(
        "library.locale_capabilities",
        "library",
        "library_locale_capabilities",
        "cYRIkd",
        "/app/library",
        _fixed(["zh-CN"]),
        "library_capabilities",
        "2026-06-18 Pro UI / Library",
    ),
    _contract(
        "notebooks.list",
        "notebooks",
        "native_notebooks_list",
        "CNgdBe",
        "/notebooks/view",
        _native_notebooks_payload,
        "notebook_list",
        "2026-07-04 Pro UI / Native Gemini Notebooks",
        stability="preview",
    ),
    _contract(
        "notebooks.chats",
        "notebooks",
        "native_notebook_chats",
        "MaZiqc",
        "/notebook/{notebook_slug}",
        _notebook_chats_payload,
        "notebook_conversation_page",
        "2026-07-04 Native Notebook recent chats; 2026-11-06 verified empty project page",
        stability="preview",
    ),
    _contract(
        "notebooks.move_chat",
        "notebooks",
        "move_chat_to_notebook",
        "MUAZcd",
        "/app",
        _move_chat_payload,
        "notebook_move",
        "2026-07-04 Pro UI / UpdateConversation notebook assignment",
        mode="mutation",
        stability="preview",
        verification_strategy="read_back_notebook_chats",
    ),
    _contract(
        "sharing.public_links",
        "sharing",
        "public_links_index",
        "K4WWud",
        "/app/sharing",
        _fixed([[1], ["zh-CN"]]),
        "public_links",
        "2026-06-18 Pro UI / Your public links",
    ),
    _contract(
        "sharing.state",
        "sharing",
        "sharing_state",
        "GPRiHf",
        "/app/sharing",
        _fixed([]),
        "opaque",
        "2026-06-18 Pro UI / Your public links",
    ),
    _contract(
        "sharing.preferences",
        "sharing",
        "sharing_preferences",
        "maGuAc",
        "/app/sharing",
        _fixed([1]),
        "opaque",
        "2026-06-18 Pro UI / Your public links",
    ),
    _contract(
        "usage.quota",
        "usage",
        "usage_quota",
        "qpEbW",
        "/app/usage",
        _fixed([[[1, 11], [2, 11], [6, 11]]]),
        "usage_entries",
        "2026-06-18 Pro UI / Usage limits",
    ),
    _contract(
        "usage.model_state",
        "usage",
        "usage_model_state",
        "qpEbW",
        "/app/usage",
        _fixed([[[1, 4], [6, 6], [1, 15]]]),
        "usage_entries",
        "2026-06-18 Pro UI / Usage limits",
    ),
    _contract(
        "personalization.state",
        "personalization",
        "personalization_state",
        "GPRiHf",
        "/app/personalization-settings",
        _fixed([]),
        "opaque",
        "2026-06-18 Pro UI / Personalization settings",
    ),
    _contract(
        "personalization.preferences",
        "personalization",
        "personalization_preferences",
        "maGuAc",
        "/app/personalization-settings",
        _fixed([1]),
        "opaque",
        "2026-06-18 Pro UI / Personalization settings",
    ),
    _contract(
        "personalization.labels",
        "personalization",
        "personalization_labels",
        "Te6DCf",
        "/app/personalization-settings",
        _fixed([["zh-CN"], [1]]),
        "opaque",
        "2026-06-18 Pro UI / Personalization settings",
    ),
    _contract(
        "import.memory_state",
        "import",
        "memory_import_state",
        "Te6DCf",
        "/app/import",
        _fixed([["zh-CN"], [1]]),
        "opaque",
        "2026-06-18 Pro UI / Memory import",
    ),
    _contract(
        "scheduled.registry",
        "scheduled",
        "scheduled_actions_registry",
        "XPSWpd",
        "/scheduled",
        _fixed([]),
        "scheduled_registry",
        "2026-06-19 Pro UI / Scheduled actions registry",
        stability="preview",
    ),
    _contract(
        "scheduled.state",
        "scheduled",
        "scheduled_actions_state",
        "otAQ7b",
        "/scheduled",
        _fixed([]),
        "opaque",
        "2026-06-18 Pro UI / Scheduled actions",
    ),
    _contract(
        "scheduled.active",
        "scheduled",
        "scheduled_actions_active",
        "MaZiqc",
        "/scheduled",
        _fixed([13, None, [1, None, 1]]),
        "opaque",
        "2026-06-18 Pro UI / Scheduled actions",
    ),
    _contract(
        "scheduled.inactive",
        "scheduled",
        "scheduled_actions_inactive",
        "MaZiqc",
        "/scheduled",
        _fixed([13, None, [0, None, 1]]),
        "opaque",
        "2026-06-18 Pro UI / Scheduled actions",
    ),
    _contract(
        "scheduled.get",
        "scheduled",
        "scheduled_action_get",
        "kwDCne",
        "/scheduled",
        _scheduled_get_payload,
        "scheduled_get",
        "2026-06-20 Pro UI / Scheduled action get-by-id",
        stability="preview",
    ),
    _contract(
        "scheduled.create_daily",
        "scheduled",
        "scheduled_action_create_daily",
        "Jba3ib",
        "/scheduled",
        _scheduled_daily_payload,
        "scheduled_create",
        "2026-06-20 Pro UI / Scheduled action daily create",
        mode="mutation",
        stability="preview",
        verification_strategy="read_back_registry_and_get_by_id",
    ),
    _contract(
        "scheduled.delete",
        "scheduled",
        "scheduled_action_delete",
        "Q4Gw3c",
        "/scheduled",
        _scheduled_delete_payload,
        "mutation_ack",
        "2026-06-20 Pro UI / Scheduled action delete",
        mode="mutation",
        stability="preview",
        verification_strategy="read_back_registry_and_get_by_id",
    ),
    _contract(
        "tool_modes.status",
        "tool_modes",
        "tool_mode_status",
        "MyzX6c",
        "/app",
        _fixed([]),
        "tool_modes",
        "2026-06-19 Pro UI / Canvas and Guided Learning tool mode toggles",
    ),
    _contract(
    "history.update",
    "history",
    "update_conversation_metadata",
    "MUAZcd",
    "/app",
    _update_chat_payload,
    "conversation_update",
    "2026-10-18 signed-in UI / pin and rename; BardChatUi D3Wg_SjWYeA.2018.O VQd/Zia",
    mode="mutation",
    stability="preview",
    verification_strategy="independent_history_metadata_read_back",
    ),
    _contract(
    "gems.custom_registry",
    "gems",
    "custom_gems_registry",
    "CNgdBe",
    "/app",
    _custom_gems_payload,
    "custom_gems",
    "gemini-webapi 2.0.0 and 2.1.1 GeminiClient.fetch_gems source / custom identifier",
    stability="preview",
    verification_strategy="complete_custom_registry",
    ),
    _contract(
    "notebooks.get", "notebooks", "native_notebook_get", "HcT8bb", "/notebooks/view",
    _notebook_get_payload, "notebook_get",
    "2026-10-18 public main:2610-2621 GetBot / nK.s7; lbYGFc:6916 czh",
    stability="preview",
    ),
    _contract(
    "notebooks.create", "notebooks", "native_notebook_create", "oMH3Zd", "/notebooks/create",
    _notebook_metadata_payload, "notebook_created",
    "2026-10-18 public main:2596-2613 Vjd/CreateBot; ScPWJf:6126,6174,6247 Notebook caller",
    mode="mutation", stability="preview", verification_strategy="read_back_notebook_get",
    ),
    _contract(
    "notebooks.update", "notebooks", "native_notebook_update", "kHv0Vd", "/notebooks/view",
    _notebook_metadata_payload, "notebook_update_ack",
    "2026-10-18 public main:2596 Vjd; lbYGFc:6914 nK.G3b UpdateBotMetadata",
    mode="mutation", stability="preview", verification_strategy="read_back_notebook_get",
    ),
    _contract(
    "notebooks.delete", "notebooks", "native_notebook_delete", "UXcSJb", "/notebooks/view",
    _notebook_id_payload, "native_void_ack",
    "2026-10-18 public main:2609,2620 nK.N2 DeleteBot",
    mode="mutation", stability="preview", verification_strategy="read_back_notebook_list_absence",
    ),
    _contract(
    "notebooks.add_source", "notebooks", "native_notebook_add_source", "ko3zcd", "/notebooks/view",
    _notebook_source_payload, "notebook_source_created",
    "2026-10-18 public lbYGFc:6900 jyh; 6911-6913 nK.E2 CreateNotebookSource context [1,3]",
    mode="mutation", stability="preview", verification_strategy="read_back_notebook_source",
    ),
    _contract(
    "native_skills.list", "native_skills", "native_skills_list", "cCSu7", "/customize/skills",
    _fixed([]), "native_skill_list", "2026-10-18 public main:2138,2149 N6d ListManagedSkills / tz:1645-1647",
    stability="preview",
    ),
    _contract(
    "native_skills.get", "native_skills", "native_skill_get", "pvBRnd", "/customize/skills",
    _native_skill_id_payload, "native_skill_get", "2026-10-18 public main:2138 getSkill / GetManagedSkill",
    stability="preview",
    ),
    _contract(
    "native_skills.save", "native_skills", "native_skill_save", "SMuXte", "/customize/skills",
    _native_skill_save_payload, "native_skill_save",
    "2026-10-18 public main:6755-6758 GSa; 6769-6772 manual ZIP create/update modes 3/2",
    mode="mutation", stability="preview", verification_strategy="read_back_native_skill_get",
    ),
    _contract(
    "scheduled.remy_registry",
    "scheduled",
    "remy_schedules_registry",
    "Ey90Ac",
    "/scheduled",
    _fixed([]),
    "remy_schedule_registry",
    "2026-10-18 public BardChatUi / x9QgSe scheduled-tasks-tab -> Load schedules -> ZS.L6d",
    stability="preview",
    ),
    _contract(
    "scheduled.remy_get",
    "scheduled",
    "remy_schedule_get",
    "Mfr55e",
    "/scheduled",
    _scheduled_get_payload,
    "remy_schedule_get",
    "2026-10-18 public BardChatUi / x9QgSe ZS.I6c and ReadRemySchedule field 1",
    stability="preview",
    ),
    _contract(
    "scheduled.remy_delete", "scheduled", "delete_remy_schedule", "CK9lTc", "/scheduled",
    _scheduled_get_payload, "remy_void_ack",
    "2026-10-18 public BardChatUi / ZS.Qpd DeleteRemySchedule request field 1 id, empty response message",
    mode="mutation", stability="preview", verification_strategy="independent_complete_remy_registry_absence",
    ),
    _contract("library.page", "library", "library_artifact_page", "jGArJ", "/library",
    _library_page_payload, "library_page",
    "2026-10-18 public QAfPpb:5275,5331-5339 MyStuffApi.m0a and aFf", stability="preview"),
    _contract("library.delete", "library", "delete_library_artifact", "PGX16d", "/library",
    _library_delete_payload, "library_delete",
    "2026-10-18 public QAfPpb:5274,5331,5337-5338 MyStuffApi.Jbb and YEf",
    mode="mutation", stability="preview", verification_strategy="complete_library_read_back"),
    _contract("sharing.create", "sharing", "create_public_link", "fuVx7", "/app",
    _public_link_create_payload, "public_link_create",
    "2026-10-18 public sharing:5424-5436 TN.Lka public destination 1",
    mode="mutation", stability="preview", verification_strategy="exact_token_list_read_back"),
    _contract("sharing.delete", "sharing", "delete_public_link", "SgORbf", "/sharing",
    _public_link_delete_payload, "sharing_void_ack",
    "2026-10-18 public sharing:5619-5621 WKm and TN.XVa, token field2 only",
    mode="mutation", stability="preview", verification_strategy="exact_token_list_read_back"),
    _contract("canvas.get", "canvas", "get_artifact_version", "vCj0tc", "/app",
    _canvas_get_payload, "canvas_artifact",
    "2026-10-18 public xmJ3d:4279,4482 GetArtifactVersion GXa and II", stability="preview"),
    _contract("canvas.versions", "canvas", "list_artifact_versions", "VNbbrc", "/app",
    _canvas_get_payload, "canvas_versions",
    "2026-10-18 public x9QgSe:4917-4919 Wcc P_d field1 and response field1 repeated ez",
    stability="preview"),
    _contract("canvas.chat_artifacts", "canvas", "chat_artifact_versions", "hNvQHb", "/app",
    _canvas_chat_payload, "canvas_chat_artifacts",
    "2026-10-18 QAfPpb:3327 draft field31 ez; x9:4379-4380 response f2 and4480 request f3 continuation",
    stability="preview"),
]


RPC_CONTRACTS: Mapping[str, RPCContract] = MappingProxyType({contract.key: contract for contract in _CONTRACT_LIST})

# These are the read-only probes exposed by the existing account/capability
# tools.  Mutation and parameterized contracts stay registry-only.
WEB_FEATURE_PROBE_KEYS = (
    "history.recent",
    "history.pinned",
    "history.remy_goals",
    "library.index",
    "library.assets",
    "library.locale_capabilities",
    "notebooks.list",
    "sharing.public_links",
    "sharing.state",
    "sharing.preferences",
    "usage.quota",
    "usage.model_state",
    "personalization.state",
    "personalization.preferences",
    "personalization.labels",
    "import.memory_state",
    "scheduled.registry",
    "scheduled.state",
    "scheduled.active",
    "scheduled.inactive",
    "tool_modes.status",
)
WEB_FEATURE_PROBE_CONTRACTS: tuple[RPCContract, ...] = tuple(
    RPC_CONTRACTS[key] for key in WEB_FEATURE_PROBE_KEYS
)
WEB_FEATURE_PROBES: tuple[dict[str, str], ...] = tuple(
    contract.as_probe() for contract in WEB_FEATURE_PROBE_CONTRACTS
)


def get_contract(key: str) -> RPCContract:
    try:
        return RPC_CONTRACTS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown Gemini Web RPC contract: {key}") from exc


def get_probe(surface: str, name: str) -> dict[str, str]:
    for probe in WEB_FEATURE_PROBES:
        if probe["surface"] == surface and probe["name"] == name:
            return dict(probe)
    raise KeyError(f"Unknown Gemini Web probe: {surface}.{name}")


async def execute_contract(
    client: Any,
    contract_key: str,
    *,
    source_path: str | None = None,
    retry_count: int | None = None,
    **payload_arguments: Any,
) -> Any:
    contract = get_contract(contract_key)
    resolved_path = source_path or contract.source_path
    options: dict[str, Any] = {"source_path": resolved_path, "close_on_error": False}
    if retry_count is not None:
        options["current_retry"] = retry_count
    return await client._batch_execute(
        [RawRPCData(contract.rpc_id, contract.build_payload(**payload_arguments))],
        **options,
    )
