"""Local Prompt storage shared by both MCP compatibility tools."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

PromptData = dict[str, dict[str, Any]]
T = TypeVar("T")


class PromptLibrary:
    """Atomically replace the latest snapshot, under the released sidecar lock."""

    def __init__(self, file_path: str | Path):
        self.path = Path(file_path).expanduser().resolve()
        self.current()  # Fail early on malformed existing storage.

    def current(self) -> PromptData:
        try:
            with self.path.open(encoding="utf-8") as file:
                document = json.load(file)
            if not isinstance(document, dict) or not isinstance(document.get("prompts"), dict):
                raise ValueError("Invalid Prompt library document")
            data: PromptData = document["prompts"]
            for key, item in data.items():
                if (
                    not isinstance(item, dict) or item.get("id") != key
                    or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("content"), str)
                ):
                    raise ValueError("Invalid Prompt library entry")
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ValueError("无法加载提示词库；原文件已保留，请先修复后重试。") from error

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        # Never unlink the sidecar: the data file is replaced, but the lock
        # inode must remain stable across versions, threads and processes.
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Prompt library lock is not a regular file")
            if os.name == "nt":
                import msvcrt

                # The Windows lock can cover an empty range beyond EOF.
                # Initialize the released sentinel only after acquiring it.
                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_LOCK"), 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                if os.name == "nt" and os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                yield
            finally:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _atomic_write(self, prompts: PromptData) -> None:
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temp_path = file.name
                json.dump(
                    {"version": "1.0", "updated_at": datetime.now().isoformat(), "prompts": prompts},
                    file, ensure_ascii=False, indent=2,
                )
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path:
                with suppress(OSError):
                    os.unlink(temp_path)

    def _change(self, change: Callable[[PromptData], tuple[bool, T]]) -> T:
        with self._file_lock():
            data = self.current()
            changed, result = change(data)
            if changed:
                self._atomic_write(data)
            return result

    def create(self, name: str, content: str, category: str = "general", description: str = "") -> str:
        def change(data: PromptData) -> tuple[bool, str]:
            prompt_id = str(uuid.uuid4())
            timestamp = datetime.now().isoformat()
            data[prompt_id] = {
                "id": prompt_id, "name": name, "content": content, "category": category,
                "description": description, "created_at": timestamp, "updated_at": timestamp,
            }
            return True, prompt_id

        return self._change(change)

    def get(self, prompt_id: str) -> dict[str, Any] | None:
        return self.current().get(prompt_id)

    def list(self, category: str | None = None) -> list[dict[str, Any]]:
        rows = (item for item in self.current().values() if not category or item.get("category") == category)
        return sorted(rows, key=lambda item: item.get("created_at", ""), reverse=True)

    def update(self, prompt_id: str, **fields: str | None) -> bool:
        def change(data: PromptData) -> tuple[bool, bool]:
            if prompt_id not in data:
                return False, False
            data[prompt_id].update({key: value for key, value in fields.items() if value is not None})
            data[prompt_id]["updated_at"] = datetime.now().isoformat()
            return True, True

        return self._change(change)

    def delete(self, prompt_id: str) -> bool:
        def change(data: PromptData) -> tuple[bool, bool]:
            if prompt_id not in data:
                return False, False
            del data[prompt_id]
            return True, True

        return self._change(change)
