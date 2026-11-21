"""Shared, atomic Prompt storage for both compatibility surfaces."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TypeVar


T = TypeVar("T")
PromptData = dict[str, dict[str, Any]]


class PromptLibrary:
    """Keep one JSON format and serialize complete read-modify-write transactions."""

    def __init__(self, file_path: str | Path):
        self.path = Path(file_path).expanduser().resolve()
        self.data: PromptData = {}
        self._lock = threading.RLock()
        self._saving_mutation = False
        self.load()

    def _read(self) -> PromptData:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                document = json.load(file)
            return self._validate(document)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ValueError("无法加载提示词库；原文件已保留，请先修复后重试。") from error

    @staticmethod
    def _validate(document: Any) -> PromptData:
        if not isinstance(document, dict) or not isinstance(document.get("prompts"), dict):
            raise ValueError("Invalid Prompt library document")
        data: PromptData = document["prompts"]
        for key, prompt in data.items():
            if (
                not isinstance(key, str) or not isinstance(prompt, dict) or prompt.get("id") != key
                or not isinstance(prompt.get("name"), str) or not isinstance(prompt.get("content"), str)
            ):
                raise ValueError("Invalid Prompt library entry")
        return data

    def load(self) -> None:
        with self._lock:
            self.data = self._read()

    def current(self) -> PromptData:
        with self._lock:
            if self.path.exists():
                self.data = self._read()
            return self.data

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        # Keep the sidecar inode stable: unlinking it allows concurrent writers
        # to lock different files while both believe they own the transaction.
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_LOCK"), 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _atomic_write(self, document: dict[str, Any] | None = None) -> None:
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as file:
                temp_path = file.name
                json.dump(
                    document if document is not None else {
                        "version": "1.0", "updated_at": datetime.now().isoformat(), "prompts": self.data,
                    },
                    file, ensure_ascii=False, indent=2,
                )
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path:
                with suppress(OSError):
                    os.unlink(temp_path)

    def save(self) -> None:
        with self._lock:
            if self._saving_mutation:
                self._atomic_write()
            else:
                with self._file_lock():
                    self._read()  # A damaged source must never be overwritten.
                    self._atomic_write()

    def _change(
        self,
        change: Callable[[PromptData], tuple[PromptData | None, T]],
        save: Callable[[], None] | None,
    ) -> T:
        with self._lock, self._file_lock():
            original = self._read()
            self.data = original
            updated, result = change(original)
            if updated is None:
                return result
            self.data = updated
            self._saving_mutation = True
            try:
                (save or self.save)()
            except Exception:
                self.data = original
                raise
            finally:
                self._saving_mutation = False
            return result

    def create(
        self, name: str, content: str, category: str = "general", description: str = "",
        *, unique_name: bool = False, save: Callable[[], None] | None = None,
    ) -> str:
        def change(data: PromptData) -> tuple[PromptData, str]:
            if unique_name and any(prompt["name"].casefold() == name.casefold() for prompt in data.values()):
                raise ValueError("A Prompt with this name already exists; choose another name.")
            prompt_id = str(uuid.uuid4())
            timestamp = datetime.now().isoformat()
            return {**data, prompt_id: {
                "id": prompt_id, "name": name, "content": content, "category": category,
                "description": description, "created_at": timestamp, "updated_at": timestamp,
            }}, prompt_id

        return self._change(change, save)

    def seed_if_absent(self, default_file: str | Path | Traversable) -> bool:
        """Atomically initialize bundled defaults without replacing an existing library."""
        document: dict[str, Any] | None = None

        def change(_data: PromptData) -> tuple[PromptData | None, bool]:
            nonlocal document
            if self.path.exists():
                return None, False
            resource = Path(default_file) if isinstance(default_file, str) else default_file
            with resource.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
                defaults = self._validate(loaded)
                document = loaded
            return defaults, True

        def save_defaults() -> None:
            assert document is not None
            self._atomic_write(document)

        return self._change(change, save_defaults)

    def get(self, prompt_id: str) -> dict[str, Any] | None:
        return self.current().get(prompt_id)

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        return next((prompt for prompt in self.current().values() if prompt["name"].casefold() == name.casefold()), None)

    def list(self, category: str | None = None, *, by_name: bool = False) -> list[dict[str, Any]]:
        values = [prompt for prompt in self.current().values() if not category or prompt.get("category") == category]
        if by_name:
            return sorted(values, key=lambda prompt: prompt.get("name", "").casefold())
        return sorted(values, key=lambda prompt: prompt.get("created_at", ""), reverse=True)

    def update(
        self, prompt_id: str, *, save: Callable[[], None] | None = None, **fields: str | None,
    ) -> bool:
        def change(data: PromptData) -> tuple[PromptData | None, bool]:
            if prompt_id not in data:
                return None, False
            prompt = {**data[prompt_id], **{key: value for key, value in fields.items() if value is not None}}
            prompt["updated_at"] = datetime.now().isoformat()
            return {**data, prompt_id: prompt}, True

        return self._change(change, save)

    def delete(self, identifier: str, *, by_name: bool = False, save: Callable[[], None] | None = None) -> bool:
        def change(data: PromptData) -> tuple[PromptData | None, bool]:
            prompt_id = identifier
            if by_name:
                prompt_id = next((key for key, value in data.items() if value["name"].casefold() == identifier.casefold()), "")
            if prompt_id not in data:
                return None, False
            return {key: value for key, value in data.items() if key != prompt_id}, True

        return self._change(change, save)
