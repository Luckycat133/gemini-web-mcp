"""Central registry for observed Gemini Web RPC contracts.

The Web RPC layer is deliberately kept below MCP handlers.  A contract owns the
RPC id, source path, payload builder, parser name, and the evidence describing
where it was observed.  Services refer to a stable contract key rather than
embedding private RPC ids or payload shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping


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
        "conversation_page",
        "2026-07-04 Pro UI / Native Gemini Notebook recent chats",
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
]


RPC_CONTRACTS: Mapping[str, RPCContract] = MappingProxyType({contract.key: contract for contract in _CONTRACT_LIST})

# These are the read-only probes exposed by the existing account/capability
# tools.  Mutation and parameterized contracts stay registry-only.
_PROBE_KEYS = (
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
WEB_FEATURE_PROBES: tuple[dict[str, str], ...] = tuple(RPC_CONTRACTS[key].as_probe() for key in _PROBE_KEYS)


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
    return await client._batch_execute(
        [RawRPCData(contract.rpc_id, contract.build_payload(**payload_arguments))],
        source_path=resolved_path,
        close_on_error=False,
    )
