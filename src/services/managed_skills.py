"""Account-fenced native Skill metadata reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
import io
import re
import zipfile
from typing import Any, Awaitable, Callable

from ..client_manager import ClientAuthenticationRequired
from ..domain import DomainErrorCode, DomainResult
from ..domain.managed_skills import ManagedSkill, ManagedSkillMutation, ManagedSkillPage
from ..infrastructure.account_identity import current_account_key
from ..services.deadlines import await_before_deadline


def _skill_description_yaml(description: str) -> str:
    """Match the native editor's YAML scalar formatting."""
    if "\n" in description:
        return "|\n" + "\n".join("  " + line for line in description.split("\n"))
    if re.search(r"[:#{}\[\],&*?|>!'\"%@`]", description) or description.startswith(("-", " ")):
        return '"' + description.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return description


@dataclass(frozen=True)
class ManagedSkillDependencies:
    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    read: Callable[..., Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key
    upload: Callable[..., Awaitable[str]] | None = None
    mutate: Callable[..., Awaitable[Any]] | None = None


class ManagedSkillService:
    def __init__(self, dependencies: ManagedSkillDependencies) -> None:
        self._deps = dependencies

    async def read(self, *, skill_id: str | None = None, include_instructions: bool = False,
                   limit: int = 20, offset: int = 0, max_chars: int = 4000) -> DomainResult[Any]:
        if (type(include_instructions) is not bool or type(limit) is not int or not 1 <= limit <= 100
                or type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 20000
                or (skill_id is not None and (not isinstance(skill_id, str) or not skill_id.strip()
                                              or len(skill_id) > 256))
                or (include_instructions and skill_id is None)):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Skill read arguments.")
        owner = self._deps.account_key_provider()
        try:
            try:
                await await_before_deadline(self._deps.client_initializer(), timeout=30)
            except ClientAuthenticationRequired:
                return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Gemini authentication is required.",
                                            verification_status="authentication_required")
            current = self._deps.account_key_provider()
            if not current or (owner and owner != current):
                raise ValueError
            owner = current
            client = self._deps.client_provider()
            if self._deps.account_key_provider() != owner:
                raise ValueError
            value = await await_before_deadline(self._deps.read(
                client, "managed_skills.get" if skill_id is not None else "managed_skills.list",
                skill_id=skill_id, include_instructions=include_instructions), timeout=35)
            if self._deps.account_key_provider() != owner:
                raise ValueError
            if skill_id is not None:
                if not isinstance(value, ManagedSkill) or value.skill_id != skill_id:
                    raise ValueError
                text = value.instructions
                truncated = text is not None and len(text) > max_chars
                value = replace(value, instructions=text[:max_chars] if include_instructions and text is not None else None)
                return DomainResult.success(value, verification_status="fresh_read", details={"instructions_truncated": truncated})
            if not isinstance(value, tuple) or any(not isinstance(item, ManagedSkill) for item in value):
                raise ValueError
            # Defend the public boundary even if an injected provider returns private instructions.
            items = tuple(replace(item, instructions=None) for item in value[offset:offset + limit])
            end = offset + len(items)
            return DomainResult.success(ManagedSkillPage(items, len(value), offset, end if end < len(value) else None),
                                        verification_status="fresh_read")
        except Exception:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Skill metadata could not be verified.",
                                        verification_status="read_unverified")


    async def mutate(self, action: str, *, skill_id: str | None = None, title: str | None = None,
                     description: str | None = None, instructions: str | None = None,
                     enabled: bool | None = None) -> DomainResult[Any]:
        valid_id = isinstance(skill_id, str) and bool(skill_id.strip()) and len(skill_id) <= 256
        if (action not in {"create", "update", "enable", "delete"}
                or (action == "create" and skill_id is not None)
                or (action != "create" and not valid_id)
                or (action == "enable" and type(enabled) is not bool)
                or (action != "enable" and enabled is not None)
                or (action in {"enable", "delete"} and any(x is not None for x in (title, description, instructions)))
                or (title is not None and (not isinstance(title, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", title) or len(title) > 64))
                or (description is not None and (not isinstance(description, str) or not description or len(description) > 800))
                or (instructions is not None and (not isinstance(instructions, str) or not instructions or len(instructions) > 20000))
                or (action == "create" and any(x is None for x in (title, description, instructions)))
                or (action == "update" and all(x is None for x in (title, description, instructions)))):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Skill mutation arguments.")
        if self._deps.mutate is None or (action in {"create", "update"} and self._deps.upload is None):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Skill mutation transport is unavailable.")
        owner = self._deps.account_key_provider()
        dispatched = False
        returned_id = skill_id
        try:
            try:
                await await_before_deadline(self._deps.client_initializer(), timeout=30)
            except ClientAuthenticationRequired:
                return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Gemini authentication is required.",
                                            verification_status="authentication_required")
            current = self._deps.account_key_provider()
            if not current or (owner and current != owner):
                raise ValueError
            owner = current
            client = self._deps.client_provider()
            if self._deps.account_key_provider() != owner:
                raise ValueError
            before = None
            if action != "create":
                before = await await_before_deadline(self._deps.read(client, "managed_skills.get", skill_id=skill_id,
                                                       include_instructions=action == "update"), timeout=35)
                if self._deps.account_key_provider() != owner or not isinstance(before, ManagedSkill) or before.skill_id != skill_id:
                    raise ValueError
            if action == "update":
                assert isinstance(before, ManagedSkill)
                if not before.plain_editable or before.instructions is None or before.enabled is None:
                    return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                                "This Skill has unverified attached content and cannot be safely updated.")
                title = title if title is not None else before.title
                description = description if description is not None else before.description
                instructions = instructions if instructions is not None else before.instructions
                if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", title) or len(title) > 64:
                    return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Existing Skill name cannot be safely edited.")
            uploaded = None
            if action in {"create", "update"}:
                assert self._deps.upload is not None
                assert title is not None and description is not None and instructions is not None
                content = f"---\nname: {title}\ndescription: {_skill_description_yaml(description)}\n---\n{instructions}\n"
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("SKILL.md", content)
                uploaded = await await_before_deadline(self._deps.upload(client, buffer.getvalue(), f"{title}.zip"), timeout=60)
                if self._deps.account_key_provider() != owner or not isinstance(uploaded, str) or not uploaded:
                    raise ValueError
            dispatched = True
            result = await await_before_deadline(self._deps.mutate(client, action, skill_id=skill_id, upload_handle=uploaded,
                                                    enabled=enabled if action == "enable" else (before.enabled if before else True)), timeout=30)
            if self._deps.account_key_provider() != owner:
                raise ValueError
            if action == "create":
                if not isinstance(result, str) or not result.strip() or len(result) > 256:
                    raise ValueError
                returned_id = result
            if action == "delete":
                after = await await_before_deadline(self._deps.read(client, "managed_skills.list", skill_id=None,
                                                      include_instructions=False), timeout=35)
                verified = isinstance(after, tuple) and all(isinstance(x, ManagedSkill) for x in after) and all(x.skill_id != skill_id for x in after)
            else:
                after = await await_before_deadline(self._deps.read(client, "managed_skills.get", skill_id=returned_id,
                                                      include_instructions=action in {"create", "update"}), timeout=35)
                verified = isinstance(after, ManagedSkill) and after.skill_id == returned_id
                if verified and action in {"create", "update"}:
                    verified = after.title == title and after.description == description and after.instructions == instructions
                if verified and action == "enable":
                    verified = after.enabled is enabled
            if self._deps.account_key_provider() != owner:
                raise ValueError
            data = ManagedSkillMutation(returned_id, action, bool(verified), True if verified else None)
            if verified:
                return DomainResult.success(data, verification_status="independent_readback")
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "Skill mutation could not be verified.",
                                        data=data, verification_status="mutation_unverified")
        except Exception:
            failure_data = ManagedSkillMutation(returned_id, action, False, None) if dispatched and self._deps.account_key_provider() == owner else None
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Skill mutation outcome is unavailable.",
                                        data=failure_data, verification_status="mutation_unverified" if dispatched else "preflight_unverified")
