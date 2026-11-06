"""Thin account adapter for persistent Gemini Task preferences."""

import logging
from typing import Any, Literal

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult
from ..domain.preferences import PermissionRecord
from ..infrastructure.account_identity import current_account_key
from ..services.preference_transport import read_permission_catalog, read_preferences, write_preference
from ..services.preferences import PreferencesDependencies, PreferencesService
from .annotations import MUTATES_REMOTE

logger = logging.getLogger(__name__)


async def _catalog(client: Any, locale: str) -> tuple[PermissionRecord, ...]:
    return await read_permission_catalog(client, locale=locale)


def _service() -> PreferencesService:
    return PreferencesService(PreferencesDependencies(
        client_provider=get_gemini_client, client_initializer=initialize_client,
        read_permissions=read_preferences, read_catalog=_catalog,
        write_permission=write_preference, account_key_provider=current_account_key,
    ))


def register_preference_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=MUTATES_REMOTE)
    @domain_error_boundary("gemini_preferences", logger)
    async def gemini_preferences(
        action: Literal["get_browser", "set_browser", "list_permissions", "set_permission"] = "get_browser",
        browser: Literal["automatic", "remote_only"] | None = None,
        permission: Literal["always_ask", "always_allow"] | None = None,
        permission_id: str | None = None, expected_revision: str | None = None,
        limit: int = 20, offset: int = 0,
    ) -> list[TextContent]:
        """Read or explicitly change saved Task browser and app-action preferences.

        get_browser returns saved browser choice and permission_preference. set_browser
        supports automatic (local Chrome or remote) and remote_only. list_permissions
        is bounded and returns the exact opaque permission_id for set_permission.
        Use expected_revision to guard stale writes. always_allow requires explicit
        persistent authorization. Once/thread approvals use the original Task card.
        Writes dispatch once and independently read back the saved value. Unknown
        outcomes must not be replayed. effective_permission stays unknown and
        behavior_verified=false. Native confirmations may still appear. Verify app
        and website changes separately. Permission identifiers are opaque.
        """
        incompatible = (
            (action in {"get_browser", "list_permissions"}
             and any(value is not None for value in (browser, permission, permission_id, expected_revision)))
            or (action == "set_browser" and permission_id is not None)
            or (action == "set_permission" and browser is not None)
        )
        if incompatible:
            result: DomainResult[Any] = DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT, "Arguments do not match the requested preference action.")
        else:
            service = _service()
            if action == "get_browser":
                result = await service.get_browser()
            elif action == "list_permissions":
                result = await service.list_permissions(limit=limit, offset=offset)
            elif action == "set_browser":
                result = await service.set_browser(browser=browser, permission=permission,
                                                   expected_revision=expected_revision)
            elif action == "set_permission":
                if permission_id is None or permission is None:
                    result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                                                  "permission_id and permission are required.")
                else:
                    result = await service.set_permission(permission_id=permission_id, permission=permission,
                                                          expected_revision=expected_revision)
            else:
                result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Unknown preference action.")
        return domain_text(result, "Preference result." if result.ok else domain_failure_text(result),
                           use_result_data=True)
