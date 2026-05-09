"""
Gemini Web 逆向工程常量
基于 HanaokaYuzu/Gemini-API 源码分析 + 最新调研（2026.5）
"""

from enum import StrEnum

# 模型配置
MODEL_CONFIG = {
    "fast": {
        "name": "gemini-3-flash",
        "hex_id": "fbb127bbb056c959",
        "capacity_tail": 1,
        "advanced_only": False,
        "music_model": "lyria-3-clip",
    },
    "thinking": {
        "name": "gemini-3-flash-thinking",
        "hex_id": "5bf011840784117a",
        "capacity_tail": 1,
        "advanced_only": False,
        "music_model": "lyria-3-pro",
    },
    "pro": {
        "name": "gemini-3-pro",
        "hex_id": "9d8ca3786ebdfbea",
        "capacity_tail": 1,
        "advanced_only": True,
        "music_model": "lyria-3-pro",
    },
}

# RPC 方法 ID
class RPC(StrEnum):
    # 聊天管理
    LIST_CHATS = "MaZiqc"
    READ_CHAT = "hNvQHb"
    DELETE_CHAT_1 = "GzXR5e"
    DELETE_CHAT_2 = "qWymEb"
    
    # Gem 管理
    LIST_GEMS = "CNgdBe"
    CREATE_GEM = "oMH3Zd"
    UPDATE_GEM = "kHv0Vd"
    DELETE_GEM = "UXcSJb"
    
    # Deep Research
    DEEP_RESEARCH_STATUS = "kwDCne"
    DEEP_RESEARCH_PREFS = "L5adhe"
    DEEP_RESEARCH_BOOTSTRAP = "ku4Jyf"
    DEEP_RESEARCH_MODEL_STATE = "qpEbW"
    DEEP_RESEARCH_CAPS = "aPya6c"
    DEEP_RESEARCH_ACK = "PCck7e"
    
    # 用户状态
    GET_USER_STATUS = "otAQ7b"
    BARD_SETTINGS = "ESY5D"

# API 端点
ENDPOINTS = {
    "GENERATE": "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
    "BATCH_EXEC": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
    "ROTATE_COOKIES": "https://accounts.google.com/RotateCookies",
    "UPLOAD": "https://content-push.googleapis.com/upload",
}

# 错误码
class ErrorCode:
    TEMPORARY_ERROR = 1013
    USAGE_LIMIT_EXCEEDED = 1037
    MODEL_INCONSISTENT = 1050
    MODEL_HEADER_INVALID = 1052
    IP_BLOCKED = 1060
    LOCATION_REJECTED = 1060


def build_model_header(hex_id: str, capacity_tail: int) -> dict[str, str]:
    """构建模型选择 Header"""
    return {
        "x-goog-ext-525001261-jspb": f'[1,null,null,null,"{hex_id}",null,null,0,[4],null,null,{capacity_tail}]',
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0]",
    }
