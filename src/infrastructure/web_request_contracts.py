"""Evidence-backed Gemini Web generation selectors, independent of MCP tools.

2026-10-02 public frontend evidence from gemini.gstatic.com modules LQaXg and
xrScke, build BardChatUi.zh_CN.sOs2tZuLK_8.2018.O:
LQaXg SHA256 5d4f50b3a40b96f14efd7891866d1009d6f6f6553347de881fc46d1ced4e5c5b;
xrScke with dependencies SHA256
7d0447a85801b729091fad6fd1e889b0533f11152b6709b2496b677bb3868ffa.
Tool definitions use image featureMode 14,
video 11 and music 21. The video entry was re-fetched and hash-verified on
2026-10-03 (xrScke with dependencies, lines 7028-7030).
Selection flows through featureModeDataChange, Ry, Lm,
UpdateSelectedFeatureMode, Yg and Cce into request.jf. pRd passes that integer
through EOd into JSPB field 50 (the serialized array's index 49). This path
does not set the learning-mode companion fields 54/55 or select a model header.
These are static frontend contracts; account generation remains a separate
compatibility check.
"""

from types import MappingProxyType
from typing import Any, Final, Mapping

import orjson
from gemini_webapi.constants import MODEL_HEADER_KEY
from gemini_webapi.exceptions import GeminiError


FEATURE_MODE_INDEX: Final = 49
MEDIA_FEATURE_MODE_INDEX: Final = FEATURE_MODE_INDEX
NATIVE_MEDIA_MODE_IDS = MappingProxyType({"image": 14, "video": 11, "music": 21})
# 2026-10-18 official x9QgSe menu: Canvas=2, Guided Learning=24;
# main.js Ayd/byd forwards featureMode through JSPB field 50.
NATIVE_TOOL_MODE_IDS = MappingProxyType({"canvas": 2, "guided_learning": 24})
# 2026-10-18 main.js flag 45829880 and mwljBd's Task selection use 40;
# GzXWE's task sendRequest passes Cf=40 into JSPB field 50 and Rk=true
# into field 84 (main.js's Ayd request builder).
NATIVE_TASK_MODE_ID: Final = 40
TASK_REQUEST_FLAG_INDEX: Final = 83

# 2026-10-18 official main.js: EF/Eqd/Twd enumerate these values; PCb
# writes header fields 15/16 and the generation builder writes fields 80/81.
# SHA256: 5d9d08be834ebd7682e1f9c0859603710fc7a6bf16df3747f71fa7b8261e2e54.
THINKING_LEVEL_VALUES = MappingProxyType({"standard": 1, "extended": 2, "deep_think": 3, "medium": 4})
THINKING_MODE_INDEX: Final = 79
THINKING_LEVEL_INDEX: Final = 80
MODEL_HEADER_MODE_INDEX: Final = 14
MODEL_HEADER_LEVEL_INDEX: Final = 15


class NativeMediaRequestShapeError(GeminiError):
    """Fail before sending; bypass upstream's APIError wrapping and retries."""

    code = "UPSTREAM_CHANGED"


class ThinkingRequestShapeError(GeminiError):
    """Reject changed thinking request shapes before a provider submission."""

    code = "UPSTREAM_CHANGED"


class NativeTaskRequestShapeError(GeminiError):
    """Reject a changed native Task selector before any provider submission."""

    code = "UPSTREAM_CHANGED"


class NativeToolRequestShapeError(GeminiError):
    """Reject changed Canvas/Guided Learning selectors before submission."""

    code = "UPSTREAM_CHANGED"


def synchronize_thinking_headers(
    headers: Mapping[str, Any], *, mode_id: int | None, level_id: int,
) -> dict[str, Any]:
    """Copy a known model selector and preserve its identity/session fields."""
    if type(level_id) is not int or level_id not in THINKING_LEVEL_VALUES.values():
        raise ThinkingRequestShapeError("Thinking level is outside the observed request contract.")
    if mode_id is not None and (type(mode_id) is not int or mode_id <= 0):
        raise ThinkingRequestShapeError("Thinking model category is invalid.")
    patched = dict(headers)
    if MODEL_HEADER_KEY not in patched and mode_id is None:
        # An intentionally unspecified/default model has no selector header.
        return patched
    raw_header = patched.get(MODEL_HEADER_KEY)
    if not isinstance(raw_header, str):
        raise ThinkingRequestShapeError("Thinking request is missing its model selector header.")
    try:
        selector = orjson.loads(raw_header)
    except orjson.JSONDecodeError as error:
        raise ThinkingRequestShapeError("Thinking model selector is not valid JSON.") from error
    if (
        not isinstance(selector, list) or len(selector) <= MODEL_HEADER_MODE_INDEX
        or not isinstance(selector[4], str) or not selector[4]
        or type(selector[MODEL_HEADER_MODE_INDEX]) is not int
        or selector[MODEL_HEADER_MODE_INDEX] <= 0
    ):
        raise ThinkingRequestShapeError("Thinking model selector shape changed.")
    if len(selector) > MODEL_HEADER_LEVEL_INDEX and selector[MODEL_HEADER_LEVEL_INDEX] is not None and (
        type(selector[MODEL_HEADER_LEVEL_INDEX]) is not int
    ):
        raise ThinkingRequestShapeError("Thinking model selector level shape changed.")
    if len(selector) <= MODEL_HEADER_LEVEL_INDEX:
        selector.append(level_id)
    else:
        selector[MODEL_HEADER_LEVEL_INDEX] = level_id
    if mode_id is not None:
        selector[MODEL_HEADER_MODE_INDEX] = mode_id
    patched[MODEL_HEADER_KEY] = orjson.dumps(selector).decode("utf-8")
    return patched


def native_media_mode_id(mode: str) -> int:
    """Resolve only modes observed in the current public frontend chain."""
    try:
        return NATIVE_MEDIA_MODE_IDS[mode]
    except KeyError as error:
        raise ValueError("media_mode supports only image, video or music.") from error


def native_tool_mode_id(mode: str) -> int:
    """Resolve the native composer tools whose current request chain is known."""
    try:
        return NATIVE_TOOL_MODE_IDS[mode]
    except KeyError as error:
        raise ValueError("tool_mode supports only canvas or guided_learning.") from error
