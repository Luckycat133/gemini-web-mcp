"""Evidence-backed Gemini Web generation selectors, independent of MCP tools.

2026-10-02 public frontend evidence from gemini.gstatic.com modules LQaXg and
xrScke, build BardChatUi.zh_CN.sOs2tZuLK_8.2018.O:
LQaXg SHA256 5d4f50b3a40b96f14efd7891866d1009d6f6f6553347de881fc46d1ced4e5c5b;
xrScke with dependencies SHA256
7d0447a85801b729091fad6fd1e889b0533f11152b6709b2496b677bb3868ffa.
Tool definitions use image featureMode 14
and music 21. Selection flows through featureModeDataChange, Ry, Lm,
UpdateSelectedFeatureMode, Yg and Cce into request.jf. pRd passes that integer
through EOd into JSPB field 50 (the serialized array's index 49). This path
does not set the learning-mode companion fields 54/55 or select a model header.
These are static frontend contracts; account generation remains a separate
compatibility check.
"""

from types import MappingProxyType
from typing import Final

from gemini_webapi.exceptions import GeminiError


MEDIA_FEATURE_MODE_INDEX: Final = 49
NATIVE_MEDIA_MODE_IDS = MappingProxyType({"image": 14, "music": 21})


class NativeMediaRequestShapeError(GeminiError):
    """Fail before sending; bypass upstream's APIError wrapping and retries."""

    code = "UPSTREAM_CHANGED"


def native_media_mode_id(mode: str) -> int:
    """Resolve only modes observed in the current public frontend chain."""
    try:
        return NATIVE_MEDIA_MODE_IDS[mode]
    except KeyError as error:
        raise ValueError("media_mode supports only image or music.") from error
