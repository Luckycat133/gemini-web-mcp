"""Bounded native Gemini Skill read models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedSkill:
    skill_id: str
    title: str
    description: str
    enabled: bool | None
    version_id: str | None
    instructions: str | None = None
    plain_editable: bool = False


@dataclass(frozen=True)
class ManagedSkillPage:
    items: tuple[ManagedSkill, ...]
    total: int
    offset: int
    next_offset: int | None


@dataclass(frozen=True)
class ManagedSkillMutation:
    skill_id: str | None
    action: str
    verified: bool
    changed: bool | None
