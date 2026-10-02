"""Gem management with strict read-back verification for every mutation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gemini_webapi import GeminiClient

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.rpc_contracts import RawRPCData, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope


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
            mismatch = (
                f"mismatched_fields={','.join(self.mismatched_fields)}；"
                if self.mismatched_fields
                else ""
            )
            if gem_id:
                message = (
                    f"Gem 创建请求返回 ID {gem_id}，但读回验证未通过；"
                    f"{mismatch}verification_status={verification_status}。请重新列出 Gems 核对。"
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
        gems = await _fetch_verified_custom_gems(client)
    except Exception as exc:
        return None, "read_back_error", f"{type(exc).__name__}: {exc}"
    gem = find_gem_by_id(gems, gem_id)
    return gem, "verified" if gem is not None else "read_back_not_observed", ""


async def _fetch_verified_custom_gems(client: Any) -> Any:
    if isinstance(client, GeminiClient):
        # SDK fetch_gems combines system/custom replies and silently accepts a
        # missing custom reply when system entries exist. Only the complete,
        # matching custom collection can prove a custom Gem's absence.
        contract = get_contract("gems.custom_registry")
        response = await client._batch_execute(
            [RawRPCData(contract.rpc_id, contract.build_payload(locale=client.language), identifier="custom")],
            source_path=contract.source_path, close_on_error=False,
        )
        items, diagnostic = _read_gem_registry(response, "custom")
        if not diagnostic["read_back_valid"]:
            raise ValueError("Custom Gem registry did not provide valid read-back evidence.")
        return {item["id"]: item for item in items}
    gems = await client.fetch_gems()
    if gems is None or isinstance(gems, (str, bytes)):
        raise ValueError("Gem read-back did not return a collection.")
    values = iter_gem_values(gems)
    if any(not gem_field(item, "id", "gem_id")[1].strip() for item in values):
        raise ValueError("Gem read-back contained an invalid entry.")
    return gems


def _read_gem_registry(response: Any, identifier: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contract = get_contract(f"gems.{identifier}_registry")
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), contract.rpc_id,
                                  expected_identifier=identifier)
    diagnostic: dict[str, Any] = {
        "contract_key": contract.key, "status_code": getattr(response, "status_code", None),
        "reject_code": envelope.reject_code, "body_count": len(envelope.bodies), "read_back_valid": False,
    }
    if (diagnostic["status_code"] != 200 or not envelope.parsed
            or envelope.reject_code is not None or len(envelope.bodies) != 1):
        return [], diagnostic
    parsed = parse_contract_body(contract, envelope.bodies[0])
    diagnostic.update(parser_status=parsed.status, parser_warnings=list(parsed.warnings))
    if not parsed.ok or parsed.warnings:
        return [], diagnostic
    diagnostic["read_back_valid"] = True
    return [{**item, "predefined": identifier == "system"} for item in parsed.value], diagnostic


async def list_gems(client: Any) -> DomainResult[dict[str, Any]]:
    """Observe both complete registries; never silently omit a missing reply."""
    if not isinstance(client, GeminiClient):
        gems = await _fetch_verified_custom_gems(client)
        return DomainResult.success({"items": [gem_to_dict(item) for item in iter_gem_values(gems)],
                                     "diagnostic": {"source": "client_collection", "complete": True}},
                                    verification_status="observed")
    contracts = [get_contract(f"gems.{identifier}_registry") for identifier in ("system", "custom")]
    response = await client._batch_execute(
        [RawRPCData(contract.rpc_id, contract.build_payload(locale=client.language), identifier=identifier)
         for contract, identifier in zip(contracts, ("system", "custom"), strict=True)],
        source_path=contracts[0].source_path, close_on_error=False,
    )
    system, system_diagnostic = _read_gem_registry(response, "system")
    custom, custom_diagnostic = _read_gem_registry(response, "custom")
    complete = system_diagnostic["read_back_valid"] and custom_diagnostic["read_back_valid"]
    data: dict[str, Any] = {"items": list({item["id"]: item for item in [*system, *custom]}.values()), "diagnostic": {
        "source": "gems.registry", "complete": complete, "system": system_diagnostic, "custom": custom_diagnostic,
    }}
    if complete:
        return DomainResult.success(data, verification_status="observed")
    if system_diagnostic["read_back_valid"] or custom_diagnostic["read_back_valid"]:
        return DomainResult.success(
            data, operation_state=OperationState.PARTIAL, verification_status="observed_partial",
            warnings=(DomainWarning("GEM_REGISTRY_INCOMPLETE", "The Gem list contains only the registries observed in this response."),),
        )
    code = (DomainErrorCode.UPSTREAM_REJECTED if any(diagnostic["reject_code"] is not None
                                                   for diagnostic in (system_diagnostic, custom_diagnostic))
            else DomainErrorCode.UPSTREAM_CHANGED)
    return DomainResult.failure(code, "The Gem registries could not be verified.", data=data,
                                verification_status="read_back_unavailable", details=data["diagnostic"])


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
    mismatches: list[str] = []
    if observed is not None:
        actual = gem_to_dict(observed)
        expected = {
            "name": clean_name,
            "description": description or "",
            "instructions": instructions,
        }
        mismatches = [key for key, value in expected.items() if actual.get(key) != value]
        if mismatches:
            verification_status = "read_back_mismatch"
    payload = {
        "ok": bool(created_id),
        "id": created_id,
        "name": clean_name,
        "gem": gem_to_dict(observed or created),
        "verification_status": verification_status,
        "verification_error": verification_error,
        "mismatched_fields": mismatches,
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
        gems = await _fetch_verified_custom_gems(client)
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
        gems = await _fetch_verified_custom_gems(client)
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
