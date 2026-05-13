"""
Gemini 客户端封装 - 线程安全版本
"""

import os
import time
import threading
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# 线程安全的全局变量
_client: Optional[Any] = None
_initialized: bool = False
_client_lock = threading.Lock()

# 内存会话存储（带过期机制）
_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def validate_config() -> None:
    """
    验证必需的和可选的环境变量
    包括类型验证
    """
    required = ["GEMINI_PSID"]
    missing = [var for var in required if not os.environ.get(var)]
    if missing:
        raise ValueError(
            f"缺少必需的环境变量: {', '.join(missing)}\n"
            "请设置 GEMINI_PSID 环境变量"
        )
    
    # 可选变量类型验证
    optional_vars = {
        "GEMINI_PSIDTS": str,
        "GEMINI_PROXY": str,
        "GEMINI_AUTO_REFRESH": str
    }
    
    for var, expected_type in optional_vars.items():
        value = os.environ.get(var)
        if value is not None and not isinstance(value, expected_type):
            logger.warning(f"环境变量 {var} 类型不正确，期望 {expected_type.__name__}")
    
    # 验证 GEMINI_AUTO_REFRESH 的值
    auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH", "true")
    if auto_refresh.lower() not in ["true", "false"]:
        logger.warning(f"GEMINI_AUTO_REFRESH 值无效: {auto_refresh}，默认使用 'true'")


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
                auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
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
        # 直接清理过期会话，避免调用 cleanup_expired_sessions() 导致嵌套锁
        now = time.time()
        max_age_seconds = 1800
        expired = [sid for sid, data in _sessions.items() 
                   if now - data["created_at"] > max_age_seconds]
        
        for sid in expired:
            del _sessions[sid]
        
        if expired:
            logger.info(f"清理了 {len(expired)} 个过期会话")
        
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


def list_sessions() -> Dict[str, Dict[str, Any]]:
    """
    线程安全获取所有会话
    返回 dict 的副本
    """
    with _sessions_lock:
        return _sessions.copy()


def load_images(image_paths: List[str]) -> List[Any]:
    """
    安全加载图片，自动处理异常，确保文件句柄正确关闭
    """
    images = []
    if not image_paths:
        return images
    
    try:
        from PIL import Image
        for path in image_paths:
            if not path or not path.strip():
                continue
            try:
                img = Image.open(path)
                images.append(img)
            except Exception as e:
                logger.warning(f"加载图片失败: {path}, 错误: {e}")
                continue
    except ImportError:
        logger.warning("未安装 PIL/Pillow，无法加载图片")
    except Exception as e:
        logger.error(f"加载图片过程中出错: {e}")
    
    return images
