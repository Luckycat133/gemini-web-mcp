"""Gem management with read-back verification for every mutation."""

from __future__ import annotations

from typing import Any


def iter_gem_values(gems: Any) -> list[Any]:
    if not gems:
        return []
    if hasattr(gems, "values"):
        return list(gems.values())
    return list(gems)


def gem_field(gem: Any, *names: str) -> tuple[bool, str]:
    for name in names:
        if isinstance(gem, dict) and name in gem and gem[name] is not None:
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
            return gem
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


async def create_gem(
    client: Any,
    *,
    name: str,
    description: str | None,
    instructions: str,
) -> dict[str, Any]:
    created = await client.create_gem(name=name, prompt=instructions, description=description)
    created_id = gem_field(created, "id", "gem_id")[1]
    observed, verification_status, verification_error = (
        await _read_back(client, created_id)
        if created_id
        else (
            None,
            "missing_mutation_id",
            "",
        )
    )
    return {
        "ok": bool(created_id),
        "id": created_id,
        "name": name,
        "gem": gem_to_dict(observed or created),
        "verification_status": verification_status,
        "verification_error": verification_error,
    }


async def update_gem(
    client: Any,
    *,
    gem_id: str,
    name: str | None,
    description: str | None,
    instructions: str | None,
) -> dict[str, Any]:
    existing = None
    if name is None or instructions is None or description is None:
        gems = await client.fetch_gems()
        existing = find_gem_by_id(gems, gem_id)
        if existing is None:
            return {
                "ok": False,
                "id": gem_id,
                "verification_status": "target_not_found",
                "missing_fields": [],
            }

    missing_fields: list[str] = []
    if name is None:
        found, resolved_name = gem_field(existing, "name")
        if not found:
            missing_fields.append("name")
    else:
        resolved_name = name
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
            "id": gem_id,
            "verification_status": "existing_fields_unavailable",
            "missing_fields": missing_fields,
        }

    await client.update_gem(
        gem=gem_id,
        name=resolved_name,
        prompt=resolved_instructions,
        description=resolved_description,
    )
    observed, verification_status, verification_error = await _read_back(client, gem_id)
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
    return {
        "ok": True,
        "id": gem_id,
        "gem": gem_to_dict(observed) if observed is not None else None,
        "verification_status": verification_status,
        "verification_error": verification_error,
        "mismatched_fields": mismatches,
    }


async def delete_gem(client: Any, *, gem_id: str) -> dict[str, Any]:
    await client.delete_gem(gem_id)
    try:
        gems = await client.fetch_gems()
    except Exception as exc:
        return {
            "ok": True,
            "id": gem_id,
            "verification_status": "read_back_error",
            "verification_error": f"{type(exc).__name__}: {exc}",
        }
    still_present = find_gem_by_id(gems, gem_id) is not None
    return {
        "ok": True,
        "id": gem_id,
        "verification_status": "still_present" if still_present else "verified_deleted",
        "verification_error": "",
    }


_iter_gem_values = iter_gem_values
_find_gem_by_id = find_gem_by_id
_gem_field = gem_field
