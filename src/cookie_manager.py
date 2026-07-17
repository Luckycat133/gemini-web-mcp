"""
Cookie 管理模块 - 自动刷新、监控和浏览器Cookie获取
"""

import os
import sys
import time
import threading
import logging
import asyncio
import json
from typing import Optional, Dict, Tuple, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
    extra_cookies: Dict[str, str] = field(default_factory=dict)
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
                extra_cookies=self._load_extra_cookies_from_env(psid, psidts),
                source="manual"
            )
            self._cookie_data.status = CookieStatus.VALID
            logger.info("✅ 从环境变量加载 Cookie")

    @staticmethod
    def _load_extra_cookies_from_env(psid: str = "", psidts: str = "") -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        if psid:
            cookies["__Secure-1PSID"] = psid
        if psidts:
            cookies["__Secure-1PSIDTS"] = psidts
        psidcc = os.environ.get("GEMINI_PSIDCC", "")
        if psidcc:
            cookies["__Secure-1PSIDCC"] = psidcc
        return cookies

    @staticmethod
    def _google_cookie_names() -> set[str]:
        return {
            "__Secure-1PSID",
            "__Secure-1PSIDTS",
            "__Secure-1PSIDCC",
            "__Secure-1PAPISID",
            "__Secure-3PSID",
            "__Secure-3PSIDTS",
            "__Secure-3PSIDCC",
            "__Secure-3PAPISID",
            "__Secure-BUCKET",
            "SID",
            "SIDCC",
            "HSID",
            "SSID",
            "APISID",
            "SAPISID",
            "NID",
            "AEC",
            "COMPASS",
            "GCL_AW_P",
            "SEARCH_SAMESITE",
            "_ga",
            "_ga_BF8Q35BMLM",
            "_ga_WC57KJ50ZZ",
            "_gcl_au",
        }

    @staticmethod
    def get_cookies_from_browser(browser: str = "chrome", profile: str = "") -> Dict[str, str]:
        """从浏览器获取 Gemini 所需的完整 Google 认证 Cookie。"""
        cookie_names = CookieManager._google_cookie_names()

        try:
            import browser_cookie3
        except ImportError:
            logger.warning("⚠️ browser-cookie3 未安装，无法从浏览器获取Cookie")
            logger.warning("   请运行: pip install browser-cookie3")
            return {}

        cookie_functions = {
            "chrome": browser_cookie3.chrome,
            "firefox": browser_cookie3.firefox,
            "edge": browser_cookie3.edge,
            "opera": browser_cookie3.opera,
            "brave": browser_cookie3.brave
        }

        if browser not in cookie_functions:
            logger.error(f"❌ 不支持的浏览器: {browser}")
            return {}

        try:
            logger.info(f"🔍 正在从 {browser} 浏览器获取 Cookie...")
            candidates = CookieManager._browser_cookie_candidates(
                browser_cookie3,
                browser,
                cookie_functions[browser],
                cookie_names,
            )
            if profile and candidates:
                cookies = CookieManager._select_named_cookie_candidate(candidates, profile)
            elif not candidates:
                cookies = CookieManager._read_cookie_jar(
                    cookie_functions[browser](domain_name="google.com"),
                    cookie_names,
                )
            else:
                cookies = CookieManager._select_valid_cookie_candidate(candidates)

            if not cookies.get("__Secure-1PSID"):
                logger.warning("⚠️ 未在浏览器中找到有效的 Cookie")
                return {}

            for name in ("__Secure-1PSID", "__Secure-1PSIDTS"):
                if name in cookies:
                    logger.info(f"✅ 获取到 {name}")
            logger.info(f"✅ 已获取 {len(cookies)} 个 Google 认证 Cookie")
            return cookies
        except Exception as e:
            logger.error(f"❌ 从浏览器获取 Cookie 失败: {e}")
            return {}

    @staticmethod
    def list_browser_cookie_profiles(browser: str = "chrome", validate: bool = True) -> list[dict[str, object]]:
        """Return browser cookie profile diagnostics without exposing cookie values."""
        cookie_names = CookieManager._google_cookie_names()

        try:
            import browser_cookie3
        except ImportError:
            return [{"browser": browser, "error": "browser-cookie3 not installed"}]

        cookie_functions = {
            "chrome": browser_cookie3.chrome,
            "firefox": browser_cookie3.firefox,
            "edge": browser_cookie3.edge,
            "opera": browser_cookie3.opera,
            "brave": browser_cookie3.brave,
        }
        if browser not in cookie_functions:
            return [{"browser": browser, "error": f"unsupported browser: {browser}"}]

        candidates = CookieManager._browser_cookie_candidates(
            browser_cookie3,
            browser,
            cookie_functions[browser],
            cookie_names,
            require_psid=False,
        )
        if not candidates:
            try:
                cookies = CookieManager._read_cookie_jar(
                    cookie_functions[browser](domain_name="google.com"),
                    cookie_names,
                )
            except Exception as e:
                return [{"browser": browser, "error": str(e)}]
            candidates = [("auto", cookies)] if cookies else []

        selected_profile = CookieManager._chrome_selected_profile_directory() if browser == "chrome" else ""
        profiles = [
            {
                "browser": browser,
                "profile": profile_name,
                "chrome_selected_profile": bool(selected_profile and profile_name == selected_profile),
                "chrome_selected_profile_directory": selected_profile,
                "has_psid": bool(cookies.get("__Secure-1PSID")),
                "has_psidts": bool(cookies.get("__Secure-1PSIDTS")),
                "cookie_count": len(cookies),
            }
            for profile_name, cookies in candidates
        ]
        if validate and candidates:
            validation_candidates = [(name, cookies) for name, cookies in candidates if cookies.get("__Secure-1PSID")]
            validation = CookieManager._validate_cookie_candidate_profiles(validation_candidates)
            validation_by_profile = {item["profile"]: item for item in validation}
            for profile in profiles:
                profile.update(validation_by_profile.get(profile["profile"], {}))
        return profiles

    @staticmethod
    def _read_cookie_jar(cookie_jar, cookie_names: set[str]) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        for cookie in cookie_jar:
            if cookie.name not in cookie_names or not cookie.value:
                continue
            if cookie.domain != "google.com" and not cookie.domain.endswith(".google.com"):
                continue
            cookies[cookie.name] = cookie.value
        return cookies

    @staticmethod
    def _chrome_base_path() -> Optional[Path]:
        """返回当前平台的 Chrome 用户数据目录路径。"""
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support/Google/Chrome"
        elif sys.platform == "win32":
            return Path.home() / "AppData/Local/Google/Chrome/User Data"
        else:
            return Path.home() / ".config/google-chrome"

    @staticmethod
    def _browser_cookie_candidates(
        browser_cookie3,
        browser: str,
        cookie_function,
        cookie_names: set[str],
        require_psid: bool = True,
    ) -> list[tuple[str, Dict[str, str]]]:
        if browser != "chrome":
            return []

        base = CookieManager._chrome_base_path()
        if base is None:
            return []
        paths = [base / "Default/Cookies", *sorted(base.glob("Profile */Cookies"))]
        candidates: list[tuple[str, Dict[str, str]]] = []
        try:
            cookies = CookieManager._read_cookie_jar(cookie_function(domain_name="google.com"), cookie_names)
            if cookies.get("__Secure-1PSID"):
                candidates.append(("auto", cookies))
        except Exception as e:
            logger.debug("跳过 Chrome auto cookie: %s", e)

        for path in paths:
            if not path.exists():
                continue
            try:
                cookies = CookieManager._read_cookie_jar(
                    browser_cookie3.chrome(cookie_file=str(path), domain_name="google.com"),
                    cookie_names,
                )
            except Exception as e:
                logger.debug("跳过 Chrome profile cookie %s: %s", path.parent.name, e)
                continue
            if cookies.get("__Secure-1PSID") or not require_psid:
                candidates.append((path.parent.name, cookies))

        if not candidates:
            return []
        return candidates

    @staticmethod
    def _chrome_selected_profile_directory() -> str:
        base = CookieManager._chrome_base_path()
        if base is None:
            return ""
        local_state = base / "Local State"
        try:
            data = json.loads(local_state.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("无法读取 Chrome Local State %s: %s", local_state, e)
            return ""
        profile_info = data.get("profile") if isinstance(data, dict) else {}
        if not isinstance(profile_info, dict):
            return ""
        selected = profile_info.get("last_used") or profile_info.get("last_active_profiles")
        if isinstance(selected, str):
            return selected
        if isinstance(selected, list) and selected and isinstance(selected[0], str):
            return selected[0]
        return ""

    @staticmethod
    def _select_valid_cookie_candidate(candidates: list[tuple[str, Dict[str, str]]]) -> Dict[str, str]:
        if len(candidates) == 1:
            return candidates[0][1]

        result: dict[str, Dict[str, str]] = {}

        def validate_worker() -> None:
            result["cookies"] = asyncio.run(CookieManager._validate_cookie_candidates_async(candidates))

        thread = threading.Thread(target=validate_worker)
        thread.start()
        thread.join(timeout=45)
        cookies = result.get("cookies")
        if cookies:
            return cookies

        logger.warning("⚠️ 无法验证 Chrome profile，使用第一个可读取的 Cookie 候选")
        return candidates[0][1]

    @staticmethod
    def _select_named_cookie_candidate(candidates: list[tuple[str, Dict[str, str]]], profile: str) -> Dict[str, str]:
        requested = profile.strip().lower()
        for profile_name, cookies in candidates:
            if profile_name.lower() == requested:
                logger.info("✅ 使用指定 Chrome profile: %s", profile_name)
                return cookies
        logger.warning("⚠️ 未找到指定 Chrome profile: %s", profile)
        return {}

    @staticmethod
    def _validate_cookie_candidate_profiles(candidates: list[tuple[str, Dict[str, str]]]) -> list[dict[str, object]]:
        result: dict[str, list[dict[str, object]]] = {}

        def validate_worker() -> None:
            result["profiles"] = asyncio.run(CookieManager._validate_cookie_candidate_profiles_async(candidates))

        thread = threading.Thread(target=validate_worker)
        thread.start()
        thread.join(timeout=45)
        return result.get("profiles", [])

    @staticmethod
    async def _validate_cookie_candidates_async(candidates: list[tuple[str, Dict[str, str]]]) -> Dict[str, str]:
        try:
            from gemini_webapi import GeminiClient
            from gemini_webapi.constants import AccountStatus
        except ImportError as e:
            logger.warning("gemini_webapi 不可用，跳过 Chrome profile Cookie 验证: %s", e)
            return {}

        first_available: Dict[str, str] = {}
        for profile_name, cookies in candidates:
            client = GeminiClient(cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS", ""))
            client.cookies = cookies
            try:
                await client.init(timeout=15, auto_close=False, auto_refresh=False)
                if getattr(client, "account_status", None) != AccountStatus.AVAILABLE:
                    logger.debug("Chrome profile %s 的 Gemini 账号状态不可用", profile_name)
                    continue
                if not first_available:
                    first_available = cookies
                scheduled_count = await CookieManager._probe_scheduled_registry_count(client)
                if scheduled_count > 0:
                    logger.info("✅ Chrome profile %s 的 Gemini Cookie 验证通过，定时操作 %s 条", profile_name, scheduled_count)
                    return cookies
                logger.debug("Chrome profile %s 可用，但定时操作 registry 为空", profile_name)
            except Exception as e:
                logger.debug("Chrome profile %s 的 Gemini Cookie 验证失败: %s", profile_name, e)
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
        if first_available:
            logger.info("✅ Chrome profile Cookie 验证通过，但未发现定时操作 registry，使用首个可用账号")
        return first_available

    @staticmethod
    async def _validate_cookie_candidate_profiles_async(candidates: list[tuple[str, Dict[str, str]]]) -> list[dict[str, object]]:
        try:
            from gemini_webapi import GeminiClient
            from gemini_webapi.constants import AccountStatus
        except Exception as e:
            return [{"profile": profile_name, "validation_error": str(e)} for profile_name, _ in candidates]

        profiles: list[dict[str, object]] = []
        for profile_name, cookies in candidates:
            info: dict[str, object] = {
                "profile": profile_name,
                "account_available": False,
                "scheduled_registry_count": 0,
            }
            client = GeminiClient(cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS", ""))
            client.cookies = cookies
            try:
                await client.init(timeout=15, auto_close=False, auto_refresh=False)
                status = getattr(client, "account_status", None)
                info["account_status"] = str(status)
                info["account_available"] = status == AccountStatus.AVAILABLE
                if info["account_available"]:
                    info["scheduled_registry_count"] = await CookieManager._probe_scheduled_registry_count(client)
                    info["language"] = getattr(client, "language", "")
                    info["build_label"] = getattr(client, "build_label", "")
            except Exception as e:
                info["validation_error"] = str(e)
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
            profiles.append(info)
        return profiles

    @staticmethod
    async def _probe_scheduled_registry_count(client) -> int:
        try:
            from gemini_webapi.types import RPCData
            from gemini_webapi.utils import extract_json_from_response, get_nested_value
        except ImportError as e:
            logger.warning("gemini_webapi 不可用，无法探测定时操作 registry: %s", e)
            return 0

        previous_language = getattr(client, "language", None)
        try:
            client.language = "zh-CN"
            response = await client._batch_execute(
                [RPCData("XPSWpd", "[]")],
                source_path="/scheduled",
                close_on_error=False,
            )
            for part in extract_json_from_response(response.text):
                if get_nested_value(part, [0]) != "wrb.fr":
                    continue
                if get_nested_value(part, [1]) != "XPSWpd":
                    continue
                body = get_nested_value(part, [2])
                if isinstance(body, str):
                    parsed = json.loads(body)
                    return len(parsed[0]) if isinstance(parsed, list) and parsed and isinstance(parsed[0], list) else 0
        except Exception as e:
            logger.debug("Chrome profile 定时操作 registry 探测失败: %s", e)
        finally:
            if previous_language:
                client.language = previous_language
        return 0

    @staticmethod
    def get_cookie_from_browser(browser: str = "chrome", profile: str = "") -> Tuple[Optional[str], Optional[str]]:
        """
        从浏览器自动获取 Cookie

        Args:
            browser: 浏览器类型 (chrome, firefox, edge, opera, brave)

        Returns:
            (psid, psidts) 元组
        """
        cookies = CookieManager.get_cookies_from_browser(browser, profile=profile)
        return cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS")

    def update_cookie(
        self,
        psid: str,
        psidts: str = "",
        source: str = "manual",
        extra_cookies: Optional[Dict[str, str]] = None,
    ) -> bool:
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
                extra_cookies=extra_cookies or self._load_extra_cookies_from_env(psid, psidts),
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

    def get_cookie_status(self) -> Tuple[CookieStatus, Dict[str, Any]]:
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
