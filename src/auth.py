"""
认证管理模块
支持从 .env、环境变量、cookies.json 加载 Google 认证 Cookie
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 默认的 Cookie 文件路径
DEFAULT_COOKIE_FILE = Path(__file__).parent.parent / "cookies.json"


def load_cookies_from_env() -> dict[str, str]:
    """从环境变量加载必需的 Cookie"""
    psid = os.environ.get("GEMINI_PSID", "")
    psidts = os.environ.get("GEMINI_PSIDTS", "")
    
    if not psid:
        logger.warning("GEMINI_PSID 环境变量未设置")
    
    return {
        "__Secure-1PSID": psid,
        "__Secure-1PSIDTS": psidts,
    }


def load_cookies_from_file(filepath: Optional[str] = None) -> dict[str, str]:
    """
    从 cookies.json 文件加载完整 Cookie。
    
    支持 EditThisCookie 格式的 JSON 导出。
    """
    path = Path(filepath) if filepath else DEFAULT_COOKIE_FILE
    
    if not path.exists():
        logger.warning(f"Cookie 文件不存在: {path}")
        return {}
    
    try:
        with open(path) as f:
            cookies_data = json.load(f)
        
        if isinstance(cookies_data, list):
            # EditThisCookie format: list of cookie objects
            cookies = {}
            for cookie in cookies_data:
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                if name and value:
                    cookies[name] = value
            return cookies
        elif isinstance(cookies_data, dict):
            # Simple key-value format
            return cookies_data
        else:
            logger.warning(f"无法解析 Cookie 文件格式: {path}")
            return {}
    except Exception as e:
        logger.error(f"加载 Cookie 文件失败: {e}")
        return {}


def get_auth_cookies() -> tuple[str, str]:
    """
    获取认证所需的 Cookie。
    
    优先级: 环境变量 > cookies.json
    
    Returns:
        (psid, psidts) 元组
    """
    # 先尝试环境变量
    psid = os.environ.get("GEMINI_PSID", "")
    psidts = os.environ.get("GEMINI_PSIDTS", "")
    
    if psid:
        logger.info("使用环境变量中的 Cookie")
        return psid, psidts
    
    # 回退到文件
    file_cookies = load_cookies_from_file()
    if file_cookies:
        psid = file_cookies.get("__Secure-1PSID", "")
        psidts = file_cookies.get("__Secure-1PSIDTS", "")
        if psid:
            logger.info("使用 cookies.json 中的 Cookie")
            return psid, psidts
    
    logger.warning("未找到有效的认证 Cookie")
    return psid, psidts


def validate_cookies() -> bool:
    """验证 Cookie 是否有效"""
    psid, psidts = get_auth_cookies()
    if not psid:
        logger.error("缺少 __Secure-1PSID Cookie")
        return False
    logger.info("Cookie 验证通过")
    return True


def get_cookie_dict() -> dict[str, str]:
    """获取完整的 Cookie 字典用于 HTTP 请求"""
    cookies = load_cookies_from_file()
    if not cookies:
        cookies = load_cookies_from_env()
    return cookies
