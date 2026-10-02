"""Action-specific input contracts for the seven explicit account facades."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonBlank = Annotated[str, Field(min_length=1)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(Request):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0, le=5000)] = 0


class HistoryList(Page):
    action: Literal["list"]


class HistorySearch(Page):
    action: Literal["search"]
    query: NonBlank
    scan_turns: bool = False
    turns_per_chat: Annotated[int, Field(ge=1, le=100)] = 20
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 1000


class HistoryRead(Request):
    action: Literal["read", "export"]
    chat_id: NonBlank
    limit: Annotated[int, Field(ge=1, le=200)] = 100
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 10000


class HistoryDelete(Request):
    action: Literal["delete"]
    chat_id: NonBlank


HistoryRequest: TypeAlias = Annotated[
    HistoryList | HistorySearch | HistoryRead | HistoryDelete, Field(discriminator="action"),
]


class NotebooksList(Page):
    action: Literal["list"]
    locale: NonBlank = "zh-CN"


class NotebooksChats(Page):
    action: Literal["chats"]
    notebook_id: NonBlank


class NotebooksMove(Request):
    action: Literal["move"]
    chat_id: NonBlank
    notebook_id: NonBlank
    locale: NonBlank = "zh-CN"


NotebooksRequest: TypeAlias = Annotated[
    NotebooksList | NotebooksChats | NotebooksMove, Field(discriminator="action"),
]


class ScheduledList(Page):
    action: Literal["list"]
    scope: Literal["active", "inactive", "all"] = "all"
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 1000


class ScheduledGet(Request):
    action: Literal["get", "delete"]
    action_id: NonBlank
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 1000


class ScheduledCreate(Request):
    action: Literal["create_daily"]
    title: NonBlank
    instructions: NonBlank
    hour: Annotated[int, Field(ge=0, le=23)]
    timezone_name: NonBlank = "Asia/Shanghai"
    locale: NonBlank = "zh-CN"


ScheduledRequest: TypeAlias = Annotated[
    ScheduledList | ScheduledGet | ScheduledCreate, Field(discriminator="action"),
]


class GemsList(Page):
    action: Literal["list"]


class GemsCreate(Request):
    action: Literal["create"]
    name: NonBlank
    instructions: NonBlank
    description: str = ""


class GemsUpdate(Request):
    action: Literal["update"]
    gem_id: NonBlank
    name: NonBlank | None = None
    instructions: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def require_change(self) -> "GemsUpdate":
        if all(getattr(self, field) is None for field in ("name", "instructions", "description")):
            raise ValueError("Supply at least one changed Gem field.")
        return self


class GemsDelete(Request):
    action: Literal["delete"]
    gem_id: NonBlank


GemsRequest: TypeAlias = Annotated[GemsList | GemsCreate | GemsUpdate | GemsDelete, Field(discriminator="action")]


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


class AccountRead(Request):
    action: Literal["status", "models", "capabilities"]


class AccountPage(Page):
    action: Literal["links", "library", "modes"]


class AccountUsage(Request):
    action: Literal["usage"]
    scope: Literal["quota", "model_state", "all"] = "all"


class AccountFeatures(Request):
    action: Literal["features"]
    surface: Literal[
        "all", "history", "library", "notebooks", "remy", "sharing", "usage",
        "personalization", "import", "scheduled", "tool_modes",
    ] = "all"


AccountRequest: TypeAlias = Annotated[
    AccountRead | AccountPage | AccountUsage | AccountFeatures, Field(discriminator="action"),
]


class CleanupStatus(Page):
    action: Literal["status"]
    states: list[str] = Field(default_factory=list)


class CleanupRun(Request):
    action: Literal["run"]
    job_id: NonBlank | None = None


class CleanupCancel(Request):
    action: Literal["cancel"]
    job_id: NonBlank


class CleanupTests(Request):
    action: Literal["test_artifacts"]
    markers: NonBlank
    target: Literal["all", "chats", "scheduled"] = "all"
    dry_run: bool = True
    max_chats: Annotated[int, Field(ge=1, le=100)] = 25
    scan_turns: bool = False


CleanupRequest: TypeAlias = Annotated[
    CleanupStatus | CleanupRun | CleanupCancel | CleanupTests, Field(discriminator="action"),
]
