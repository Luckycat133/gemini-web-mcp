"""
智能错误处理模块 - 让 AI 能够自主解决常见问题
"""

import logging
import functools
from typing import Optional, Dict, Any
from mcp.types import TextContent

logger = logging.getLogger(__name__)

class GeminiError(Exception):
    """自定义 Gemini 错误类"""
    def __init__(self, code: str, message: str, solution: str = ""):
        super().__init__(message)
        self.code = code
        self.solution = solution

ERROR_CODES = {
    "NO_COOKIE": {
        "message": "未设置 GEMINI_PSID 环境变量",
        "solution": "请设置环境变量 export GEMINI_PSID=xxx 或使用 gemini_get_cookie_from_browser 从浏览器获取",
        "actionable": True,
        "tool": "gemini_get_cookie_from_browser"
    },
    "INVALID_COOKIE": {
        "message": "Cookie 无效或已过期",
        "solution": "请更新 GEMINI_PSID 或使用 gemini_get_cookie_from_browser 重新获取",
        "actionable": True,
        "tool": "gemini_get_cookie_from_browser"
    },
    "NETWORK_ERROR": {
        "message": "网络连接失败",
        "solution": "请检查网络连接或设置代理 GEMINI_PROXY=http://proxy:port",
        "actionable": True,
        "tool": None
    },
    "SESSION_NOT_FOUND": {
        "message": "会话不存在",
        "solution": "请先使用 gemini_start_chat 创建会话",
        "actionable": True,
        "tool": "gemini_start_chat"
    },
    "MODEL_UNAVAILABLE": {
        "message": "模型不可用（可能需要 AI Plus 订阅）",
        "solution": "请尝试使用 fast 模型，或检查账户是否有 AI Plus 订阅",
        "actionable": True,
        "tool": "gemini_list_models"
    },
    "RATE_LIMIT": {
        "message": "请求频率超限",
        "solution": "请稍后重试，或切换到 fast 模型减少请求频率",
        "actionable": True,
        "tool": None
    },
    "IMAGE_LOAD_FAILED": {
        "message": "图片加载失败",
        "solution": "请检查图片路径是否正确，或确保已安装 pillow: pip install pillow",
        "actionable": True,
        "tool": None
    },
}

def handle_error(exception: Exception) -> Dict[str, Any]:
    """
    智能错误处理 - 分析异常并提供解决方案
    
    Returns:
        dict: 包含 error_code, message, solution, actionable, tool
    """
    error_str = str(exception).lower()
    
    # 识别常见错误模式
    if "psid" in error_str or "cookie" in error_str:
        if "not set" in error_str or "missing" in error_str:
            return ERROR_CODES["NO_COOKIE"]
        else:
            return ERROR_CODES["INVALID_COOKIE"]
    
    if "network" in error_str or "connection" in error_str or "timeout" in error_str:
        return ERROR_CODES["NETWORK_ERROR"]
    
    if "session" in error_str and ("not found" in error_str or "不存在" in error_str):
        return ERROR_CODES["SESSION_NOT_FOUND"]
    
    if "model" in error_str and ("not available" in error_str or "unavailable" in error_str):
        return ERROR_CODES["MODEL_UNAVAILABLE"]
    
    if "rate" in error_str or "limit" in error_str:
        return ERROR_CODES["RATE_LIMIT"]
    
    if "image" in error_str or "pillow" in error_str:
        return ERROR_CODES["IMAGE_LOAD_FAILED"]
    
    # 默认错误处理
    return {
        "message": str(exception),
        "solution": "未知错误，请检查日志或联系管理员",
        "actionable": False,
        "tool": None
    }

def format_error_response(error_info: Dict[str, Any]) -> TextContent:
    """
    格式化错误响应，便于 AI 理解和处理
    
    Args:
        error_info: 错误信息字典
    
    Returns:
        TextContent: 格式化的错误响应
    """
    actionable_marker = "✅" if error_info.get("actionable") else "⚠️"
    response_parts = [
        f"{actionable_marker} 错误: {error_info['message']}",
        f"\n💡 解决方案: {error_info['solution']}"
    ]
    
    if error_info.get("tool"):
        response_parts.append(f"\n🔧 可使用工具: {error_info['tool']}")
    
    return TextContent(type="text", text="".join(response_parts))

def wrap_tool_error(func):
    """
    装饰器：自动捕获并处理工具执行错误
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"工具执行错误: {e}")
            error_info = handle_error(e)
            return [format_error_response(error_info)]
    return wrapper
