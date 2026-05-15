"""
Cookie 管理模块 - 自动刷新、监控和浏览器Cookie获取
"""

import os
import time
import threading
import logging
from typing import Optional, Dict, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CookieStatus(Enum):
    """Cookie 状态枚举"""
    VALID = "valid"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    REFRESHING = "refreshing"


@dataclass
class CookieData:
    """Cookie 数据结构"""
    psid: str
    psidts: str = ""
    acquired_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    status: CookieStatus = CookieStatus.UNKNOWN
    source: str = "manual"  # manual, browser, refresh


class CookieManager:
    """Cookie 管理器 - 处理自动刷新、监控和浏览器Cookie获取"""

    def __init__(
        self,
        psid_env: str = "GEMINI_PSID",
        psidts_env: str = "GEMINI_PSIDTS",
        refresh_threshold_hours: int = 24,
        auto_refresh: bool = True,
        on_cookie_update: Optional[Callable[[CookieData], None]] = None
    ):
        """
        初始化 Cookie 管理器
        
        Args:
            psid_env: PSID 环境变量名
            psidts_env: PSIDTS 环境变量名
            refresh_threshold_hours: Cookie 刷新阈值（小时）
            auto_refresh: 是否启用自动刷新
            on_cookie_update: Cookie 更新回调函数
        """
        self.psid_env = psid_env
        self.psidts_env = psidts_env
        self.refresh_threshold = refresh_threshold_hours * 3600
        self.auto_refresh = auto_refresh
        self.on_cookie_update = on_cookie_update
        
        self._cookie_data: Optional[CookieData] = None
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._monitor_interval = 3600  # 1小时检查一次
        
        self._load_initial_cookie()

    def _load_initial_cookie(self) -> None:
        """加载初始 Cookie（从环境变量）"""
        psid = os.environ.get(self.psid_env, "")
        psidts = os.environ.get(self.psidts_env, "")
        
        if psid:
            self._cookie_data = CookieData(
                psid=psid,
                psidts=psidts,
                source="manual"
            )
            self._cookie_data.status = CookieStatus.VALID
            logger.info("✅ 从环境变量加载 Cookie")

    @staticmethod
    def get_cookie_from_browser(browser: str = "chrome") -> Tuple[Optional[str], Optional[str]]:
        """
        从浏览器自动获取 Cookie
        
        Args:
            browser: 浏览器类型 (chrome, firefox, edge, opera, brave)
        
        Returns:
            (psid, psidts) 元组
        """
        try:
            import browser_cookie3
        except ImportError:
            logger.warning("⚠️ browser-cookie3 未安装，无法从浏览器获取Cookie")
            logger.warning("   请运行: pip install browser-cookie3")
            return None, None
        
        try:
            cookie_functions = {
                "chrome": browser_cookie3.chrome,
                "firefox": browser_cookie3.firefox,
                "edge": browser_cookie3.edge,
                "opera": browser_cookie3.opera,
                "brave": browser_cookie3.brave
            }
            
            if browser not in cookie_functions:
                logger.error(f"❌ 不支持的浏览器: {browser}")
                return None, None
            
            cookie_func = cookie_functions[browser]
            logger.info(f"🔍 正在从 {browser} 浏览器获取 Cookie...")
            
            cj = cookie_func(domain_name="google.com")
            
            psid = ""
            psidts = ""
            
            for cookie in cj:
                if cookie.name == "__Secure-1PSID":
                    psid = cookie.value
                    logger.info(f"✅ 获取到 __Secure-1PSID")
                elif cookie.name == "__Secure-1PSIDTS":
                    psidts = cookie.value
                    logger.info(f"✅ 获取到 __Secure-1PSIDTS")
            
            if psid:
                return psid, psidts
            else:
                logger.warning("⚠️ 未在浏览器中找到有效的 Cookie")
                return None, None
                
        except Exception as e:
            logger.error(f"❌ 从浏览器获取 Cookie 失败: {e}")
            return None, None

    def update_cookie(self, psid: str, psidts: str = "", source: str = "manual") -> bool:
        """
        更新 Cookie
        
        Args:
            psid: 新的 PSID
            psidts: 新的 PSIDTS
            source: Cookie 来源
        
        Returns:
            是否更新成功
        """
        with self._lock:
            if not psid:
                logger.error("❌ PSID 不能为空")
                return False
            
            self._cookie_data = CookieData(
                psid=psid,
                psidts=psidts,
                source=source,
                status=CookieStatus.VALID
            )
            
            logger.info(f"✅ Cookie 已更新 (来源: {source})")
            
            if self.on_cookie_update:
                try:
                    self.on_cookie_update(self._cookie_data)
                except Exception as e:
                    logger.error(f"❌ Cookie 更新回调失败: {e}")
            
            return True

    def get_cookie(self) -> Optional[CookieData]:
        """
        获取当前 Cookie
        
        Returns:
            CookieData 对象或 None
        """
        with self._lock:
            return self._cookie_data

    def get_cookie_status(self) -> Tuple[CookieStatus, Dict[str, any]]:
        """
        获取 Cookie 状态
        
        Returns:
            (状态, 详细信息)
        """
        with self._lock:
            if not self._cookie_data:
                return CookieStatus.UNKNOWN, {
                    "has_cookie": False,
                    "message": "未设置 Cookie"
                }
            
            age = time.time() - self._cookie_data.acquired_at
            hours = age / 3600
            
            info = {
                "has_cookie": True,
                "acquired_at": self._cookie_data.acquired_at,
                "age_hours": round(hours, 2),
                "source": self._cookie_data.source,
                "status": self._cookie_data.status.value
            }
            
            if hours > self.refresh_threshold / 3600:
                status = CookieStatus.EXPIRED
                info["needs_refresh"] = True
            else:
                status = CookieStatus.VALID
                info["needs_refresh"] = False
            
            return status, info

    def needs_refresh(self) -> bool:
        """
        检查是否需要刷新 Cookie
        
        Returns:
            是否需要刷新
        """
        status, _ = self.get_cookie_status()
        return status == CookieStatus.EXPIRED

    def refresh_cookie(self, browser: Optional[str] = None) -> bool:
        """
        刷新 Cookie
        
        Args:
            browser: 浏览器类型（如果指定，则尝试从浏览器获取）
        
        Returns:
            是否刷新成功
        """
        with self._lock:
            if not self._cookie_data:
                logger.error("❌ 没有可用的 Cookie 进行刷新")
                return False
            
            self._cookie_data.status = CookieStatus.REFRESHING
        
        try:
            if browser:
                psid, psidts = self.get_cookie_from_browser(browser)
                if psid:
                    return self.update_cookie(psid, psidts, source=f"browser_{browser}")
            
            logger.warning("⚠️ 自动刷新需要用户交互，请手动更新 Cookie")
            return False
            
        finally:
            with self._lock:
                if self._cookie_data:
                    self._cookie_data.status = CookieStatus.VALID

    def _monitor_loop(self) -> None:
        """监控循环"""
        logger.info("🔍 Cookie 监控线程已启动")
        
        while self._monitor_running:
            try:
                status, info = self.get_cookie_status()
                
                if status == CookieStatus.EXPIRED:
                    logger.warning(f"⚠️ Cookie 可能已过期，使用时长: {info['age_hours']:.1f} 小时")
                    
                    if self.auto_refresh:
                        logger.info("🔄 尝试自动刷新 Cookie...")
                        self.refresh_cookie()
                
                time.sleep(self._monitor_interval)
                
            except Exception as e:
                logger.error(f"❌ Cookie 监控出错: {e}")
                time.sleep(60)

    def start_monitor(self, interval: int = 3600) -> None:
        """
        启动 Cookie 监控
        
        Args:
            interval: 监控间隔（秒）
        """
        with self._lock:
            if self._monitor_running:
                logger.warning("⚠️ Cookie 监控已在运行")
                return
            
            self._monitor_interval = interval
            self._monitor_running = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True
            )
            self._monitor_thread.start()
            logger.info(f"✅ Cookie 监控已启动 (间隔: {interval}秒)")

    def stop_monitor(self) -> None:
        """停止 Cookie 监控"""
        with self._lock:
            if not self._monitor_running:
                return
            
            self._monitor_running = False
            logger.info("🛑 Cookie 监控已停止")

    def to_env_vars(self) -> Dict[str, str]:
        """
        将 Cookie 转换为环境变量字典
        
        Returns:
            环境变量字典
        """
        with self._lock:
            if not self._cookie_data:
                return {}
            
            env = {
                self.psid_env: self._cookie_data.psid
            }
            
            if self._cookie_data.psidts:
                env[self.psidts_env] = self._cookie_data.psidts
            
            return env


# 全局 Cookie 管理器实例
_cookie_manager: Optional[CookieManager] = None
_cookie_manager_lock = threading.Lock()


def get_cookie_manager() -> CookieManager:
    """
    获取全局 Cookie 管理器实例（单例模式）
    
    Returns:
        CookieManager 实例
    """
    global _cookie_manager
    
    with _cookie_manager_lock:
        if _cookie_manager is None:
            _cookie_manager = CookieManager()
        return _cookie_manager


def init_cookie_manager(
    auto_refresh: bool = True,
    on_cookie_update: Optional[Callable[[CookieData], None]] = None
) -> CookieManager:
    """
    初始化全局 Cookie 管理器
    
    Args:
        auto_refresh: 是否启用自动刷新
        on_cookie_update: Cookie 更新回调
    
    Returns:
        CookieManager 实例
    """
    global _cookie_manager
    
    with _cookie_manager_lock:
        _cookie_manager = CookieManager(
            auto_refresh=auto_refresh,
            on_cookie_update=on_cookie_update
        )
        return _cookie_manager
