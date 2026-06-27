"""
会话管理器 - 负责本地会话的存储、检索和清理
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

from .constants import DEFAULT_CHAT_RETENTION_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class SessionData:
    """会话数据结构"""
    session: Any
    model: str = "flash"
    thinking_level: str = "standard"
    learning_mode: Optional[str] = None
    temporary: bool = False
    created_at: float = field(default_factory=time.time)
    retain_chat: bool = False
    delete_after_seconds: Optional[int] = None


class SessionManager:
    """会话管理器 - 线程安全的会话存储"""

    def __init__(self, max_age: int = DEFAULT_CHAT_RETENTION_SECONDS):
        self._sessions: Dict[str, SessionData] = {}
        self._lock = threading.Lock()
        self._max_age = max_age

    def store_session(
        self,
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
        with self._lock:
            self._sessions[session_id] = SessionData(
                session=session,
                model=model,
                thinking_level=thinking_level,
                learning_mode=learning_mode,
                temporary=temporary,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
            )

    def get_session(self, session_id: str) -> Optional[SessionData]:
        """获取存储的会话"""
        with self._lock:
            self._clean_expired_sessions()
            return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """移除会话"""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]

    def pop_session(self, session_id: str) -> Optional[SessionData]:
        """移除并返回会话数据。"""
        with self._lock:
            self._clean_expired_sessions()
            return self._sessions.pop(session_id, None)

    def list_sessions(self) -> Dict[str, SessionData]:
        """获取所有会话"""
        with self._lock:
            self._clean_expired_sessions()
            return dict(self._sessions)

    def clear_sessions(self) -> None:
        """清空所有会话"""
        with self._lock:
            self._sessions.clear()

    def cleanup_expired_sessions(self) -> None:
        """清理过期会话。"""
        with self._lock:
            self._clean_expired_sessions()

    def _clean_expired_sessions(self) -> None:
        """清理过期会话（内部函数，需在锁内调用）"""
        now = time.time()
        expired = [
            sid for sid, data in self._sessions.items()
            if now - data.created_at > self._max_age
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info(f"清理了 {len(expired)} 个过期会话")
