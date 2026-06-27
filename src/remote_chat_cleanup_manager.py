"""
远程聊天清理管理器 - 负责远端 Gemini chat 的自动删除调度和执行
"""

import asyncio
import time
import threading
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable

from .constants import DEFAULT_CHAT_RETENTION_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class CleanupTask:
    """清理任务数据结构"""
    delete_at: float
    source: str = ""


def extract_remote_chat_id(obj: Any) -> Optional[str]:
    """从 Gemini response/chat/session 对象中提取远端 chat id。"""
    cid = getattr(obj, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid

    metadata = getattr(obj, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid

    return None


class RemoteChatCleanupManager:
    """远程聊天清理管理器 - 线程安全的清理任务调度"""

    def __init__(
        self,
        default_retention_seconds: int = DEFAULT_CHAT_RETENTION_SECONDS,
        client_provider: Optional[Callable[[], Any]] = None,
        retention_provider: Optional[Callable[[], int]] = None,
    ):
        self._pending_cleanup: Dict[str, CleanupTask] = {}
        self._lock = threading.Lock()
        self._default_retention = default_retention_seconds
        self._client_provider = client_provider
        self._retention_provider = retention_provider

    def schedule_cleanup_from_response(
        self,
        response: Any,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        source: str = "",
    ) -> Optional[str]:
        """登记 response 产生的远端 chat，默认稍后自动删除。"""
        cid = extract_remote_chat_id(response)
        if cid:
            self.schedule_cleanup(
                cid,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=source,
            )
        return cid

    def schedule_cleanup(
        self,
        cid: Optional[str],
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        source: str = "",
    ) -> None:
        """登记远端 Gemini chat 的自动删除任务。"""
        if not cid or retain_chat:
            return

        if delete_after_seconds is None and self._retention_provider is not None:
            delete_after_seconds = self._retention_provider()
        ttl = self._default_retention if delete_after_seconds is None else max(0, delete_after_seconds)
        delete_at = time.time() + ttl

        with self._lock:
            self._pending_cleanup[cid] = CleanupTask(
                delete_at=delete_at,
                source=source,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._delete_after_delay(cid, delete_at))

    async def _delete_after_delay(self, cid: str, delete_at: float) -> None:
        """延迟删除任务"""
        await asyncio.sleep(max(0, delete_at - time.time()))

        with self._lock:
            pending = self._pending_cleanup.get(cid)
            if not pending or pending.delete_at != delete_at:
                return

        await self.delete_chat(cid)

    async def delete_chat(
        self,
        cid: Optional[str],
        client: Any = None,
        client_initializer: Optional[Callable[[], Any]] = None,
    ) -> bool:
        """立即删除远端 Gemini chat。"""
        if not cid:
            return False

        if client is None:
            if client_initializer is not None:
                client = client_initializer()
            elif self._client_provider is not None:
                client = self._client_provider()

        if not hasattr(client, "delete_chat"):
            logger.warning("当前 GeminiClient 不支持 delete_chat")
            return False

        try:
            await client.delete_chat(cid)
        except Exception as e:
            logger.warning(f"删除远端 Gemini 对话失败 {cid}: {e}")
            return False

        with self._lock:
            self._pending_cleanup.pop(cid, None)

        logger.info(f"已删除远端 Gemini 对话: {cid}")
        return True

    async def cleanup_due_chats(
        self,
        client: Any = None,
        client_initializer: Optional[Callable[[], Any]] = None,
    ) -> int:
        """清理已经到期的远端 Gemini chat。"""
        now = time.time()
        with self._lock:
            due_cids = [
                cid
                for cid, data in self._pending_cleanup.items()
                if data.delete_at <= now
            ]

        if client is None:
            if client_initializer is not None:
                client = client_initializer()
            elif self._client_provider is not None:
                client = self._client_provider()

        deleted = 0
        for cid in due_cids:
            if await self.delete_chat(cid, client=client):
                deleted += 1
        return deleted

    def list_pending_cleanup(self) -> Dict[str, CleanupTask]:
        """返回待自动删除的远端 chat。"""
        with self._lock:
            return dict(self._pending_cleanup)
