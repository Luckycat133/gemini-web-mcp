"""The released v0.2.2 local Prompt request models."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


NonBlank = Annotated[str, Field(min_length=1)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(Request):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0, le=5000)] = 0


class PromptsList(Page):
    action: Literal["list", "categories"]
    category: str | None = None


class PromptsGet(Request):
    action: Literal["get", "delete"]
    prompt_id: NonBlank


class PromptsCreate(Request):
    action: Literal["create"]
    name: NonBlank
    content: NonBlank
    category: NonBlank = "general"
    description: str = ""


class PromptsUpdate(Request):
    action: Literal["update"]
    prompt_id: NonBlank
    name: NonBlank | None = None
    content: str | None = None
    category: NonBlank | None = None
    description: str | None = None

    @model_validator(mode="after")
    def require_change(self) -> "PromptsUpdate":
        if all(getattr(self, field) is None for field in ("name", "content", "category", "description")):
            raise ValueError("Supply at least one changed Prompt field.")
        return self


class PromptsRender(Request):
    action: Literal["render"]
    prompt_id: NonBlank
    variables: dict[str, str] = Field(default_factory=dict)


PromptsRequest: TypeAlias = Annotated[
    PromptsList | PromptsGet | PromptsCreate | PromptsUpdate | PromptsRender, Field(discriminator="action"),
]
