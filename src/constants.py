"""
Gemini 常量配置
"""

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
