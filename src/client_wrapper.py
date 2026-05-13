"""
Gemini 客户端封装 - 线程安全版本
"""

import os
import time
import threading
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 线程安全的全局变量
_client: Optional[Any] = None
_initialized: bool = False
_client_lock = threading.Lock()

# 内存会话存储（带过期机制）
_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def validate_config() -> None:
    """验证必需的环境变量"""
    required = ["GEMINI_PSID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise ValueError(
            f"缺少必需的环境变量: {', '.join(missing)}\n"
            "请设置 GEMINI_PSID 环境变量"
        )


def get_gemini_client() -> Any:
    """获取或初始化 GeminiClient 实例（线程安全）"""
    global _client
    
    with _client_lock:
        if _client is None:
            validate_config()
            
            try:
                from gemini_webapi import GeminiClient
            except ImportError:
                raise ImportError("请先安装 gemini-webapi: pip install gemini-webapi")
            
            # 从环境变量获取认证信息
            psid = os.environ.get("GEMINI_PSID")
            psidts = os.environ.get("GEMINI_PSIDTS", "")
            proxy = os.environ.get("GEMINI_PROXY")
            
            logger.info("正在初始化 GeminiClient...")
            _client = GeminiClient(psid, psidts, proxy=proxy)
    
    return _client


async def initialize_client() -> Any:
    """调用 client.init() 完成初始化（线程安全）"""
    global _client, _initialized
    
    with _client_lock:
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


def store_session(session_id: str, session: Any, model: str = "fast") -> None:
    """存储多轮对话会话（带时间戳）"""
    with _sessions_lock:
        _sessions[session_id] = {
            "session": session,
            "model": model,
            "created_at": time.time()
        }


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取存储的会话"""
    with _sessions_lock:
        # 自动清理过期会话
        cleanup_expired_sessions()
        return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """移除会话"""
    with _sessions_lock:
        if session_id in _sessions:
            del _sessions[session_id]


def clear_sessions() -> None:
    """清空所有会话"""
    with _sessions_lock:
        _sessions.clear()


def cleanup_expired_sessions(max_age_seconds: int = 1800) -> int:
    """
    清理过期会话
    默认保留30分钟内活跃的会话
    返回被清理的会话数量
    """
    with _sessions_lock:
        now = time.time()
        expired = [sid for sid, data in _sessions.items() 
                   if now - data["created_at"] > max_age_seconds]
        
        for sid in expired:
            del _sessions[sid]
        
        if expired:
            logger.info(f"清理了 {len(expired)} 个过期会话")
        
        return len(expired)


def reset_client() -> None:
    """重置客户端实例（清除 cookies）"""
    global _client, _initialized
    
    with _client_lock:
        _client = None
        _initialized = False
    
    clear_sessions()
    logger.info("✅ 客户端已重置")


def get_session_count() -> int:
    """获取当前活跃会话数量"""
    with _sessions_lock:
        return len(_sessions)
