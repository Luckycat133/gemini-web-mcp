"""
Gemini 常量配置
"""

from enum import Enum
from typing import TypedDict


class RPC(str, Enum):
    """Gemini Web RPC identifiers used by compatible tooling."""

    GENERATE = "GenerateContent"
    STREAM_GENERATE = "StreamGenerateContent"
    LIST_CHATS = "ListChats"
    FETCH_GEMS = "FetchGems"


ENDPOINTS = {
    "base": "https://gemini.google.com",
    "batchexecute": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
}

# 默认的会话/远端聊天保留时间（秒），可通过 GEMINI_CHAT_RETENTION_SECONDS 环境变量覆盖
DEFAULT_CHAT_RETENTION_SECONDS = 1800


class ModelConfig(TypedDict):
    name: str
    hex_id: str
    capacity_tail: int
    advanced_only: bool
    thinking_mode_id: int


MODEL_CONFIG: dict[str, ModelConfig] = {
    "flash-lite": {
        "name": "3.1 Flash-Lite",
        "hex_id": "8c46e95b1a07cecc",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 6,
    },
    "lite": {
        "name": "3.1 Flash-Lite",
        "hex_id": "8c46e95b1a07cecc",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 6,
    },
    "fast": {
        "name": "gemini-3-flash",
        "hex_id": "fbb127bbb056c959",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 1,
    },
    "flash": {
        "name": "gemini-3-flash",
        "hex_id": "fbb127bbb056c959",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 1,
    },
    "thinking": {
        "name": "gemini-3-flash-thinking",
        "hex_id": "5bf011840784117a",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 1,
    },
    "pro": {
        "name": "gemini-3-pro",
        "hex_id": "9d8ca3786ebdfbea",
        "capacity_tail": 1,
        "advanced_only": True,
        "thinking_mode_id": 3,
    },
}

THINKING_LEVEL_IDS = {
    "standard": 1,
    "extended": 2,
    "标准": 1,
    "扩展": 2,
}

THINKING_MODE_IDS = {
    "3.1 flash-lite": 6,
    "flash-lite": 6,
    "lite": 6,
    "8c46e95b1a07cecc": 6,
    "3.5 flash": 1,
    "flash": 1,
    "fast": 1,
    "thinking": 1,
    "gemini-3-flash": 1,
    "gemini-3-flash-thinking": 1,
    "fbb127bbb056c959": 1,
    "56fdd199312815e2": 1,
    "5bf011840784117a": 1,
    "e051ce1aa80aa576": 1,
    "3.1 pro": 3,
    "pro": 3,
    "gemini-3-pro": 3,
    "9d8ca3786ebdfbea": 3,
    "e6fa609c3fa255c0": 3,
}

class LearningModeConfig(TypedDict, total=False):
    alias_for: str
    id: int
    x9b_field: str
    x9b_value: int
    prompt_prefix: str


LEARNING_MODE_CONFIG: dict[str, LearningModeConfig] = {
    "interactive_quiz": {
        "id": 18,
        "x9b_field": "h5d",
        "x9b_value": 1,
        "prompt_prefix": "生成一份关于以下内容的互动式测验： ",
    },
    "quiz": {
        "alias_for": "interactive_quiz",
    },
    "flashcards": {
        "id": 19,
        "x9b_field": "h5d",
        "x9b_value": 2,
        "prompt_prefix": "为以下内容生成互动式抽认卡： ",
    },
    "practice_test": {
        "id": 20,
        "x9b_field": "h5d",
        "x9b_value": 3,
        "prompt_prefix": "创建模拟测试，考察主题是",
    },
    "study_guide": {
        "id": 21,
        "x9b_field": "h5d",
        "x9b_value": 4,
        "prompt_prefix": "帮我准备 ",
    },
    "exam_prep": {
        "alias_for": "study_guide",
    },
}


def resolve_model_name(model: str) -> str:
    """Resolve MCP aliases while keeping runtime Gemini model names intact."""
    config = MODEL_CONFIG.get(model)
    return config["name"] if config else model


def normalize_model_alias(model: str | None) -> str:
    """Normalize incoming aliases to a stable MCP-facing model key."""
    if not model:
        return "flash"

    alias = model.strip().lower()
    if alias in {"3.1 flash-lite", "flash-lite", "lite"}:
        return "flash-lite"
    if alias in {"3.5 flash", "flash", "fast"}:
        return "flash"
    if alias in {"3.1 pro", "pro"}:
        return "pro"
    if alias in {"thinking", "gemini-3-flash-thinking"}:
        return "thinking"
    return alias


def resolve_media_request(
    model: str | None,
    media_type: str,
    thinking_level: str | None = None,
) -> dict[str, str]:
    """Resolve the effective Gemini Web backend behavior for media generation."""
    alias = normalize_model_alias(model)
    thinking = (thinking_level or "standard").strip().lower()

    if media_type == "image":
        return {
            "requested_alias": alias,
            "effective_alias": "flash",
            "request_model": resolve_model_name("flash"),
            "backend_label": "Nano Banana 2",
            "note": (
                "Gemini Web 当前首轮图像生成统一走 Nano Banana 2；"
                "flash-lite / flash / pro 不会改变首轮图像后端。"
            ),
        }

    if media_type == "music":
        if alias == "pro" and thinking in {"extended", "扩展"}:
            return {
                "requested_alias": alias,
                "effective_alias": "pro",
                "request_model": resolve_model_name(alias),
                "backend_label": "Lyria 3 Pro",
                "note": "实测当前 MCP/Web RPC 中 pro + extended / 扩展 对应 Lyria 3 fullsong。",
            }
        return {
            "requested_alias": alias,
            "effective_alias": "flash",
            "request_model": resolve_model_name(alias),
            "backend_label": "Lyria 3",
            "note": "实测当前 MCP/Web RPC 中非 pro+extended 音乐请求返回 Lyria 3 clip。",
        }

    return {
        "requested_alias": alias,
        "effective_alias": alias,
        "request_model": resolve_model_name(alias),
        "backend_label": "Gemini Web default",
        "note": "",
    }


def describe_model_name(model: str) -> str:
    """Return a stable model label for session/status output."""
    resolved = resolve_model_name(model)
    return resolved or "unspecified"


def resolve_thinking_mode_id(model: object) -> int | None:
    """Resolve the current Web UI thinking-level mode bucket for a model."""
    if isinstance(model, str):
        keys = [model, resolve_model_name(model)]
    else:
        keys = [
            getattr(model, "model_id", ""),
            getattr(model, "model_name", ""),
            getattr(model, "display_name", ""),
        ]

    for key in keys:
        if isinstance(key, str):
            mode_id = THINKING_MODE_IDS.get(key.strip().lower())
            if mode_id:
                return mode_id
    return None


def resolve_thinking_level_id(thinking_level: str | None) -> int | None:
    """Resolve the Web UI standard/extended thinking-level selector."""
    if thinking_level is None:
        return None
    return THINKING_LEVEL_IDS.get(thinking_level.strip().lower())


def resolve_learning_mode_config(learning_mode: str | None) -> dict[str, object] | None:
    """Resolve Gemini Web guided-learning companion modes observed in the UI."""
    if not learning_mode:
        return None

    key = learning_mode.strip().lower().replace("-", "_")
    config = LEARNING_MODE_CONFIG.get(key)
    if not config:
        return None
    alias = config.get("alias_for")
    if isinstance(alias, str):
        config = LEARNING_MODE_CONFIG[alias]
    return dict(config)


def supported_learning_modes() -> str:
    """Return user-facing learning mode names."""
    return "interactive_quiz/quiz, flashcards, practice_test, study_guide/exam_prep"
