"""
Gemini 常量配置
"""

from enum import Enum
import re
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

# 远端 Gemini 对话在请求完成后的保留时间（秒）。0 表示返回前尝试删除并验证，失败或无法验证时留在持久队列中重试；可用 GEMINI_CHAT_RETENTION_SECONDS 覆盖。
DEFAULT_CHAT_RETENTION_SECONDS = 0
# 本地会话状态的最大存活时间（秒）。
DEFAULT_SESSION_MAX_AGE_SECONDS = 1800

OMNI_VIDEO_MODEL = "gemini-omni-1.2-flash"
OMNI_VIDEO_API_BACKEND = "Gemini API (Omni 1.2 Flash)"
NATIVE_MEDIA_BROWSER_BACKEND = "Gemini Web native browser"
GEMINI_GENERIC_FAILURE_REPLY = (
    "I'm having a hard time fulfilling your request. Can I help you with something else instead?"
)
_GEMINI_GENERATION_FAILURE_REPLIES = frozenset({
    GEMINI_GENERIC_FAILURE_REPLY,
    "I encountered an error doing what you asked. Could you try again?",
    "Sorry, something went wrong. Please try your request again.",
    "I'm sorry, I'm experiencing unusually high traffic right now generating videos and am at full capacity. Please try again later.",
    "我只是一个文本 AI，在这方面没法帮到你。",
    "由于程序代码的局限，我无法提供这方面的帮助。",
})
_MUSIC_TOOL_UNAVAILABLE = re.compile(
    r"\A(?:I can['’]t generate or output an audio file directly right now(?: because|, as) "
    r"the music generation tool isn['’]t available in this session\.(?:\s|$)|"
    r"I cannot directly generate or output playable audio files here, as "
    r"the music generation tool is currently unavailable\.(?:\s|$)|"
    r"I can['’]t generate the audio track right now because "
    r"the music generation tool isn['’]t available to run in this turn\.(?:\s|$)|"
    r"I don['’]t have a built-in music generation tool available right now to "
    r"output an audio file directly(?:[.,]|\s|$)|"
    r"No audio generation tool is currently active in this session, so I cannot render a playable audio track directly(?:[.,]|\s|$))",
    re.IGNORECASE,
)
_MUSIC_SCORE_FALLBACK = re.compile(
    r"\A(?:I can['’]t generate an audio track directly right now, but here is an original,|"
    r"Since direct (?:audio|music) generation isn['’]t available right now\b)(?:\s|,|$)",
    re.IGNORECASE,
)
_MUSIC_CHINESE_SCORE_FALLBACK = re.compile(
    r"\A(?:我已经为你创作了一段舒缓的钢琴纯音乐。\s*"
    r"由于当前系统工具调用受限，暂时无法直接为你生成并导出音频文件，\s*但我为你编写了|"
    r"(?:由于)?(?:目前|当前)(?:系统)?(?:无法直接|暂时无法)(?:为你)?生成(?:并导出)?(?:音频|音乐))",
)
_RESEARCH_REFUSAL_REPLIES = frozenset({
    "我是一个语言模型，这超出了我的设计用途。",
    "我没法提供这方面的帮助，因为我只是一个语言模型。",
    "我没法提供帮助。我只是一个语言模型，无法处理和理解这个问题。",
    "我是一个语言模型，在这方面帮不上忙。",
    "我是一个文本 AI，在这方面爱莫能助。",
    "请恕我爱莫能助，我只是一个语言模型，不能理解或回复你的这个问题。",
    "I'm a text-based AI and can't assist with that.",
    "I'm just a language model, so I can't help you with that.",
    "I cannot assist with that as I am only a language model.",
})
_RESEARCH_REFUSAL_PATTERN = re.compile(
    r"\A(?:我(?:只是)?一个(?:语言模型|文本\s*AI)|"
    r"我没法提供(?:这方面的)?帮助|"
    r"请恕我爱莫能助|"
    r"I(?:'m| am) (?:just )?a (?:language model|text-based AI)|"
    r"I (?:can['’]t|cannot) assist with that)\b",
    re.IGNORECASE,
)
_IMAGE_TOOL_UNAVAILABLE = re.compile(
    r"\A(?:I don['’]t have an (?:active )?image[- ]generation tool available in this (?:session|interface)|"
    r"I don['’]t have access to an image[- ]generation tool in this (?:session|interface)|"
    r"I cannot generate images directly in this (?:session|interface)|"
    r"I can['’]t generate (?:or output )?images directly (?:right now|in this (?:session|interface)))(?:[\s.,—–:-]|$)",
    re.IGNORECASE,
)
_MEDIA_GENERATION_ERROR = re.compile(
    r"\A(?:I seem to be encountering an error\.|"
    r"I can['’]t make (?:that type of video|videos?\b[^.?!]{0,200})\.)(?:\s|$)",
    re.IGNORECASE,
)
_MARKDOWN_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_ABC_FIELD = re.compile(r"([A-Za-z]):[ \t]*(.*)")
_ABC_HEADERS = {
    "X": re.compile(r"[0-9]+"),
    "M": re.compile(r"(?:C\|?|none|(?:[1-9][0-9]*|\([1-9][0-9]*(?:[ \t]*\+[ \t]*[1-9][0-9]*)+\))"
                    r"\s*/\s*[1-9][0-9]*)", re.IGNORECASE),
    "L": re.compile(r"[1-9][0-9]*\s*/\s*[1-9][0-9]*"),
    "K": re.compile(r"(?:[A-G](?:#|b)?(?:maj(?:or)?|m(?:in(?:or)?)?|dor(?:ian)?|mix(?:olydian)?|"
                    r"phr(?:ygian)?|lyd(?:ian)?|loc(?:rian)?|aeo(?:lian)?|ion(?:ian)?)?(?:[ \t].*)?|none|HP)",
                    re.IGNORECASE),
}
_ABC_INLINE_ANNOTATION = re.compile(r'"(?:[^"\\]|\\.)*"|![^!]*!|\+[^+]*\+|\[[A-Za-z]:[^\]]*\]')
_ABC_MUSIC_SYMBOLS = re.compile(r"[A-Ga-gzZxX0-9|:()\[\]{}^_=,/'<>.&+*~\\ \t-]+")
_ABC_NOTE = re.compile(r"[A-Ga-g]")


def is_music_tool_unavailable_reply(text: object) -> bool:
    """Match finite observed NoTool/score-fallback prefixes, never quoted instructions."""
    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split())
    return bool(_MUSIC_TOOL_UNAVAILABLE.match(normalized) or _MUSIC_SCORE_FALLBACK.match(normalized)
                or _MUSIC_CHINESE_SCORE_FALLBACK.match(normalized))


def is_image_tool_unavailable_reply(text: object) -> bool:
    """Match finite observed text-only image-tool-unavailable refusal prefixes."""
    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split())
    return bool(_IMAGE_TOOL_UNAVAILABLE.match(normalized))


def _abc_score_has_notes(lines: list[str]) -> bool:
    headers: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%"):
            continue
        field = _ABC_FIELD.fullmatch(line)
        if "K" in headers and (field is None or field[1] != "X"):
            if field is None:
                notation = _ABC_INLINE_ANNOTATION.sub("", line).partition("%")[0].strip()
                if _ABC_MUSIC_SYMBOLS.fullmatch(notation) and _ABC_NOTE.search(notation):
                    return True
            continue
        if field is None or not headers and field[1] != "X":
            return False
        name, value = field[1], field[2].partition("%")[0].strip()
        if name == "X":
            headers.clear()
        if name in _ABC_HEADERS:
            if not _ABC_HEADERS[name].fullmatch(value) or name == "K" and not {"X", "M", "L"} <= headers:
                return False
            headers.add(name)
    return False


def is_music_notation_reply(text: object) -> bool:
    """Recognize a complete fenced ABC score as text evidence only."""
    if not isinstance(text, str):
        return False
    fence = ""
    score: list[str] | None = None
    for line in text.splitlines():
        boundary = _MARKDOWN_FENCE.fullmatch(line)
        if boundary is not None:
            marker, info = boundary[1], boundary[2].strip()
            if not fence and (marker[0] == "~" or "`" not in info):
                fence = marker
                score = [] if info.lower() == "abc" else None
                continue
            if fence and marker[0] == fence[0] and len(marker) >= len(fence) and not info:
                if score is not None and _abc_score_has_notes(score):
                    return True
                fence, score = "", None
                continue
        if score is not None:
            score.append(line)
    return False


def is_media_generation_error_reply(text: object) -> bool:
    """Recognize the observed generic error sentence at the start of a reply."""
    return isinstance(text, str) and bool(_MEDIA_GENERATION_ERROR.match(" ".join(text.split())))


def is_generation_failure_reply(text: object) -> bool:
    """Recognize the full observed Gemini failure notice, including browser whitespace."""
    return (isinstance(text, str) and " ".join(text.split()) in _GEMINI_GENERATION_FAILURE_REPLIES
            or is_media_generation_error_reply(text))


def is_research_refusal_reply(text: object) -> bool:
    """Recognize canned LLM refusals when Deep Research or plan generation is rejected."""
    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split())
    return (
        is_generation_failure_reply(normalized)
        or normalized in _RESEARCH_REFUSAL_REPLIES
        or (len(normalized) <= 120 and bool(_RESEARCH_REFUSAL_PATTERN.match(normalized)))
    )


class ModelConfig(TypedDict):
    name: str
    hex_id: str
    capacity_tail: int
    advanced_only: bool
    thinking_mode_id: int


MODEL_CONFIG: dict[str, ModelConfig] = {
    "flash-lite": {
        "name": "Flash-Lite",
        "hex_id": "8c46e95b1a07cecc",
        "capacity_tail": 1,
        "advanced_only": False,
        "thinking_mode_id": 6,
    },
    "lite": {
        "name": "Flash-Lite",
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
    "low": 1,
    "medium": 4,
    "high": 2,
    "低": 1,
    "中": 4,
    "高": 2,
    "standard": 1,
    "extended": 2,
    "标准": 1,
    "扩展": 2,
}

THINKING_MODE_IDS = {
    "3.5 flash-lite": 6,
    "3.1 flash-lite": 6,
    "flash-lite": 6,
    "gemini-flash-lite": 6,
    "lite": 6,
    "8c46e95b1a07cecc": 6,
    "3.5 flash": 1,
    "3.8 flash": 1,
    "flash": 1,
    "fast": 1,
    "thinking": 1,
    "gemini-3-flash": 1,
    "gemini-flash": 1,
    "gemini-3-flash-thinking": 1,
    "fbb127bbb056c959": 1,
    "56fdd199312815e2": 1,
    "5bf011840784117a": 1,
    "e051ce1aa80aa576": 1,
    "3.1 pro": 3,
    "pro": 3,
    "gemini-3-pro": 3,
    "gemini-pro": 3,
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
    "guided_learning": {
        "id": 11,
        "x9b_field": "LYd",
        "x9b_value": 2,
        "prompt_prefix": "",
    },
    "guided": {
        "alias_for": "guided_learning",
    },
    "canvas": {
        "id": 8,
        "x9b_field": "QLd",
        "x9b_value": 2,
        "prompt_prefix": "",
    },
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


GEMINI_4_MODEL_NAMES = {
    "argon": "gemini-argon",
    "carbon": "gemini-carbon",
    "hydrogen": "gemini-4-hydrogen",
    "neon": "gemini-neon",
}


def gemini_4_model_family(label: str) -> str | None:
    """Identify exact family names without guessing opaque model selectors."""
    normalized = re.sub(r"[\s_-]+", "-", label.strip().lower())
    match = re.fullmatch(r"(?:gemini-)?(?:4-)?(argon|carbon|hydrogen|neon)", normalized)
    return match.group(1) if match else None


def is_auto_model_name(label: str) -> bool:
    """Recognize Auto labels returned by English or Chinese Web catalogs."""
    normalized = re.sub(r"[\s_-]+", "-", label.strip().lower())
    return normalized in {"auto", "automatic", "自动", "gemini-auto", "gemini-automatic", "gemini-自动"}


def resolve_model_name(model: str | None) -> str:
    """Resolve MCP aliases while keeping runtime Gemini model names intact."""
    if model is None:
        return ""
    family = gemini_4_model_family(model)
    if family:
        return GEMINI_4_MODEL_NAMES[family]
    if is_auto_model_name(model):
        return "auto"
    current_web_names = {
        "3.5 flash-lite": "flash-lite",
        "3.8 flash": "flash",
        "3.1 pro": "pro",
    }
    alias = current_web_names.get(model.strip().lower(), model)
    config = MODEL_CONFIG.get(alias)
    return config["name"] if config else model


def normalize_model_alias(model: str | None) -> str:
    """Normalize incoming aliases to a stable MCP-facing model key."""
    if not model:
        return "flash"

    alias = model.strip().lower()
    if alias in {"3.1 flash-lite", "3.5 flash-lite", "flash-lite", "lite"}:
        return "flash-lite"
    if alias in {"3.5 flash", "3.8 flash", "flash", "fast"}:
        return "flash"
    if alias in {"3.1 pro", "pro"}:
        return "pro"
    family = gemini_4_model_family(alias)
    if family:
        return family
    if is_auto_model_name(alias):
        return "auto"
    if alias in {"thinking", "gemini-3-flash-thinking"}:
        return "thinking"
    return alias


def resolve_media_request(
    model: str | None,
    media_type: str,
    thinking_level: str | None = None,
) -> dict[str, str]:
    """Resolve Web selectors without asserting the hidden generator version."""
    omitted = model is None or not model.strip()
    alias = "hydrogen" if omitted else normalize_model_alias(model)

    if media_type == "video" and alias == OMNI_VIDEO_MODEL:
        return {
            "requested_alias": alias,
            "effective_alias": alias,
            "request_model": OMNI_VIDEO_MODEL,
            "backend_label": OMNI_VIDEO_API_BACKEND,
            "note": "Explicit Gemini API video generation with GEMINI_API_KEY.",
        }

    if media_type == "image":
        return {
            "requested_alias": alias,
            "effective_alias": alias,
            "request_model": resolve_model_name(alias),
            "backend_label": "Gemini Web image mode",
            "note": (
                "使用网页图片生成入口；聊天模型选择器与图片生成后端分别记录，"
                "仅在上游返回可验证的模型版本时报告实际后端。"
            ),
        }

    if media_type == "music":
        return {
            "requested_alias": alias,
            "effective_alias": alias,
            "request_model": resolve_model_name(alias),
            "backend_label": "Gemini Web music mode",
            "note": (
                "使用网页音乐生成入口；聊天模型和 thinking_level 不代表音乐模型版本。"
                "仅在上游返回可验证的模型版本时报告实际后端。"
            ),
        }

    if media_type == "video":
        return {
            "requested_alias": alias,
            "effective_alias": alias,
            "request_model": resolve_model_name(alias),
            "backend_label": "Gemini Web video mode",
            "note": "使用已观测的视频专用请求模式；仅在实际视频文件及视频轨道验证通过后报告完成。",
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
    """Resolve low/medium/high and compatible standard/extended Web selectors."""
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
    return "guided_learning/guided, canvas, interactive_quiz/quiz, flashcards, practice_test, study_guide/exam_prep"
