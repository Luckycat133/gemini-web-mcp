"""Thin account adapter for native Gemini Skills."""

from typing import Any, Literal

from ..adapters import domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult
from ..infrastructure.managed_skills import read_skills
from ..services.managed_skills import ManagedSkillDependencies, ManagedSkillService
from ..services.managed_skill_transport import upload_skill, mutate_skill
from .annotations import DESTRUCTIVE_REMOTE


def _service() -> ManagedSkillService:
    return ManagedSkillService(ManagedSkillDependencies(
        get_gemini_client, initialize_client, read_skills, upload=upload_skill, mutate=mutate_skill))


def register_managed_skill_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    async def gemini_skills(action: Literal["list", "get", "create", "update", "enable", "delete"] = "list", skill_id: str | None = None,
                            include_instructions: bool = False, limit: int = 20, offset: int = 0,
                            max_chars: int = 4000, title: str | None = None,
                            description: str | None = None, instructions: str | None = None,
                            enabled: bool | None = None) -> list[TextContent]:
        """Read or explicitly manage native Gemini Skills.

        Instructions are private and only returned for an exact ID with explicit
        include_instructions=true. Lists are locally paginated. Writes require
        explicit intent and independent read-back; unverified writes are not replayed.
        """
        if action in {"list", "get"} and ((action == "get") != (skill_id is not None)
                                        or any(x is not None for x in (title, description, instructions, enabled))):
            result: DomainResult[Any] = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Skill action.")
        elif action in {"list", "get"}:
            result = await _service().read(skill_id=skill_id, include_instructions=include_instructions,
                                           limit=limit, offset=offset, max_chars=max_chars)
        elif include_instructions:
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Skill action.")
        else:
            result = await _service().mutate(action, skill_id=skill_id, title=title, description=description,
                                             instructions=instructions, enabled=enabled)
        return domain_text(result, "Skill request verified." if result.ok else domain_failure_text(result),
                           use_result_data=True)
