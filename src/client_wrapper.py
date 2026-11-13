"""
Gemini 客户端封装 - 门面模式
提供统一的向后兼容接口，内部委托给专门的管理类
"""

import inspect
import asyncio
import time
from contextlib import asynccontextmanager, suppress
import logging
import os
from collections.abc import Mapping
from functools import wraps
from typing import Any, Dict, Optional, Self

import anyio

from .client_manager import (
    ClientAuthenticationRequired,
    ClientManager,
    browser_cookie_auto_load_enabled,
    get_configured_proxy,  # noqa: F401  (re-exported as public facade API)
    get_default_chat_retention_seconds,
    validate_config,  # noqa: F401  (re-exported as public facade API)
)
from .client_manager import (
    prepare_browser_cookie_cache as _prepare_browser_cookie_cache,
)
from .domain import (
    CleanupObservation,
    CleanupState,
    DomainResult,
    new_diagnostic_id,
    is_valid_remote_chat_id,
)
from .remote_chat_cleanup_manager import RemoteChatCleanupManager
from .services.lifecycle import (
    ConversationLifecycleService,
    LifecycleResetAllData,
)
from .session_manager import SessionData, SessionOperationResult, SessionService
import threading
from .infrastructure.state_store import authentication_scope, get_default_state_store
from contextvars import ContextVar

# --- ported from main (v0.2.2) for the rebased branch ---
async def finalize_generated_chat_cleanup(
    response: Any,
    *,
    owns_chat: bool,
    retain_chat: bool = False,
    preserve_for_recovery: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
    client: Any = None,
) -> CleanupObservation:
    """Finalize a newly generated chat after artifact verification or recovery policy."""
    return await _lifecycle_service.finalize_generated_chat(
        response,
        owns_chat=owns_chat,
        retain_chat=retain_chat,
        preserve_for_recovery=preserve_for_recovery,
        delete_after_seconds=delete_after_seconds,
        source=source,
        client=client,
        client_initializer=_initialize_cleanup_client if client is None else None,
        authentication_generation=_request_cleanup_generation(),
    )

def _request_cleanup_generation() -> int | None:
    request_context = _request_authentication_generation.get()
    current_context = _cleanup_context()
    if request_context is not None and current_context is not None and request_context[0] == current_context[0]:
        return request_context[1]
    return None

def _bind_cleanup_authentication(client: Any) -> None:
    if not isinstance(_cleanup_manager, RemoteChatCleanupManager):
        return
    cookies = getattr(client, "cookies", None)
    getter = getattr(cookies, "get", None)
    psid = getter("__Secure-1PSID") if callable(getter) else None
    if not isinstance(psid, str):
        psid = os.environ.get("GEMINI_PSID", "")
    store = get_default_state_store()
    _cleanup_manager.bind_authentication_scope(authentication_scope(psid, store=store), state_store=store)

_request_authentication_generation: ContextVar[tuple[str, int] | None] = ContextVar(
    "gemini_authentication_generation", default=None,
)

_authentication_lock = threading.RLock()

async def _initialize_cleanup_client() -> Any:
    """Resolve a current initialized generation inside the cleanup error boundary."""
    client = get_gemini_client()
    initialized = await initialize_client()
    return initialized if initialized is not None else client

def _cleanup_context() -> tuple[str, int] | None:
    if isinstance(_cleanup_manager, RemoteChatCleanupManager):
        return _cleanup_manager.authentication_context()
    return None


logger = logging.getLogger(__name__)

try:
    from .cookie_manager import CookieData, get_cookie_manager, init_cookie_manager
    COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    COOKIE_MANAGER_AVAILABLE = False
    logger.warning("cookie_manager 模块不可用")

# 全局管理器实例
_client_manager = ClientManager()
_session_manager = SessionService()
_cleanup_manager = RemoteChatCleanupManager(
    client_provider=lambda: _client_manager.get_client(),
    retention_provider=get_default_chat_retention_seconds,
)
_lifecycle_service = ConversationLifecycleService(
    session_provider=lambda: _session_manager,
    cleanup_provider=lambda: _cleanup_manager,
)


class _AttributeMapping(dict[str, Any]):
    """Keep mapping semantics while exposing fields to legacy attribute readers."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _adapt_history_value(value: Any) -> Any:
    """Recursively adapt mapping-backed history values without changing objects."""

    if isinstance(value, _AttributeMapping):
        return value
    if isinstance(value, Mapping):
        return _AttributeMapping({key: _adapt_history_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_adapt_history_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_adapt_history_value(item) for item in value)
    return value


def _install_history_compatibility(client: Any) -> Any:
    """Normalize only history-return values while preserving the client identity."""

    if getattr(client, "_gemini_mcp_history_compatible", False):
        return client

    list_chats = getattr(client, "list_chats", None)
    if callable(list_chats):
        @wraps(list_chats)
        def list_chats_compatible(*args: Any, **kwargs: Any) -> Any:
            return _adapt_history_value(list_chats(*args, **kwargs))

        try:
            setattr(client, "list_chats", list_chats_compatible)
        except (AttributeError, TypeError):
            logger.debug("Could not install list_chats mapping compatibility on %s", type(client).__name__)

    read_chat = getattr(client, "read_chat", None)
    if callable(read_chat):
        @wraps(read_chat)
        async def read_chat_compatible(*args: Any, **kwargs: Any) -> Any:
            result = read_chat(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return _adapt_history_value(result)

        try:
            setattr(client, "read_chat", read_chat_compatible)
        except (AttributeError, TypeError):
            logger.debug("Could not install read_chat mapping compatibility on %s", type(client).__name__)

    try:
        setattr(client, "_gemini_mcp_history_compatible", True)
    except (AttributeError, TypeError):
        pass
    return client


class ScheduledCleanupChatId(str):
    """String-compatible chat ID carrying its structured scheduling evidence."""

    cleanup_observation: CleanupObservation

    def __new__(
        cls,
        value: str,
        observation: CleanupObservation,
    ) -> Self:
        instance = super().__new__(cls, value)
        instance.cleanup_observation = observation
        return instance


def _session_data_to_dict(data: Optional[SessionData]) -> Optional[Dict[str, Any]]:
    """将 SessionData 转换为字典以保持向后兼容"""
    if data is None:
        return None
    return {
        "session": data.session,
        "session_id": data.session_id,
        "model": data.model,
        "thinking_level": data.thinking_level,
        "learning_mode": data.learning_mode,
        "temporary": data.temporary,
        "created_at": data.created_at,
        "updated_at": data.updated_at,
        "upstream_chat_id": data.upstream_chat_id,
        "retain_chat": data.retain_chat,
        "delete_after_seconds": data.delete_after_seconds,
        "lifecycle_state": data.lifecycle_state.value,
    }


# ============ 客户端管理接口 ============

def get_gemini_client() -> Any:
    """获取 GeminiClient，并规范化混合对象/映射历史返回值。"""
    return _install_history_compatibility(_client_manager.get_client())


async def initialize_client() -> Any:
    """完成客户端初始化"""
    return await _client_manager.initialize()


def reset_client() -> None:
    """重置客户端"""
    _client_manager.reset()
    _lifecycle_service.clear_sessions()


async def reset_client_async() -> Optional[DomainResult[LifecycleResetAllData]]:
    """重置客户端并等待旧连接关闭。"""
    reset_result: Optional[DomainResult[LifecycleResetAllData]] = None
    if isinstance(_session_manager, SessionService):
        reset_result = await _lifecycle_service.reset_all(
            delete_callback=lambda cid: delete_remote_chat(cid),
        )
    else:
        _lifecycle_service.clear_sessions()
    await _client_manager.reset_async()
    return reset_result


# ============ 会话管理接口 ============

def create_session(
    session: Any,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    temporary: bool = False,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
) -> SessionOperationResult:
    """创建带不可碰撞本地 ID 的共享会话。"""
    return _lifecycle_service.create_session(
        session,
        model,
        thinking_level=thinking_level,
        learning_mode=learning_mode,
        temporary=temporary,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
    )


def store_session(
    session_id: str,
    session: Any,
    model: str = "flash",
    thinking_level: str = "standard",
    learning_mode: Optional[str] = None,
    temporary: bool = False,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
) -> None:
    """存储会话"""
    _lifecycle_service.store_session(
        session_id,
        session,
        model,
        thinking_level=thinking_level,
        learning_mode=learning_mode,
        temporary=temporary,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
    )


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取存储的会话"""
    return _session_data_to_dict(_lifecycle_service.get_session(session_id))


def lookup_session(session_id: str) -> SessionOperationResult:
    """查找共享会话并保留显式 SESSION_NOT_FOUND 结果。"""
    return _lifecycle_service.lookup_session(session_id)


async def send_session_message(session_id: str, **request_kwargs: Any) -> SessionOperationResult:
    """通过共享会话服务串行发送消息。"""
    return await _lifecycle_service.send_message(session_id, **request_kwargs)


async def send_session_message_stream(session_id: str, **request_kwargs: Any) -> SessionOperationResult:
    """通过共享会话服务串行消费一次流式消息。"""
    return await _lifecycle_service.send_message_stream(session_id, **request_kwargs)


async def reset_session(session_id: str) -> SessionOperationResult:
    """只重置指定会话，并按保留策略清理其远端聊天。"""
    return await _lifecycle_service.reset_one(
        session_id,
        delete_callback=lambda cid: delete_remote_chat(cid),
    )


def remove_session(session_id: str) -> None:
    """移除会话"""
    _lifecycle_service.remove_session(session_id)


def pop_session(session_id: str) -> Optional[Dict[str, Any]]:
    """移除并返回会话数据。"""
    return _session_data_to_dict(_lifecycle_service.pop_session(session_id))


def clear_sessions() -> None:
    """清空所有会话"""
    _lifecycle_service.clear_sessions()


def cleanup_expired_sessions() -> None:
    """清理过期会话。"""
    _lifecycle_service.cleanup_expired_sessions()


def list_sessions() -> Dict[str, Dict[str, Any]]:
    """获取所有会话"""
    sessions = _lifecycle_service.list_sessions()
    return {
        sid: _session_data_to_dict(data)  # type: ignore[misc]  # data 非 None 时返回 Dict
        for sid, data in sessions.items()
        if data is not None
    }


# ============ 远程聊天清理接口 ============

def schedule_remote_chat_cleanup_from_response(
    response: Any,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
) -> Optional[str]:
    """登记 response 产生的远端 chat，默认稍后自动删除。"""
    observation = _lifecycle_service.schedule_cleanup_from_response(
        response,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
    )
    if observation.upstream_chat_id is None:
        return None
    return ScheduledCleanupChatId(
        observation.upstream_chat_id,
        observation,
    )


def schedule_remote_chat_cleanup(
    cid: Optional[str],
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
    reschedule_pending: bool = False,
    _owner_account: str | None = None,
) -> CleanupObservation:
    """登记远端 Gemini chat 的自动删除任务。"""
    if _owner_account is not None:
        return _cleanup_manager.schedule_cleanup_result(
            cid, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
            source=source, reschedule_pending=reschedule_pending, _owner_account=_owner_account,
        )
    return _lifecycle_service.schedule_cleanup(
        cid,
        retain_chat=retain_chat,
        delete_after_seconds=delete_after_seconds,
        source=source,
        reschedule_pending=reschedule_pending,
    )


async def delete_remote_chat(cid: Optional[str], client: Any = None) -> bool:
    """立即删除远端 Gemini chat。"""
    observation = await delete_remote_chat_result(cid, client=client)
    return observation.state in {
        CleanupState.COMPLETED,
        CleanupState.ALREADY_COMPLETED,
    }


async def finalize_remote_chat_cleanup(
    cid: Optional[str],
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
    client: Any = None,
    *,
    expected_account: str | None = None,
) -> CleanupObservation:
    """Apply explicit retention or await the default immediate verified deletion."""
    delay = get_default_chat_retention_seconds() if delete_after_seconds is None else max(0, delete_after_seconds)
    owner = expected_account or _cleanup_manager._account()
    if not owner and is_valid_remote_chat_id(cid):
        return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid, source=source)
    try:
        if retain_chat or delay > 0 or not is_valid_remote_chat_id(cid):
            return schedule_remote_chat_cleanup(
                cid, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds, source=source,
                reschedule_pending=True, _owner_account=owner,
            )
        # Apply the latest explicit policy synchronously without launching a
        # competing delete task that might use a different client instance.
        observation = _cleanup_manager.schedule_cleanup_result(
            cid, retain_chat=False, delete_after_seconds=0, source=source, dispatch=False,
            reschedule_pending=True, _owner_account=owner,
        )
        if _cleanup_manager._account_key_provider() != owner:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid, source=source)
        if observation.state in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED, CleanupState.FAILED}:
            return observation
        return await delete_remote_chat_result(cid, client=client, expected_account=owner)
    except Exception as error:
        try:
            return _cleanup_manager.record_cleanup_failure(cid, error, source=source, expected_account=owner)
        except Exception as persistence_error:
            diagnostic = new_diagnostic_id()
            logger.warning("Cleanup metadata not persisted diagnostic_id=%s error_type=%s",
                           diagnostic, type(persistence_error).__name__)
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid,
                                      attempts=1, diagnostic_id=diagnostic, source=source)


async def finalize_remote_chat_cleanup_from_response(
    response: Any,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    source: str = "",
    client: Any = None,
    *,
    expected_account: str | None = None,
) -> CleanupObservation:
    """Finish a one-shot workflow only after its artifacts have been consumed."""
    from .remote_chat_cleanup_manager import extract_remote_chat_id

    return await finalize_remote_chat_cleanup(
        extract_remote_chat_id(response), retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
        source=source, client=client, expected_account=expected_account,
    )


async def delete_remote_chat_result(
    cid: Optional[str],
    client: Any = None,
    *,
    expected_account: str | None = None,
) -> CleanupObservation:
    """Immediately delete a chat and retain structured lifecycle evidence."""
    if not is_valid_remote_chat_id(cid):
        return CleanupObservation(
            state=(CleanupState.INVALID_ID if cid is not None else CleanupState.NOT_APPLICABLE),
        )
    manager = _cleanup_manager
    if isinstance(manager, RemoteChatCleanupManager):
        expected_account = expected_account or manager._account()
        if expected_account is not None and manager._account_key_provider() != expected_account:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
    if client is None:
        await initialize_client()
        if (isinstance(manager, RemoteChatCleanupManager) and expected_account is not None
                and manager._account_key_provider() != expected_account):
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        client = get_gemini_client()
    return await _lifecycle_service.delete_chat_result(cid, client=client, expected_account=expected_account)


async def cleanup_due_remote_chats(client: Any = None) -> int:
    """清理已经到期的远端 Gemini chat。"""
    if client is None:
        client = get_gemini_client()
        await initialize_client()
    return await _lifecycle_service.cleanup_due_chats(client=client)


def list_pending_remote_chat_cleanup() -> Dict[str, Dict[str, Any]]:
    """返回待自动删除的远端 chat。"""
    pending = _cleanup_manager.list_pending_cleanup()
    return {
        cid: {
            "delete_at": data.delete_at,
            "source": data.source,
            "attempts": data.attempts,
            "diagnostic_id": data.last_diagnostic_id,
        }
        for cid, data in pending.items()
    }


async def _drain_due_cleanup() -> None:
    cleanup_expired_sessions()
    pending = _cleanup_manager.list_pending_cleanup()
    if not any(task.delete_at <= time.time() for task in pending.values()):
        return
    try:
        await cleanup_due_remote_chats()
    except Exception as error:
        logger.warning("Deferred cleanup could not run error_type=%s", type(error).__name__)


@asynccontextmanager
async def server_lifespan(_server: Any):
    """Resume durable cleanup while serving and finish active sessions on exit."""
    async def maintain() -> None:
        while True:
            try:
                from .infrastructure.operation_store import OperationStore
                from .services.background import maintain_background_operations, repair_operation_cleanup
                await maintain_background_operations()
                repair_operation_cleanup(
                    OperationStore(), _cleanup_manager.get_cleanup_observation, schedule_remote_chat_cleanup,
                )
                await _drain_due_cleanup()
            except Exception as error:
                logger.warning("Cleanup queue remains unverified error_type=%s", type(error).__name__)
            await asyncio.sleep(15)

    maintenance = asyncio.create_task(maintain())
    try:
        yield {}
    finally:
        maintenance.cancel()
        # Stdio EOF normally cancels the serving scope; shield finalization so
        # a short-lived caller cannot discard its non-retained conversations.
        with anyio.CancelScope(shield=True):
            deadline = asyncio.get_running_loop().time() + 30
            try:
                with suppress(asyncio.CancelledError):
                    await _await_shutdown_step(maintenance, deadline)
                if asyncio.get_running_loop().time() < deadline:
                    from .services.task_browser_sessions import close_task_browser_sessions
                    await _await_shutdown_step(close_task_browser_sessions(), deadline)
                if asyncio.get_running_loop().time() < deadline:
                    await _await_shutdown_step(
                        _lifecycle_service.reset_all(delete_callback=delete_remote_chat_result), deadline,
                    )
                if asyncio.get_running_loop().time() < deadline:
                    await _await_shutdown_step(_drain_due_cleanup(), deadline)
            except Exception as error:
                logger.warning("Shutdown cleanup remains unverified error_type=%s", type(error).__name__)
            finally:
                if asyncio.get_running_loop().time() < deadline:
                    await _await_shutdown_step(_client_manager.reset_async(), deadline)


async def _await_shutdown_step(awaitable: Any, deadline: float) -> bool:
    """Honor one shared deadline even if an upstream coroutine ignores cancel."""
    task = asyncio.ensure_future(awaitable)
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    done, _ = await asyncio.wait({task}, timeout=remaining)
    if done:
        await task
        return True
    task.cancel()

    def consume(finished: asyncio.Future) -> None:
        with suppress(BaseException):
            finished.result()

    task.add_done_callback(consume)
    logger.warning("Shutdown deadline reached; cleanup remains in the durable queue")
    return False


# ============ Cookie 管理接口 ============

def _on_cookie_update(cookie_data: CookieData) -> None:
    """Cookie 更新回调"""
    logger.info("🔄 Cookie 已更新，重置客户端...")
    os.environ["GEMINI_PSID"] = cookie_data.psid
    if cookie_data.psidts:
        os.environ["GEMINI_PSIDTS"] = cookie_data.psidts
    else:
        os.environ.pop("GEMINI_PSIDTS", None)
    reset_client()


def init_cookie_manager_integration() -> None:
    """初始化 Cookie Manager 集成"""
    if not COOKIE_MANAGER_AVAILABLE:
        return
    auto_refresh = os.environ.get("GEMINI_AUTO_REFRESH", "true").lower() == "true"
    cookie_manager = init_cookie_manager(auto_refresh=auto_refresh, on_cookie_update=_on_cookie_update)
    if auto_refresh:
        cookie_manager.start_monitor()
    logger.info("✅ Cookie Manager 集成已初始化")


def get_cookie_from_browser(browser: str = "chrome", profile: str = "") -> bool:
    """从浏览器获取 Cookie"""
    if not COOKIE_MANAGER_AVAILABLE:
        logger.error("❌ Cookie Manager 不可用")
        return False
    cm = get_cookie_manager()
    discovery = cm.discover_browser_cookies(browser, profile=profile)
    cookies = discovery.cookies
    _client_manager.browser_auth_reason = discovery.reason
    psid = cookies.get("__Secure-1PSID")
    psidts = cookies.get("__Secure-1PSIDTS", "")
    if psid:
        source = discovery.source or f"browser_{browser}"
        success = cm.update_cookie(
            psid,
            psidts,
            source=source,
            extra_cookies=cookies,
        )
        if success:
            os.environ["GEMINI_PSID"] = psid
            if psidts:
                os.environ["GEMINI_PSIDTS"] = psidts
            else:
                os.environ.pop("GEMINI_PSIDTS", None)
            logger.info("✅ 已从浏览器获取 Cookie 并更新")
        return success
    return False


def list_browser_cookie_profiles(browser: str = "chrome", validate: bool = True) -> list[dict[str, Any]]:
    """List local browser cookie profile diagnostics without exposing cookie values."""
    if not COOKIE_MANAGER_AVAILABLE:
        return [{"browser": browser, "error": "Cookie Manager unavailable"}]
    if validate:
        _prepare_browser_cookie_cache(force=True)
    return get_cookie_manager().list_browser_cookie_profiles(browser, validate=validate)


def init_startup_infrastructure() -> None:
    """服务器启动时的基础设施自动化（AI 不可见）。

    初始化 Cookie Manager，并在未配置 Cookie 时尝试从已登录浏览器自动获取。
    所有 MCP 服务器入口（server.py、skill_server.py、assist.py）均应调用，
    以保证启动行为一致，无需用户每次确认。
    """
    init_cookie_manager_integration()

    if browser_cookie_auto_load_enabled() and not os.environ.get("GEMINI_PSID"):
        logger.info("🍪 环境变量未设置 Cookie，尝试从 Chrome 自动获取...")
        try:
            _client_manager.ensure_browser_cookie()
        except ClientAuthenticationRequired as error:
            logger.warning("Browser cookie autoload unavailable reason=%s", error.auth_reason)


def ensure_web_cookie() -> None:
    """Make browser acquisition available to background submissions before they claim an account."""
    _client_manager.ensure_browser_cookie()


def get_cookie_status() -> Dict[str, Any]:
    """获取 Cookie 状态"""
    if not COOKIE_MANAGER_AVAILABLE:
        return {"available": False, "message": "Cookie Manager 不可用"}
    status, info = get_cookie_manager().get_cookie_status()
    return {**info, "available": True, "status": status.value,
            "browser_auto_load": browser_cookie_auto_load_enabled(),
            "browser_auth_reason": _client_manager.browser_auth_reason}
