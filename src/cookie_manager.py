"""
Cookie 管理模块 - 自动刷新、监控和浏览器Cookie获取
"""

import asyncio
from contextlib import contextmanager
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from .sdk_auth_logging import suppress_sdk_auth_logs

logger = logging.getLogger(__name__)
_BROWSER_COOKIE_ACCESS_LOCK = threading.Lock()
_SUPPORTED_BROWSERS = ("chrome", "chrome-beta", "edge", "brave", "firefox", "opera")


class _BrowserCookieAccessTimeout(BaseException):
    """Stop browser profile enumeration even when the dependency swallows Exception."""


def _browser_cookie_timeout_seconds(value: Optional[float] = None) -> float:
    raw_value: object = value if value is not None else os.environ.get("GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS", "15")
    try:
        timeout = float(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        timeout = 15.0
    if not math.isfinite(timeout):
        timeout = 15.0
    return min(max(timeout, 0.01), 120.0)


@contextmanager
def _bounded_macos_keychain_reader(browser_cookie3: object, timeout_seconds: float) -> Iterator[None]:
    """Bound browser-cookie3's unbounded macOS ``security`` subprocess call."""
    original_reader = getattr(browser_cookie3, "_get_osx_keychain_password", None)
    if sys.platform != "darwin" or not callable(original_reader):
        yield
        return

    default_password = getattr(browser_cookie3, "CHROMIUM_DEFAULT_PASSWORD", b"peanuts")

    def bounded_reader(osx_key_service: object, osx_key_user: object) -> bytes:
        command = [
            "/usr/bin/security",
            "-q",
            "find-generic-password",
            "-w",
            "-a",
            str(osx_key_user),
            "-s",
            str(osx_key_service),
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise _BrowserCookieAccessTimeout from error
        if completed.returncode != 0:
            return default_password
        return completed.stdout.strip()

    with _BROWSER_COOKIE_ACCESS_LOCK:
        setattr(browser_cookie3, "_get_osx_keychain_password", bounded_reader)
        try:
            yield
        finally:
            setattr(browser_cookie3, "_get_osx_keychain_password", original_reader)


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


@dataclass(frozen=True)
class BrowserCookieDiscovery:
    """Credentials for a verified browser session or a safe reason discovery failed."""

    cookies: dict[str, str] = field(default_factory=dict, repr=False)
    source: str | None = None
    reason: str | None = None


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
    def get_cookies_from_browser(
        browser: str = "chrome",
        profile: str = "",
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, str]:
        """Compatibility adapter. A named profile is a local extraction only."""
        if not profile:
            return CookieManager.discover_browser_cookies(browser, timeout_seconds=timeout_seconds).cookies
        try:
            candidates = CookieManager._read_browser_candidates(browser, timeout_seconds)
        except (Exception, _BrowserCookieAccessTimeout):
            return {}
        return CookieManager._select_named_cookie_candidate(candidates, profile)

    @staticmethod
    def discover_browser_cookies(
        browser: str = "chrome",
        profile: str = "",
        *,
        expected_psid: str | None = None,
        timeout_seconds: float | None = None,
    ) -> BrowserCookieDiscovery:
        """Find a locally readable browser Cookie that authenticates to Gemini."""
        try:
            import browser_cookie3  # noqa: F401 - distinguishes unavailable browser support
        except ImportError:
            return BrowserCookieDiscovery(reason="BROWSER_COOKIE_DEPENDENCY_MISSING")

        if browser not in _SUPPORTED_BROWSERS:
            return BrowserCookieDiscovery(reason="BROWSER_COOKIE_NOT_FOUND")
        attempt_browsers = [browser] if profile else [browser, *(name for name in _SUPPORTED_BROWSERS if name != browser)]
        reasons: set[str] = set()
        for attempt_browser in attempt_browsers:
            browser_reasons: set[str] = set()
            try:
                candidates = CookieManager._read_browser_candidates(attempt_browser, timeout_seconds, browser_reasons)
            except _BrowserCookieAccessTimeout:
                reasons.add("BROWSER_COOKIE_ACCESS_TIMEOUT")
                break
            except PermissionError:
                reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
                continue
            except Exception as error:
                logger.debug("Browser Cookie read failed browser=%s error_type=%s", attempt_browser, type(error).__name__)
                continue
            finally:
                reasons.update(browser_reasons)

            candidates = [(name, jar) for name, jar in candidates
                          if jar.get("__Secure-1PSID") and (not profile or name.casefold() == profile.strip().casefold())]
            if expected_psid is not None:
                matching = [(name, jar) for name, jar in candidates if jar["__Secure-1PSID"] == expected_psid]
                if candidates and not matching:
                    reasons.add("BROWSER_COOKIE_ACCOUNT_MISMATCH")
                candidates = matching
            if not candidates:
                continue

            # The generic library reader can return the same jar as a named
            # profile. Prefer the named profile so the diagnostic is precise.
            named = [jar for name, jar in candidates if name != "auto"]
            sources = [(f"browser_{attempt_browser}" + (f":{name}" if name != "auto" else ""), jar)
                       for name, jar in candidates if name != "auto" or jar not in named]
            result = CookieManager._validate_browser_candidates(sources)
            if result.cookies:
                return result
            if result.reason:
                reasons.add(result.reason)

        for reason in ("BROWSER_COOKIE_ACCESS_TIMEOUT", "BROWSER_COOKIE_AUTH_UNVERIFIED", "BROWSER_COOKIE_AUTH_REJECTED",
                       "BROWSER_COOKIE_ACCESS_DENIED", "BROWSER_COOKIE_ACCOUNT_MISMATCH"):
            if reason in reasons:
                return BrowserCookieDiscovery(reason=reason)
        return BrowserCookieDiscovery(reason="BROWSER_COOKIE_NOT_FOUND")

    @staticmethod
    def _read_browser_candidates(
        browser: str,
        timeout_seconds: float | None,
        reasons: set[str] | None = None,
        *,
        require_psid: bool = True,
    ) -> list[tuple[str, dict[str, str]]]:
        import browser_cookie3

        reader = getattr(browser_cookie3, "chrome" if browser == "chrome-beta" else browser, None)
        if not callable(reader):
            return []
        cookie_names = CookieManager._google_cookie_names()
        reasons = reasons if reasons is not None else set()
        with _bounded_macos_keychain_reader(browser_cookie3, _browser_cookie_timeout_seconds(timeout_seconds)):
            candidates = CookieManager._browser_cookie_candidates(
                browser_cookie3, browser, reader, cookie_names, require_psid=require_psid, reasons=reasons,
            )
            if not candidates and browser != "chrome-beta" and "BROWSER_COOKIE_ACCESS_DENIED" not in reasons:
                cookies = CookieManager._read_cookie_jar(reader(domain_name="google.com"), cookie_names)
                if cookies and (not require_psid or cookies.get("__Secure-1PSID")):
                    candidates = [("auto", cookies)]
        return candidates

    @staticmethod
    def list_browser_cookie_profiles(
        browser: str = "chrome",
        validate: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> list[dict[str, object]]:
        """Return browser cookie profile diagnostics without exposing cookie values."""
        try:
            import browser_cookie3  # noqa: F401 - distinguishes unavailable browser support
        except ImportError:
            return [{"browser": browser, "error": "browser-cookie3 not installed",
                     "error_code": "BROWSER_COOKIE_DEPENDENCY_MISSING"}]

        if browser not in _SUPPORTED_BROWSERS:
            return [{"browser": browser, "error": f"unsupported browser: {browser}"}]

        reasons: set[str] = set()
        try:
            candidates = CookieManager._read_browser_candidates(browser, timeout_seconds, reasons, require_psid=False)
        except PermissionError:
            reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
            candidates = []
        except _BrowserCookieAccessTimeout:
            return [
                {
                    "browser": browser,
                    "error": "Browser cookie access timed out while waiting for macOS Keychain.",
                    "error_code": "BROWSER_COOKIE_ACCESS_TIMEOUT",
                }
            ]
        except Exception as e:
            return [{"browser": browser, "error": "Browser Cookie access failed.", "error_code": "BROWSER_COOKIE_ACCESS_FAILED", "error_type": type(e).__name__}]

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
        if "BROWSER_COOKIE_ACCESS_DENIED" in reasons:
            profiles.append({"browser": browser, "error": "The operating system denied access to browser Cookie storage.",
                             "error_code": "BROWSER_COOKIE_ACCESS_DENIED"})
        return profiles

    @staticmethod
    def _read_cookie_jar(cookie_jar, cookie_names: set[str]) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        now = time.time()
        for cookie in cookie_jar:
            if cookie.name not in cookie_names or not cookie.value:
                continue
            if cookie.domain != "google.com" and not cookie.domain.endswith(".google.com"):
                continue
            expires = getattr(cookie, "expires", None)
            if isinstance(expires, (int, float)) and expires <= now:
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
    def _browser_base_paths(browser: str) -> list[Path]:
        """返回当前平台下指定浏览器的用户数据目录候选。"""
        home = Path.home()
        if sys.platform == "darwin":
            base = home / "Library/Application Support"
            paths = {
                "chrome": base / "Google/Chrome",
                "chrome-beta": base / "Google/Chrome Beta",
                "edge": base / "Microsoft Edge",
                "brave": base / "BraveSoftware/Brave-Browser",
                "opera": base / "com.operasoftware.Opera",
                "firefox": home / "Library/Application Support/Firefox/Profiles",
            }
        elif sys.platform == "win32":
            base = home / "AppData/Local"
            paths = {
                "chrome": base / "Google/Chrome/User Data",
                "chrome-beta": base / "Google/Chrome SxS/User Data",
                "edge": base / "Microsoft/Edge/User Data",
                "brave": base / "BraveSoftware/Brave-Browser/User Data",
                "opera": base / "Opera Software/Opera Stable",
                "firefox": home / "AppData/Roaming/Mozilla/Firefox/Profiles",
            }
        else:
            base = home / ".config"
            paths = {
                "chrome": base / "google-chrome",
                "chrome-beta": base / "google-chrome-beta",
                "edge": base / "microsoft-edge",
                "brave": base / "BraveSoftware/Brave-Browser",
                "opera": base / "opera",
                "firefox": home / ".mozilla/firefox",
            }
        return [p for p in [paths.get(browser)] if p is not None]

    @staticmethod
    def _browser_cookie_candidates(
        browser_cookie3,
        browser: str,
        cookie_function,
        cookie_names: set[str],
        require_psid: bool = True,
        reasons: set[str] | None = None,
    ) -> list[tuple[str, Dict[str, str]]]:
        """从指定浏览器枚举可用的 Cookie 候选。

        Chrome 使用 profile 目录枚举；Firefox/Edge/Brave/Opera 使用
        浏览器自带的 Cookie 数据库读取。

        Auto 候选始终要求 __Secure-1PSID（与历史行为一致），仅 profile
        候选在 require_psid=False 时允许无 PSID。
        """
        if browser == "chrome":
            base = CookieManager._chrome_base_path()
            bases = [base] if base else []
        else:
            bases = CookieManager._browser_base_paths(browser)

        if not bases:
            return []

        candidates: list[tuple[str, Dict[str, str]]] = []
        seen_profiles: set[str] = set()
        reasons = reasons if reasons is not None else set()

        for base in bases:
            try:
                base.stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            except PermissionError:
                reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
                continue

            # Chrome's generic reader may select a different Chrome channel.
            if browser != "chrome-beta":
                try:
                    cookies = CookieManager._read_cookie_jar(cookie_function(domain_name="google.com"), cookie_names)
                    if cookies.get("__Secure-1PSID") and "auto" not in seen_profiles:
                        seen_profiles.add("auto")
                        candidates.append(("auto", cookies))
                except PermissionError:
                    reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
                except Exception as e:
                    logger.debug("跳过 %s 自动 Cookie error_type=%s", browser, type(e).__name__)

            # Chrome 族浏览器：枚举 Profile 目录
            if browser in ("chrome", "chrome-beta", "edge", "brave", "opera"):
                try:
                    named_profiles = sorted(path for path in base.iterdir() if path.name.startswith("Profile "))
                except PermissionError:
                    reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
                    named_profiles = []
                for profile_dir in (base / "Default", *named_profiles):
                    profile_name = profile_dir.name
                    if profile_name in seen_profiles:
                        continue
                    for path in (profile_dir / "Network/Cookies", profile_dir / "Cookies"):
                        try:
                            # browser-cookie3 may translate an OS-denied read
                            # into a generic BrowserCookieError via SQLite.
                            with path.open("rb"):
                                pass
                            profile_cookies = CookieManager._read_cookie_jar(
                                (browser_cookie3.chrome if browser in {"chrome", "chrome-beta"} else cookie_function)(
                                    cookie_file=str(path), domain_name="google.com",
                                ), cookie_names,
                            )
                        except (FileNotFoundError, NotADirectoryError):
                            continue
                        except PermissionError:
                            reasons.add("BROWSER_COOKIE_ACCESS_DENIED")
                            continue
                        except Exception as e:
                            logger.debug("Skipping %s profile Cookie error_type=%s", browser, type(e).__name__)
                            continue
                        if profile_cookies.get("__Secure-1PSID") or not require_psid:
                            seen_profiles.add(profile_name)
                            candidates.append((profile_name, profile_cookies))
                            break

            # Firefox：Profiles 目录下的 cookies.sqlite
            elif browser == "firefox":
                for profile_dir in sorted(base.glob("*")):
                    if not profile_dir.is_dir():
                        continue
                    profile_name = profile_dir.name
                    if profile_name in seen_profiles:
                        continue
                    cookie_db = profile_dir / "cookies.sqlite"
                    if not cookie_db.exists():
                        continue
                    try:
                        import sqlite3

                        conn = sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True)
                        try:
                            cur = conn.execute(
                                "SELECT name, value FROM moz_cookies WHERE base_domain LIKE '%google.com'",
                            )
                            profile_cookies = {
                                row[0]: row[1]
                                for row in cur
                                if row[0] in cookie_names and row[1]
                            }
                        finally:
                            conn.close()
                    except Exception as e:
                        logger.debug("跳过 Firefox profile %s Cookie error_type=%s", profile_name, type(e).__name__)
                        continue
                    if profile_cookies.get("__Secure-1PSID") or not require_psid:
                        seen_profiles.add(profile_name)
                        candidates.append((profile_name, profile_cookies))

        if not candidates:
            return []
        if browser == "chrome":
            selected = CookieManager._chrome_selected_profile_directory()
            if selected:
                candidates.sort(key=lambda candidate: candidate[0] != selected)
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
            logger.debug("无法读取 Chrome Local State error_type=%s", type(e).__name__)
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
    def _validate_browser_candidates(
        candidates: list[tuple[str, dict[str, str]]],
    ) -> BrowserCookieDiscovery:
        result = BrowserCookieDiscovery(reason="BROWSER_COOKIE_AUTH_UNVERIFIED")

        def validate_worker() -> None:
            nonlocal result
            try:
                result = asyncio.run(asyncio.wait_for(CookieManager._verify_cookie_candidates(candidates), 40))
            except Exception as error:
                logger.debug("Browser Cookie verification unavailable error_type=%s", type(error).__name__)

        thread = threading.Thread(target=validate_worker, daemon=True)
        thread.start()
        thread.join(timeout=45)
        return result

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

        thread = threading.Thread(target=validate_worker, daemon=True)
        thread.start()
        thread.join(timeout=45)
        return result.get("profiles", [])

    @staticmethod
    async def _validate_cookie_candidates_async(candidates: list[tuple[str, Dict[str, str]]]) -> Dict[str, str]:
        return (await CookieManager._verify_cookie_candidates(candidates)).cookies

    @staticmethod
    def _make_browser_cookie_probe_client(cookies: dict[str, str]) -> Any:
        from gemini_webapi import GeminiClient
        from gemini_webapi.constants import AccountStatus
        from gemini_webapi.utils.get_access_token import (
            AsyncSession, CurlFollow, CurlHttpVersion, _extract_payload, _send_request,
        )

        from .client_manager import get_configured_proxy

        class BrowserCookieProbeClient(GeminiClient):
            async def init(self, *, timeout: float = 15, **_options: Any) -> None:
                # Reuse the SDK's exact-jar transport and frontend parser.
                # Its general bootstrap prefers a persistent cache and can
                # fall back to another jar. Neither verifies this candidate.
                candidate = self.cookies
                self.client = AsyncSession(impersonate=self.impersonate, proxy=self.proxy,
                                           allow_redirects=CurlFollow.SAFE, http_version=CurlHttpVersion.NONE,
                                           timeout=timeout)
                response = await _send_request(self.client, candidate, verbose=False)
                frontend = _extract_payload(response)
                if not frontend:
                    return
                self.access_token, self.build_label, self.session_id, language, _ = frontend
                if language:
                    self.language = language
                self._running = True
                await self._fetch_user_status()
                # The SDK treats a missing numeric status as AVAILABLE. A
                # positive probe also needs the model catalog from that RPC.
                if getattr(self, "account_status", None) == AccountStatus.AVAILABLE and not self.list_models():
                    setattr(self, "account_status", None)

            async def _batch_execute(self, *args: Any, **kwargs: Any) -> Any:
                kwargs.update(current_retry=0, close_on_error=False)
                return await super()._batch_execute(*args, **kwargs)

            async def close(self, delay: float = 0) -> None:
                # Profile probes must not overwrite a persistent SDK cache.
                self._running = False
                await super().close(delay)

        client = BrowserCookieProbeClient(cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS", ""),
                                          proxy=get_configured_proxy())
        client.cookies = cookies
        client.account_status = None  # The SDK starts with a synthetic AVAILABLE default.
        return client

    @staticmethod
    def _browser_auth_reason(client: Any) -> str | None:
        from gemini_webapi.constants import AccountStatus

        status = getattr(client, "account_status", None)
        if status is None:
            return "BROWSER_COOKIE_AUTH_UNVERIFIED"
        if status != AccountStatus.AVAILABLE:
            return "BROWSER_COOKIE_AUTH_REJECTED"
        return None

    @staticmethod
    async def _verify_cookie_candidates(candidates: list[tuple[str, dict[str, str]]]) -> BrowserCookieDiscovery:
        from gemini_webapi.exceptions import AuthError

        reasons: set[str] = set()
        for source, cookies in candidates:
            client = None
            try:
                client = CookieManager._make_browser_cookie_probe_client(cookies)
                suppress_sdk_auth_logs()
                await asyncio.wait_for(client.init(timeout=15, auto_close=False, auto_refresh=False, verbose=False), 15)
                reason = CookieManager._browser_auth_reason(client)
                if not reason:
                    return BrowserCookieDiscovery(cookies=cookies, source=source)
                reasons.add(reason)
            except AuthError:
                reasons.add("BROWSER_COOKIE_AUTH_REJECTED")
            except Exception as e:
                reasons.add("BROWSER_COOKIE_AUTH_UNVERIFIED")
                logger.debug("Browser Cookie verification failed error_type=%s", type(e).__name__)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception as close_err:
                        logger.debug("关闭验证用 client 失败 error_type=%s", type(close_err).__name__)
        reason = ("BROWSER_COOKIE_AUTH_REJECTED" if reasons == {"BROWSER_COOKIE_AUTH_REJECTED"}
                  else "BROWSER_COOKIE_AUTH_UNVERIFIED")
        return BrowserCookieDiscovery(reason=reason)

    @staticmethod
    async def _validate_cookie_candidate_profiles_async(candidates: list[tuple[str, Dict[str, str]]]) -> list[dict[str, object]]:
        try:
            import gemini_webapi  # noqa: F401 - keep this diagnostic usable without the SDK
        except Exception as e:
            return [{"profile": profile_name, "validation_error": "Cookie validation is unavailable.", "error_type": type(e).__name__} for profile_name, _ in candidates]

        profiles: list[dict[str, object]] = []
        for profile_name, cookies in candidates:
            info: dict[str, object] = {
                "profile": profile_name,
                "account_available": False,
                "scheduled_registry_count": 0,
            }
            client = None
            try:
                client = CookieManager._make_browser_cookie_probe_client(cookies)
                suppress_sdk_auth_logs()
                await asyncio.wait_for(client.init(timeout=15, auto_close=False, auto_refresh=False, verbose=False), 15)
                status = getattr(client, "account_status", None)
                info["account_status"] = str(status)
                reason = CookieManager._browser_auth_reason(client)
                info["account_available"] = reason is None
                if reason:
                    info["error_code"] = reason
                if info["account_available"]:
                    info["scheduled_registry_count"] = await CookieManager._probe_scheduled_registry_count(client)
                    info["language"] = getattr(client, "language", "")
                    info["build_label"] = getattr(client, "build_label", "")
            except Exception as e:
                info["validation_error"] = "Cookie validation failed."
                info["error_type"] = type(e).__name__
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception as close_err:
                        logger.debug("关闭 profile 验证 client 失败 error_type=%s", type(close_err).__name__)
            profiles.append(info)
        return profiles

    @staticmethod
    async def _probe_scheduled_registry_count(client) -> int:
        try:
            from gemini_webapi.types import RPCData
            from .infrastructure.rpc_contracts import get_contract
            from .infrastructure.rpc_parsers import extract_rpc_bodies, parse_contract_body
        except ImportError as e:
            logger.warning("gemini_webapi 不可用，无法探测定时操作 registry error_type=%s", type(e).__name__)
            return 0

        previous_language = getattr(client, "language", None)
        try:
            contract = get_contract("scheduled.registry")
            client.language = "zh-CN"
            response = await client._batch_execute(
                [RPCData(contract.rpc_id, contract.build_payload())],
                source_path=contract.source_path,
                close_on_error=False,
            )
            bodies = extract_rpc_bodies(response.text, contract.rpc_id)
            parsed = parse_contract_body(contract, bodies[0] if bodies else [])
            return len(parsed.value) if isinstance(parsed.value, list) else 0
        except Exception as e:
            logger.debug("Chrome profile 定时操作 registry 探测失败 error_type=%s", type(e).__name__)
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
        notify: bool = True,
        guard: Optional[Callable[[], bool]] = None,
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
            if guard is not None and not guard():
                return False
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
            
            if notify and self.on_cookie_update:
                try:
                    self.on_cookie_update(self._cookie_data)
                except Exception as e:
                    logger.error("❌ Cookie 更新回调失败 error_type=%s", type(e).__name__)
            
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
                    return self.update_cookie(psid, psidts or "", source=f"browser_{browser}")
            
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
                logger.error("❌ Cookie 监控出错 error_type=%s", type(e).__name__)
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
