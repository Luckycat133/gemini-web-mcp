"""Central registry for observed Gemini Web RPC contracts.

The Web RPC layer is deliberately kept below MCP handlers.  A contract owns the
RPC id, source path, payload builder, parser name, and the evidence describing
where it was observed.  Services refer to a stable contract key rather than
embedding private RPC ids or payload shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping
import unicodedata
from urllib.parse import urlsplit


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


def _remy_goals_payload(*, page_size: int = 13, next_page_token: str | None = None) -> str:
    return _compact([page_size, next_page_token] if next_page_token else [page_size])


def _native_notebooks_payload(*, locale: str = "zh-CN") -> str:
    return _compact([2, [locale or "zh-CN"], False, None, [2]])


def native_notebook_metadata(*, title: str, description: str, instructions: str, subtype: int) -> list[Any]:
    """ScPWJf xwh -> Vjd; business subtype 4 uses a different creation flow."""
    if type(subtype) is not int or subtype not in {1, 2, 3}:
        raise ValueError("Supported native Notebook subtype must be 1, 2 or 3.")
    metadata: list[Any] = [None] * 17
    metadata[0], metadata[1], metadata[2] = title, description, instructions
    metadata[4], metadata[7], metadata[9], metadata[10], metadata[11] = [], [], False, 1, []
    metadata[16] = [2, None, None, None, subtype, []]
    return metadata


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


def _notebook_get_payload(*, notebook_id: str, locale: str = "zh-CN") -> str:
    return _compact([notebook_id, [locale or "zh-CN"], False])


def _notebook_metadata_payload(*, metadata: list[Any], notebook_id: str | None = None) -> str:
    return _compact([metadata] if notebook_id is None else [notebook_id, metadata])


def _notebook_id_payload(*, notebook_id: str) -> str:
    return _compact([notebook_id])


def _notebook_source_payload(*, notebook_id: str, source: list[Any]) -> str:
    return _compact([notebook_id, source, [1, 3]])


def _notebook_source_delete_payload(*, source_name: str) -> str:
    return _compact([source_name, [1, 3]])


def _native_skill_id_payload(*, skill_id: str) -> str:
    return _compact([skill_id])


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


def _native_skill_license_payload(*, skill_id: str, enabled: bool) -> str:
    if type(enabled) is not bool:
        raise ValueError("Native Skill enabled must be a boolean.")
    return _compact([skill_id, enabled])


# main:1578-1579/6625-6627 public feature defaults for Y4e agent_skills uploads.
NATIVE_SKILL_UPLOAD_URL = "https://content-push.googleapis.com/upload/"
NATIVE_SKILL_PUSH_ID = "feeds/or3baakvdgsg4i"
_NATIVE_SKILL_UPLOAD_HOSTS = {
    "content-push.googleapis.com", "contribution.usercontent.google.com", "push.clients6.google.com",
}


def native_skill_upload_url_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname in _NATIVE_SKILL_UPLOAD_HOSTS
                    and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        return False


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


def native_skill_name_valid(name: str) -> bool:
    """lNX4ad:5293/5296 J5m name rule, including non-Latin letters."""
    if not name or name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    if any(char != "-" and unicodedata.category(char) not in {"Ll", "Lo", "Nd", "Nl", "No", "Mn", "Mc", "Me"}
           for char in name):
        return False
    return len(name.encode("utf-16-le")) // 2 <= 64


def _custom_gems_payload(*, locale: str = "en") -> str:
    return _compact([2, [locale or "en"], 0])


def _system_gems_payload(*, locale: str = "en") -> str:
    return _compact([3, [locale or "en"], 0])


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


def _scheduled_get_payload(*, action_id: str) -> str:
    return _compact([action_id])


def _remy_schedule_write_payload(*, schedule: list[Any]) -> str:
    return _compact([schedule])


def _task_goal_payload(*, chat_id: str) -> str:
    return _compact([chat_id])


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


_CONTRACT_LIST = [
    _contract(
        "tasks.get",
        "tasks",
        "get_task_goal",
        "mXV10b",
        "/app",
        _task_goal_payload,
        "task_goal",
        "2026-10-18 public BardChatUi / GetRemyGoal OUd and GzXWE Tic conversationId",
        stability="preview",
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
        "2026-10-18 public main:2730 GetG1Interventions; library.index/name retained for compatibility, not a private Library asset index",
    ),
    _contract(
        "library.assets",
        "library",
        "library_assets",
        "VxUbXb",
        "/app/library",
        _fixed([]),
        "opaque",
        "2026-10-18 public main:3657 CheckModeFeatureQuota; library.assets/name retained for compatibility, not private Library assets",
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
        "gems.system_registry",
        "gems",
        "system_gems_registry",
        "CNgdBe",
        "/app",
        _system_gems_payload,
        "custom_gems",
        "gemini-webapi 2.0.0 and 2.1.1 GeminiClient.fetch_gems source / system identifier",
        stability="preview",
        verification_strategy="complete_system_registry",
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
        "conversation_page",
        "2026-07-04 Pro UI / Native Gemini Notebook recent chats",
        stability="preview",
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
        "notebooks.delete_with_chats", "notebooks", "native_notebook_delete_with_chats", "Nwkn9", "/notebooks/view",
        _notebook_id_payload, "native_void_ack",
        "2026-10-18 public main:2609; lbYGFc:6916-6918 dzh DeleteBotAndConversations",
        mode="mutation", stability="preview", verification_strategy="read_back_notebook_list_absence",
    ),
    _contract(
        "notebooks.add_source", "notebooks", "native_notebook_add_source", "ko3zcd", "/notebooks/view",
        _notebook_source_payload, "notebook_source_created",
        "2026-10-18 public lbYGFc:6900 jyh; 6911-6913 nK.E2 CreateNotebookSource context [1,3]",
        mode="mutation", stability="preview", verification_strategy="read_back_notebook_source",
    ),
    _contract(
        "notebooks.remove_source", "notebooks", "native_notebook_remove_source", "AptDmf", "/notebooks/view",
        _notebook_source_delete_payload, "native_void_ack",
        "2026-10-18 public lbYGFc:6911-6912 nK.ZVa DeleteNotebookSource context [1,3]",
        mode="mutation", stability="preview", verification_strategy="read_back_notebook_source_absence",
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
        "native_skills.set_license_enabled", "native_skills", "native_skill_set_license_enabled", "qlXjKc",
        "/customize/skills", _native_skill_license_payload, "native_skill_get",
        "2026-10-18 public main:2148 pvc UpdateSkillLicense; 6775 tlc Drive-owned enable caller",
        mode="mutation", stability="preview", verification_strategy="read_back_native_skill_get",
    ),
    _contract(
        "native_skills.delete", "native_skills", "native_skill_delete", "SL49ud", "/customize/skills",
        _native_skill_id_payload, "native_void_ack", "2026-10-18 public main:2138 HW DeleteManagedSkill",
        mode="mutation", stability="preview", verification_strategy="read_back_native_skill_list_absence",
    ),
    _contract(
        "native_skills.remove_license", "native_skills", "native_skill_remove_license", "NwCpae", "/customize/skills",
        _native_skill_id_payload, "native_void_ack", "2026-10-18 public main:2138; 6771 NwCpae license removal caller",
        mode="mutation", stability="preview", verification_strategy="read_back_native_skill_list_absence",
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
        "OXxBcc",
        "/sharing",
        _fixed([]),
        "public_links",
        "2026-10-18 public xaI2P / ListSharedConversations and CAf metadata mapping",
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
        "scheduled.remy_create", "scheduled", "create_remy_schedule", "mDJkAd", "/scheduled",
        _remy_schedule_write_payload, "remy_schedule_get",
        "2026-10-18 public BardChatUi / ZS.Ppd wraps O1e(fT) in request field 1; response field 1",
        mode="mutation", stability="preview", verification_strategy="independent_remy_schedule_read_back",
    ),
    _contract(
        "scheduled.remy_update", "scheduled", "update_remy_schedule", "xrJXie", "/scheduled",
        _remy_schedule_write_payload, "remy_schedule_get",
        "2026-10-18 public BardChatUi / ZS.ovc wraps O1e(fT); toggleScheduleStatusObs uses status 1/2",
        mode="mutation", stability="preview", verification_strategy="independent_remy_schedule_read_back",
    ),
    _contract(
        "scheduled.remy_delete", "scheduled", "delete_remy_schedule", "CK9lTc", "/scheduled",
        _scheduled_get_payload, "remy_void_ack",
        "2026-10-18 public BardChatUi / ZS.Qpd DeleteRemySchedule request field 1 id, empty response message",
        mode="mutation", stability="preview", verification_strategy="independent_complete_remy_registry_absence",
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
]


# Native Library, public sharing and Canvas contracts observed in the 2026-10-18
# BardChatUi bundle. These are separate from the older capability/quota probes.
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


def _library_delete_payload(*, target: list[Any]) -> str:
    return _compact([[target]])


def _public_link_create_payload(*, chat_id: str, response_id: str, title: str,
                                full_conversation: bool = True, locale: str = "zh-CN") -> str:
    # f10=1 requests a public link. Drive export and selected artifact expansion
    # have separate UI inputs and are deliberately absent from this contract.
    return _compact([None, chat_id, None, response_id, [full_conversation, title],
                     [locale], False, None, None, 1])


def _public_link_delete_payload(*, token: str) -> str:
    # Field 3 is the unrelated delete-all switch; never populate it.
    return _compact([None, token])


def _canvas_get_payload(*, artifact_id: str) -> str:
    return _compact([artifact_id])


def _canvas_update_payload(*, artifact_id: str, updated_ez: list[Any]) -> str:
    if not isinstance(updated_ez, list) or not updated_ez or updated_ez[0] != artifact_id:
        raise ValueError("Canvas update must retain the exact artifact identity.")
    return _compact([artifact_id, updated_ez])


def _canvas_chat_payload(*, chat_id: str, limit: int = 10, next_page_token: str | None = None) -> str:
    return _compact([chat_id, limit, next_page_token, 1, [1], [4], None, 1])


_CONTRACT_LIST.extend([
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
    _contract("canvas.update", "canvas", "update_current_artifact", "taAw7b", "/app",
              _canvas_update_payload, "native_void_ack",
              "2026-10-18 public x9QgSe:4280 cOd UpdateCurrentArtifact;4917 T_d;4918 qqc f1 artifactId/f2 DJc",
              mode="mutation", stability="preview", verification_strategy="exact_artifact_four_slot_read_back"),
    _contract("canvas.versions", "canvas", "list_artifact_versions", "VNbbrc", "/app",
              _canvas_get_payload, "canvas_versions",
              "2026-10-18 public x9QgSe:4917-4919 Wcc P_d field1 and response field1 repeated ez",
              stability="preview"),
    _contract("canvas.chat_artifacts", "canvas", "chat_artifact_versions", "hNvQHb", "/app",
              _canvas_chat_payload, "canvas_chat_artifacts",
              "2026-10-18 QAfPpb:3327 draft field31 ez; x9:4379-4380 response f2 and4480 request f3 continuation",
              stability="preview"),
])


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
    **payload_arguments: Any,
) -> Any:
    contract = get_contract(contract_key)
    resolved_path = source_path or contract.source_path
    batch_execute = client._batch_execute
    options: dict[str, Any] = {"source_path": resolved_path, "close_on_error": False}
    if contract.mode == "mutation":
        # Inspect the SDK's actual running wrapper, whose retry control is
        # hidden by functools.wraps. Bare clients must not receive an unknown
        # HTTP argument merely because their method accepts **kwargs.
        try:
            retry = inspect.signature(batch_execute, follow_wrapped=False).parameters.get("current_retry")
        except (TypeError, ValueError):
            retry = None
        if retry is not None and retry.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}:
            options["current_retry"] = 0
    return await batch_execute(
        [RawRPCData(contract.rpc_id, contract.build_payload(**payload_arguments))],
        **options,
    )
