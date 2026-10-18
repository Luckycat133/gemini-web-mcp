"""Explicit account workflows shared independently of MCP registrations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..domain.account_requests import Request
from ..domain.results import result_from_exception
from ..infrastructure.rpc_contracts import WEB_FEATURE_PROBES, execute_contract
from . import cleanup
from .account import account_contract_result, account_probe_status, account_read_result, execute_observed_rpc, sanitize_account_status
from .gems import GemMutationNotVerified, create_gem, delete_gem, list_gems, update_gem
from .history import (
    delete_chat_result, export_chat_result, list_chat_history_result,
    paginate_items, read_chat_result, search_chat_history_result,
)
from .manifest import web_capabilities_payload
from .history_mutations import update_chat_metadata_result
from .tasks import get_task_status_result
from .notebooks import fetch_native_notebooks, fetch_notebook_chats, move_chat_to_notebook
from .prompts import PromptLibrary
from .scheduled import (
    create_daily_action, delete_action, fetch_scheduled_registry, fetch_scheduled_task_by_id,
    fetch_remy_schedule_registry, fetch_remy_schedule_by_id,
)

logger = logging.getLogger(__name__)


def _unobserved(diagnostic: dict[str, Any]) -> DomainResult[Any]:
    return account_read_result(None, diagnostic)


def _read_valid(diagnostic: dict[str, Any]) -> bool:
    return account_read_result(None, diagnostic).ok


async def _read_contract(client: Any, key: str, **arguments: Any) -> DomainResult[Any]:
    response = await execute_contract(client, key, **arguments)
    return account_contract_result(response, key, **arguments)


def _mutation(payload: dict[str, Any]) -> DomainResult[dict[str, Any]]:
    """Only explicit read-back proof completes an account mutation."""
    status = str(payload.get("verification_status") or "not_available")
    accepted = payload.get("accepted") is True or payload.get("ok") is True
    scheduled_absence = (
        status == "deleted_state_by_id"
        and payload.get("deleted_by_id_after_delete") is True
        and payload.get("task_state_id_after_delete") == 6
        and _read_valid(payload.get("get_task_diagnostic") or {})
    ) or (
        status == "not_visible_not_readable_by_id"
        and payload.get("visible_after_delete") is False
        and payload.get("readable_by_id_after_delete") is False
        and _read_valid(payload.get("registry_diagnostic") or {})
        and _read_valid(payload.get("get_task_diagnostic") or {})
        and payload["get_task_diagnostic"].get("parser_status") == "empty"
    )
    verified = accepted and (
        payload.get("verified") is True or payload.get("verified_in_target_notebook") is True
        or scheduled_absence or status in {
            "verified", "verified_absent", "verified_created", "verified_updated", "verified_present", "verified_deleted",
        }
    )
    if verified:
        return DomainResult.success(payload, verification_status=status)
    if accepted:
        return DomainResult.success(
            payload, operation_state=OperationState.ACCEPTED, verification_status=status,
            warnings=(DomainWarning("MUTATION_UNVERIFIED", "The request was accepted; the requested state is not verified."),),
        )
    return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The requested account mutation was not verified.",
                                data=payload, verification_status=status)


class AccountFacadeService:
    """One dispatcher over existing domain services; no tools/manage dependency."""

    def __init__(self, *, client_provider: Callable[[], Any] | None = None,
                 initializer: Any = None, prompts_path: str | Path = "prompts.json") -> None:
        self._client_provider = client_provider or get_gemini_client
        self._initializer = initializer or initialize_client
        self.prompts_path = Path(prompts_path)

    async def execute(self, facade: str, request: Request) -> DomainResult[dict[str, Any]]:
        arguments = request.model_dump(exclude_none=True)
        action = arguments.pop("action")
        mutations = {"history": {"delete", "update"}, "notebooks": {"move"}, "scheduled": {"create_daily", "delete"},
                     "gems": {"create", "update", "delete"}, "prompts": {"create", "update", "delete"},
                     "cleanup": {"run", "cancel"}}
        effect = "mutation" if action in mutations.get(facade, set()) or (
            facade == "cleanup" and action == "test_artifacts" and not arguments.get("dry_run", True)
        ) else "read"
        try:
            if facade == "prompts":
                result = await asyncio.to_thread(self._prompts, action, arguments)
            elif facade == "account" and action == "capabilities":
                result = DomainResult.success(web_capabilities_payload(), verification_status="documented_contract")
            elif facade == "cleanup" and action in {"status", "cancel"}:
                result = await self._cleanup(None, action, arguments)
            else:
                client = self._client_provider()
                await self._initializer()
                result = await getattr(self, f"_{facade}")(client, action, arguments)
        except GemMutationNotVerified as error:
            result = DomainResult.failure(
                DomainErrorCode.VERIFICATION_FAILED, "Gem mutation has no verified read-back state.",
                data={"id": error.gem_id, "mismatched_fields": list(error.mismatched_fields)},
                verification_status=error.verification_status,
            )
        except Exception as error:
            result = result_from_exception(error, logger=logger, operation=f"account.{facade}.{action}")
        return replace(result, meta=replace(result.meta, details={
            **result.meta.details, "facade": facade, "action": action, "effect": effect,
        }))

    async def _history(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        if action == "task_status":
            return await get_task_status_result(client, args["chat_id"])
        if action == "update":
            return await update_chat_metadata_result(client, **args)
        if action == "read":
            return await read_chat_result(client, args["chat_id"], args["limit"], args["max_chars"], max_limit=200)
        if action == "export":
            return await export_chat_result(client, args["chat_id"], args["limit"], args["max_chars"], include_metadata=False)
        if action == "delete":
            return await delete_chat_result(client, args["chat_id"])
        if action == "search":
            return await search_chat_history_result(
                client, args["query"], args["limit"], args["offset"],
                scan_turns=args["scan_turns"], turns_per_chat=args["turns_per_chat"],
                max_chars_per_turn=args["max_chars"], max_limit=100,
                max_turns_per_chat=100, max_chars=20000,
            )
        return await list_chat_history_result(client, args["limit"], args["offset"], max_limit=100)

    async def _notebooks(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        if action == "move":
            return _mutation(await move_chat_to_notebook(client, **args))
        if action == "chats":
            items, chat_page = await fetch_notebook_chats(client, **args)
            diagnostic = chat_page["diagnostic"]
            if not _read_valid(diagnostic):
                return _unobserved(diagnostic)
            if diagnostic.get("complete") is False:
                return DomainResult.success(
                    {**chat_page, "items": items}, operation_state=OperationState.PARTIAL,
                    verification_status="observed_partial", details=diagnostic,
                    warnings=(DomainWarning("PAGINATION_INCOMPLETE", "Notebook chat pagination could not complete."),),
                )
            return DomainResult.success({**chat_page, "items": items}, verification_status="observed")
        items, diagnostic = await fetch_native_notebooks(client, args["locale"])
        if not _read_valid(diagnostic):
            return _unobserved(diagnostic)
        page, pagination = paginate_items(items, args["limit"], args["offset"])
        return DomainResult.success({**pagination, "items": page, "diagnostic": diagnostic}, verification_status="observed")

    async def _scheduled(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        backend = args.pop("backend", "legacy")
        if backend not in {"legacy", "remy"} or (backend == "remy" and action not in {"list", "get"}):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Native schedules support list/get only.")
        fetch_registry = fetch_remy_schedule_registry if backend == "remy" else fetch_scheduled_registry
        fetch_by_id = fetch_remy_schedule_by_id if backend == "remy" else fetch_scheduled_task_by_id
        if action == "create_daily":
            return _mutation(await create_daily_action(client, **args))
        if action == "delete":
            return _mutation(await delete_action(client, **args))
        if action == "get":
            item, diagnostic = await fetch_by_id(client, args["action_id"], args["max_chars"])
            if not _read_valid(diagnostic):
                return _unobserved(diagnostic)
            return DomainResult.success({"backend": backend, "item": item, "diagnostic": diagnostic}, verification_status="observed")
        items, diagnostic = await fetch_registry(client, args["max_chars"])
        if not _read_valid(diagnostic):
            return _unobserved(diagnostic)
        scope = args["scope"]
        if scope != "all":
            items = [item for item in items if item.get("enabled") is (scope == "active")]
        page, pagination = paginate_items(items, args["limit"], args["offset"])
        return DomainResult.success({**pagination, "backend": backend, "items": page, "diagnostic": diagnostic}, verification_status="observed")

    async def _gems(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        if action == "list":
            result = await list_gems(client)
            if not result.ok or result.data is None:
                return result
            page, pagination = paginate_items(result.data["items"], args["limit"], args["offset"])
            return replace(result, data={**result.data, **pagination, "items": page})
        if action == "create":
            return _mutation(await create_gem(client, **args))
        if action == "update":
            return _mutation(await update_gem(client, gem_id=args["gem_id"], name=args.get("name"),
                                              instructions=args.get("instructions"), description=args.get("description")))
        return _mutation(await delete_gem(client, **args))

    def _prompts(self, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        library = PromptLibrary(self.prompts_path)
        if action == "categories":
            return DomainResult.success({"categories": sorted({item["category"] for item in library.list()})}, verification_status="observed")
        if action == "list":
            page, pagination = paginate_items(library.list(args.get("category")), args["limit"], args["offset"])
            return DomainResult.success({**pagination, "items": page}, verification_status="observed")
        if action == "create":
            prompt_id = library.create(**args)
            return DomainResult.success({"item": library.get(prompt_id)}, verification_status="verified_created")
        prompt_id = args["prompt_id"]
        existing = library.get(prompt_id)
        if existing is None:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Prompt ID was not found.", verification_status="target_not_found")
        if action == "get":
            return DomainResult.success({"item": existing}, verification_status="observed")
        if action == "render":
            try:
                text = existing["content"].format(**args["variables"])
            except (KeyError, IndexError, ValueError, AttributeError):
                return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Prompt variables or template are invalid.")
            return DomainResult.success({"prompt_id": prompt_id, "text": text}, verification_status="rendered_local")
        if action == "update":
            library.update(prompt_id, **{key: value for key, value in args.items() if key != "prompt_id"})
            return DomainResult.success({"item": library.get(prompt_id)}, verification_status="verified_updated")
        library.delete(prompt_id)
        return DomainResult.success({"prompt_id": prompt_id, "deleted": library.get(prompt_id) is None}, verification_status="verified_absent")

    async def _account(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        if action == "status":
            if not hasattr(client, "inspect_account_status"):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Account status is unavailable in this client.")
            return DomainResult.success(sanitize_account_status(await client.inspect_account_status()), verification_status="observed")
        if action == "models":
            if not hasattr(client, "list_models"):
                return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Model discovery is unavailable in this client.")
            items = [{key: getattr(model, key, None) for key in ("model_id", "model_name", "display_name", "is_available", "description")}
                     for model in client.list_models()]
            return DomainResult.success({"items": items}, verification_status="observed")
        if action == "features":
            results = []
            for probe in WEB_FEATURE_PROBES:
                if args["surface"] != "all" and probe["surface"] != args["surface"]:
                    continue
                response = await execute_observed_rpc(client, probe)
                results.append({"name": probe["name"], "surface": probe["surface"],
                                **account_probe_status(response, probe)})
            return DomainResult.success({"results": results}, verification_status="rpc_observations")
        keys: dict[str, tuple[str, ...]] = {"links": ("sharing.public_links",), "library": ("library.locale_capabilities",), "modes": ("tool_modes.status",)}
        if action == "usage":
            keys[action] = tuple(f"usage.{scope}" for scope in ("quota", "model_state") if args["scope"] in {scope, "all"})
        results = []
        for key in keys[action]:
            result = await _read_contract(client, key)
            if not result.ok:
                return result
            assert result.data is not None
            value = result.data["value"]
            results.append({"contract_key": key, "items": value})
        if action == "usage":
            return DomainResult.success({"results": results}, verification_status="observed")
        page, pagination = paginate_items(results[0]["items"], args["limit"], args["offset"])
        return DomainResult.success({**pagination, "items": page}, verification_status="observed")

    async def _cleanup(self, client: Any, action: str, args: dict[str, Any]) -> DomainResult[Any]:
        if action == "status":
            return await cleanup.list_cleanup_jobs(client, **args)
        if action == "cancel":
            return await cleanup.cancel_cleanup_job(args["job_id"], client)
        if action == "run":
            if args.get("job_id"):
                return await cleanup.retry_cleanup_job(args["job_id"], client)
            return await cleanup.cleanup_due_jobs(client)
        payload = await cleanup.cleanup_test_artifacts_payload(client, **args)
        return DomainResult.success(
            payload, operation_state=OperationState.PARTIAL if payload["errors"] else OperationState.COMPLETED,
            verification_status="dry_run" if args["dry_run"] else "per_item_read_back",
        )


def get_account_facade_service() -> AccountFacadeService:
    return AccountFacadeService()
