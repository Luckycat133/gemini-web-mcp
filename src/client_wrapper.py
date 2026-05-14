"""
Gemini 客户端封装 - 线程安全版本
"""

import os
import time
import threading
import logging
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

try:
    from .cookie_manager import (
        get_cookie_manager,
        init_cookie_manager,
        CookieData
    )
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

# 线程安全的全局变量
_client: Optional[Any] = None
_initialized: bool = False
_client_lock = threading.Lock()

# 内存会话存储（带过期机制）
_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()

# 会话持久化配置
_SESSIONS_DIR = os.environ.get("GEMINI_SESSIONS_DIR", "./.gemini_sessions")
_SESSIONS_FILE = os.path.join(_SESSIONS_DIR, "sessions.json")
_SESSIONS_FILE_LOCK = threading.Lock()


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


def _on_cookie_update(cookie_data: CookieData) -> None:
    """
    Cookie 更新回调 - 当 Cookie 更新时重置客户端
    """
    logger.info("🔄 Cookie 已更新，重置客户端...")
    reset_client()
    
    # 更新环境变量
    os.environ["GEMINI_PSID"] = cookie_data.psid
    if cookie_data.psidts:
        os.environ["GEMINI_PSIDTS"] = cookie_data.psidts


def init_cookie_manager_integration() -> None:
    """
    初始化 Cookie Manager 集成
    """
    if not COOKIE_MANAGER_AVAILABLE:
        return
    
    auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
    
    init_cookie_manager(
        auto_refresh=auto_refresh,
        on_cookie_update=_on_cookie_update
    )
    
    cookie_manager = get_cookie_manager()
    cookie_manager.start_monitor()
    logger.info("✅ Cookie Manager 集成已初始化")


def get_cookie_from_browser(browser: str = "chrome") -> bool:
    """
    从浏览器获取 Cookie 并更新
    
    Args:
        browser: 浏览器类型
    
    Returns:
        是否成功获取
    """
    if not COOKIE_MANAGER_AVAILABLE:
        logger.error("❌ Cookie Manager 不可用")
        return False
    
    cookie_manager = get_cookie_manager()
    psid, psidts = cookie_manager.get_cookie_from_browser(browser)
    
    if psid:
        success = cookie_manager.update_cookie(psid, psidts, source=f"browser_{browser}")
        if success:
            # 更新环境变量
            os.environ["GEMINI_PSID"] = psid
            if psidts:
                os.environ["GEMINI_PSIDTS"] = psidts
            logger.info("✅ 已从浏览器获取 Cookie 并更新")
        return success
    
    return False


def get_cookie_status() -> Dict[str, Any]:
    """
    获取 Cookie 状态
    
    Returns:
        状态信息字典
    """
    if not COOKIE_MANAGER_AVAILABLE:
        return {
            "available": False,
            "message": "Cookie Manager 不可用"
        }
    
    cookie_manager = get_cookie_manager()
    status, info = cookie_manager.get_cookie_status()
    
    return {
        "available": True,
        "status": status.value,
        **info
    }


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
            save_sessions_to_file()
        
        return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """移除会话"""
    with _sessions_lock:
        if session_id in _sessions:
            del _sessions[session_id]
    
    save_sessions_to_file()


def clear_sessions() -> None:
    """清空所有会话"""
    with _sessions_lock:
        _sessions.clear()
    
    save_sessions_to_file()


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
        
        count = len(expired)
    
    if count > 0:
        save_sessions_to_file()
    
    return count


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


def _ensure_sessions_dir() -> None:
    """确保会话保存目录存在"""
    try:
        Path(_SESSIONS_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"创建会话目录失败: {e}")


def save_sessions_to_file() -> None:
    """
    将所有内存会话保存到文件（JSON格式）
    注意：GeminiClient的session对象无法直接序列化，我们只保存元数据
    """
    with _sessions_lock, _SESSIONS_FILE_LOCK:
        try:
            _ensure_sessions_dir()
            
            session_data = {}
            for sid, data in _sessions.items():
                session_data[sid] = {
                    "model": data["model"],
                    "created_at": data["created_at"]
                }
            
            with open(_SESSIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"已保存 {len(session_data)} 个会话元数据到文件")
        except Exception as e:
            logger.error(f"保存会话到文件失败: {e}")


def load_sessions_from_file() -> int:
    """
    从文件加载会话元数据
    返回加载的会话数量
    """
    with _sessions_lock, _SESSIONS_FILE_LOCK:
        try:
            if not os.path.exists(_SESSIONS_FILE):
                logger.debug("会话文件不存在")
                return 0
            
            with open(_SESSIONS_FILE, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            
            logger.debug(f"从文件加载了 {len(session_data)} 个会话元数据")
            return len(session_data)
        except Exception as e:
            logger.error(f"从文件加载会话失败: {e}")
            return 0


def save_session_to_file(session_id: str) -> None:
    """保存单个会话到文件"""
    save_sessions_to_file()


def store_session(session_id: str, session: Any, model: str = "fast") -> None:
    """存储多轮对话会话（带时间戳和自动保存）"""
    with _sessions_lock:
        _sessions[session_id] = {
            "session": session,
            "model": model,
            "created_at": time.time()
        }
    
    save_sessions_to_file()
