import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_client: Optional[any] = None
_initialized: bool = False

# 内存会话存储
_sessions: dict[str, any] = {}


def get_gemini_client() -> any:
    """获取或初始化 GeminiClient 实例"""
    global _client
    
    if _client is None:
        try:
            from gemini_webapi import GeminiClient
        except ImportError:
            raise ImportError("请先安装 gemini-webapi: pip install gemini-webapi")
        
        # 从环境变量获取认证信息
        psid = os.environ.get("GEMINI_PSID")
        psidts = os.environ.get("GEMINI_PSIDTS", "")
        proxy = os.environ.get("GEMINI_PROXY")
        
        if not psid:
            raise ValueError(
                "GEMINI_PSID 环境变量未设置!\n"
                "获取方式：1. 访问 gemini.google.com 2. F12 → Application → Cookies "
                "→ 复制 __Secure-1PSID 的值"
            )
        
        logger.info("正在初始化 GeminiClient...")
        _client = GeminiClient(psid, psidts, proxy=proxy)
    
    return _client


async def initialize_client() -> any:
    """调用 client.init() 完成初始化（必须在首次使用前调用）"""
    global _client, _initialized
    
    if _client is None:
        _client = get_gemini_client()
    
    if not _initialized:
        logger.info("正在调用 client.init()...")
        await _client.init(
            timeout=30, 
            auto_close=False, 
            auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true") == "true"
        )
        _initialized = True
        logger.info("✅ GeminiClient 初始化完成！")
    
    return _client


def store_session(session_id: str, session: any, model: str = "fast") -> None:
    """存储多轮对话会话"""
    _sessions[session_id] = {"session": session, "model": model}


def get_session(session_id: str) -> Optional[dict]:
    """获取存储的会话"""
    return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """移除会话"""
    if session_id in _sessions:
        del _sessions[session_id]


def clear_sessions() -> None:
    """清空所有会话"""
    _sessions.clear()


def reset_client() -> None:
    """重置客户端实例（清除 cookies）"""
    global _client, _initialized
    _client = None
    _initialized = False
    clear_sessions()
