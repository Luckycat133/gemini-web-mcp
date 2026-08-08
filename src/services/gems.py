"""Gem management with strict read-back verification for every mutation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class GemMutationNotVerified(RuntimeError):
    """A Gem mutation was accepted but its requested state was not observed."""

    def __init__(
        self,
        operation: str,
        *,
        gem_id: str = "",
        verification_status: str,
        mismatched_fields: list[str] | None = None,
    ) -> None:
        self.operation = operation
        self.gem_id = gem_id
        self.verification_status = verification_status
        self.mismatched_fields = tuple(mismatched_fields or ())

        if operation == "create":
            if gem_id:
                message = (
                    f"Gem 创建请求返回 ID {gem_id}，但尚未读回验证；"
                    f"verification_status={verification_status}。请重新列出 Gems 核对。"
                )
            else:
                message = (
                    "Gem 创建请求未返回可用 ID，无法确认已创建；"
                    f"verification_status={verification_status}。"
                )
        elif operation == "update":
            mismatch = (
                f" mismatched_fields={','.join(self.mismatched_fields)};"
                if self.mismatched_fields
                else ""
            )
            # Keep the historical phrase in quotes for old text-only callers
            # while explicitly stating that the success claim is unverified.
            message = (
                f"Gem {gem_id} 的“更新成功”状态未获读回验证;{mismatch} "
                f"verification_status={verification_status}。请重新读取该 Gem 核对。"
            )
        else:
            message = (
                f"Gem {gem_id} 删除请求未获已删除证据；"
                f"verification_status={verification_status}。请重新列出 Gems 核对。"
            )
        super().__init__(message)


class _GemMappingView(dict[str, Any]):
    """Preserve mapping behavior while supporting legacy attribute rendering."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _as_legacy_view(gem: Any) -> Any:
    if isinstance(gem, _GemMappingView):
        return gem
    if isinstance(gem, Mapping):
        return _GemMappingView(gem)
    return gem


def iter_gem_values(gems: Any) -> list[Any]:
    if not gems:
        return []
    if hasattr(gems, "values"):
        values = list(gems.values())
    else:
        values = list(gems)
    return [_as_legacy_view(gem) for gem in values]


def gem_field(gem: Any, *names: str) -> tuple[bool, str]:
    for name in names:
        if isinstance(gem, Mapping) and name in gem and gem[name] is not None:
            return True, str(gem[name])
        if hasattr(gem, name):
            value = getattr(gem, name)
            if value is not None:
                return True, str(value)
    return False, ""


def find_gem_by_id(gems: Any, gem_id: str) -> Any:
    if hasattr(gems, "get"):
        gem = gems.get(gem_id)
        if gem is not None:
            return _as_legacy_view(gem)
    for gem in iter_gem_values(gems):
        if gem_field(gem, "id", "gem_id")[1] == gem_id:
            return gem
    return None


def gem_to_dict(gem: Any) -> dict[str, str]:
    return {
        "id": gem_field(gem, "id", "gem_id")[1],
        "name": gem_field(gem, "name")[1],
        "description": gem_field(gem, "description")[1],
        "instructions": gem_field(gem, "prompt", "instructions")[1],
    }


async def _read_back(client: Any, gem_id: str) -> tuple[Any, str, str]:
    try:
        gems = await client.fetch_gems()
    except Exception as exc:
        return None, "read_back_error", f"{type(exc).__name__}: {exc}"
    gem = find_gem_by_id(gems, gem_id)
    return gem, "verified" if gem is not None else "read_back_not_observed", ""


def _require_verified(
    operation: str,
    payload: dict[str, Any],
    *,
    expected_status: str,
) -> dict[str, Any]:
    status = str(payload.get("verification_status") or "not_attempted")
    mismatches = [str(item) for item in payload.get("mismatched_fields", [])]
    if payload.get("ok") and status == expected_status and not mismatches:
        return payload
    raise GemMutationNotVerified(
        operation,
        gem_id=str(payload.get("id") or ""),
        verification_status=status,
        mismatched_fields=mismatches,
    )


async def create_gem(
    client: Any,
    *,
    name: str,
    description: str | None,
    instructions: str,
) -> dict[str, Any]:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Gem name must not be empty.")

    created = await client.create_gem(name=clean_name, prompt=instructions, description=description)
    created_id = gem_field(created, "id", "gem_id")[1].strip()
    observed, verification_status, verification_error = (
        await _read_back(client, created_id)
        if created_id
        else (
            None,
            "missing_mutation_id",
            "",
        )
    )
    payload = {
        "ok": bool(created_id),
        "id": created_id,
        "name": clean_name,
        "gem": gem_to_dict(observed or created),
        "verification_status": verification_status,
        "verification_error": verification_error,
    }
    return _require_verified("create", payload, expected_status="verified")


async def update_gem(
    client: Any,
    *,
    gem_id: str,
    name: str | None,
    description: str | None,
    instructions: str | None,
) -> dict[str, Any]:
    clean_gem_id = gem_id.strip()
    if not clean_gem_id:
        raise ValueError("Gem ID must not be empty.")
    clean_name = name.strip() if name is not None else None
    if name is not None and not clean_name:
        raise ValueError("Gem name must not be blank when provided.")

    existing = None
    if clean_name is None or instructions is None or description is None:
        gems = await client.fetch_gems()
        existing = find_gem_by_id(gems, clean_gem_id)
        if existing is None:
            return {
                "ok": False,
                "id": clean_gem_id,
                "verification_status": "target_not_found",
                "missing_fields": [],
            }

    missing_fields: list[str] = []
    if clean_name is None:
        found, resolved_name = gem_field(existing, "name")
        if not found:
            missing_fields.append("name")
    else:
        resolved_name = clean_name
    if instructions is None:
        found, resolved_instructions = gem_field(existing, "prompt", "instructions")
        if not found:
            missing_fields.append("instructions")
    else:
        resolved_instructions = instructions
    if description is None:
        _found, resolved_description = gem_field(existing, "description")
    else:
        resolved_description = description
    if missing_fields:
        return {
            "ok": False,
            "id": clean_gem_id,
            "verification_status": "existing_fields_unavailable",
            "missing_fields": missing_fields,
        }

    await client.update_gem(
        gem=clean_gem_id,
        name=resolved_name,
        prompt=resolved_instructions,
        description=resolved_description,
    )
    observed, verification_status, verification_error = await _read_back(client, clean_gem_id)
    mismatches: list[str] = []
    if observed is not None:
        actual = gem_to_dict(observed)
        expected = {
            "name": resolved_name,
            "description": resolved_description,
            "instructions": resolved_instructions,
        }
        mismatches = [key for key, value in expected.items() if actual.get(key) != value]
        if mismatches:
            verification_status = "read_back_mismatch"
    payload = {
        "ok": True,
        "id": clean_gem_id,
        "gem": gem_to_dict(observed) if observed is not None else None,
        "verification_status": verification_status,
        "verification_error": verification_error,
        "mismatched_fields": mismatches,
    }
    return _require_verified("update", payload, expected_status="verified")


async def delete_gem(client: Any, *, gem_id: str) -> dict[str, Any]:
    clean_gem_id = gem_id.strip()
    if not clean_gem_id:
        raise ValueError("Gem ID must not be empty.")

    await client.delete_gem(clean_gem_id)
    try:
        gems = await client.fetch_gems()
    except Exception:
        payload = {
            "ok": True,
            "id": clean_gem_id,
            "verification_status": "read_back_error",
            "verification_error": "",
        }
        return _require_verified("delete", payload, expected_status="verified_deleted")
    still_present = find_gem_by_id(gems, clean_gem_id) is not None
    payload = {
        "ok": True,
        "id": clean_gem_id,
        "verification_status": "still_present" if still_present else "verified_deleted",
        "verification_error": "",
    }
    return _require_verified("delete", payload, expected_status="verified_deleted")


_iter_gem_values = iter_gem_values
_find_gem_by_id = find_gem_by_id
_gem_field = gem_field
