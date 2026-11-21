"""Action-specific input contracts for the seven explicit account facades."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlank = Annotated[str, Field(min_length=1)]
VerbatimText = Annotated[str, StringConstraints(strip_whitespace=False)]
NonEmptyVerbatimText = Annotated[VerbatimText, Field(min_length=1)]


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


class HistoryUpdate(Request):
    action: Literal["update"]
    chat_id: NonBlank
    title: NonBlank | None = None
    pinned: Annotated[bool, Field(strict=True)] | None = None

    @model_validator(mode="after")
    def require_metadata_change(self) -> "HistoryUpdate":
        if self.title is None and self.pinned is None:
            raise ValueError("A title or pinned value is required.")
        return self


class HistoryTaskStatus(Request):
    action: Literal["task_status"]
    chat_id: NonBlank


HistoryRequest: TypeAlias = Annotated[
    HistoryList | HistorySearch | HistoryRead | HistoryDelete | HistoryUpdate | HistoryTaskStatus, Field(discriminator="action"),
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


class NotebooksGet(Request):
    action: Literal["get", "sources"]
    notebook_id: NonBlank
    locale: NonBlank = "zh-CN"


class NotebooksCreate(Request):
    action: Literal["create"]
    title: NonBlank
    instructions: VerbatimText = ""
    locale: NonBlank = "zh-CN"


class NotebooksUpdate(Request):
    action: Literal["update"]
    notebook_id: NonBlank
    title: NonBlank | None = None
    instructions: VerbatimText | None = None
    locale: NonBlank = "zh-CN"

    @model_validator(mode="after")
    def require_change(self) -> "NotebooksUpdate":
        if all(getattr(self, field) is None for field in ("title", "instructions")):
            raise ValueError("Supply at least one changed notebook field.")
        return self


class NotebooksDelete(Request):
    action: Literal["delete"]
    notebook_id: NonBlank
    delete_chats: Annotated[bool, Field(strict=True)] = False
    locale: NonBlank = "zh-CN"


class NotebooksAddSource(Request):
    action: Literal["add_source"]
    notebook_id: NonBlank
    display_name: NonBlank
    text: NonEmptyVerbatimText | None = None
    url: NonBlank | None = None
    upload_reference: NonBlank | None = None
    file_path: NonBlank | None = None
    mime_type: NonBlank = "text/plain"
    locale: NonBlank = "zh-CN"

    @model_validator(mode="after")
    def require_one_source(self) -> "NotebooksAddSource":
        if sum(value is not None for value in (self.text, self.url, self.upload_reference, self.file_path)) != 1:
            raise ValueError("Supply exactly one text, url, upload_reference or file_path.")
        return self


class NotebooksRemoveSource(Request):
    action: Literal["remove_source"]
    notebook_id: NonBlank
    source_name: NonBlank
    locale: NonBlank = "zh-CN"


NotebooksRequest: TypeAlias = Annotated[
    NotebooksList | NotebooksChats | NotebooksMove | NotebooksGet | NotebooksCreate | NotebooksUpdate | NotebooksDelete
    | NotebooksAddSource | NotebooksRemoveSource,
    Field(discriminator="action"),
]


class ScheduledList(Page):
    action: Literal["list"]
    backend: Literal["legacy", "remy"] = "legacy"
    scope: Literal["active", "inactive", "all"] = "all"
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 1000


class ScheduledGet(Request):
    action: Literal["get", "delete"]
    action_id: NonBlank
    backend: Literal["legacy", "remy"] = "legacy"
    max_chars: Annotated[int, Field(ge=200, le=20000)] = 1000

class ScheduledCreate(Request):
    action: Literal["create_daily"]
    title: NonBlank
    instructions: NonBlank
    hour: Annotated[int, Field(ge=0, le=23)]
    timezone_name: NonBlank = "Asia/Shanghai"
    locale: NonBlank = "zh-CN"


class DailyTrigger(Request):
    type: Literal["daily"]
    hour: Annotated[int, Field(ge=0, le=23, strict=True)]
    minute: Annotated[int, Field(ge=0, le=59, strict=True)] = 0


class WeeklyTrigger(Request):
    type: Literal["weekly"]
    days_of_week: Annotated[list[Annotated[int, Field(ge=1, le=7, strict=True)]], Field(min_length=1, max_length=7)]
    hour: Annotated[int, Field(ge=0, le=23, strict=True)]
    minute: Annotated[int, Field(ge=0, le=59, strict=True)] = 0


class MonthlyTrigger(Request):
    type: Literal["monthly"]
    days_of_month: Annotated[list[Annotated[int, Field(ge=1, le=31, strict=True)]], Field(min_length=1, max_length=31)]
    hour: Annotated[int, Field(ge=0, le=23, strict=True)]
    minute: Annotated[int, Field(ge=0, le=59, strict=True)] = 0


class YearlyTrigger(Request):
    type: Literal["yearly"]
    month: Annotated[int, Field(ge=1, le=12, strict=True)]
    day: Annotated[int, Field(ge=1, le=31, strict=True)]
    hour: Annotated[int, Field(ge=0, le=23, strict=True)]
    minute: Annotated[int, Field(ge=0, le=59, strict=True)] = 0


class HourlyTrigger(Request):
    type: Literal["hourly"]
    minute: Annotated[int, Field(ge=0, le=59, strict=True)] = 0


class OneOffTrigger(Request):
    type: Literal["one_off"]
    unix_seconds: Annotated[int, Field(gt=0, strict=True)]


ScheduleTrigger: TypeAlias = Annotated[
    DailyTrigger | WeeklyTrigger | MonthlyTrigger | YearlyTrigger | HourlyTrigger | OneOffTrigger,
    Field(discriminator="type"),
]


class ScheduledNativeCreate(Request):
    action: Literal["create"]
    backend: Literal["remy"] = "remy"
    title: NonBlank
    instructions: NonBlank
    trigger: ScheduleTrigger
    timezone_name: NonBlank = "Asia/Shanghai"
    enabled: Annotated[bool, Field(strict=True)] = True


class ScheduledUpdate(Request):
    action: Literal["update"]
    backend: Literal["remy"] = "remy"
    action_id: NonBlank
    title: NonBlank | None = None
    instructions: NonBlank | None = None
    trigger: ScheduleTrigger | None = None
    timezone_name: NonBlank | None = None
    enabled: Annotated[bool, Field(strict=True)] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ScheduledUpdate":
        if all(getattr(self, field) is None for field in ("title", "instructions", "trigger", "timezone_name", "enabled")):
            raise ValueError("Supply at least one changed schedule field.")
        return self


class ScheduledToggle(Request):
    action: Literal["pause", "resume"]
    backend: Literal["remy"] = "remy"
    action_id: NonBlank


ScheduledRequest: TypeAlias = Annotated[
    ScheduledList | ScheduledGet | ScheduledCreate | ScheduledNativeCreate | ScheduledUpdate | ScheduledToggle,
    Field(discriminator="action"),
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


class NativeSkillsList(Page):
    action: Literal["list"]


class NativeSkillsTarget(Request):
    action: Literal["get", "enable", "disable", "delete"]
    skill_id: NonBlank


class NativeSkillsCreate(Request):
    action: Literal["create"]
    name: NonBlank
    description: NonBlank
    instructions: NonEmptyVerbatimText


class NativeSkillsUpdate(Request):
    action: Literal["update"]
    skill_id: NonBlank
    name: NonBlank | None = None
    description: NonBlank | None = None
    instructions: NonEmptyVerbatimText | None = None

    @model_validator(mode="after")
    def require_change(self) -> "NativeSkillsUpdate":
        if all(getattr(self, field) is None for field in ("name", "description", "instructions")):
            raise ValueError("Supply at least one changed native Skill field.")
        return self


class NativeSkillsUpload(Request):
    action: Literal["upload"]
    file_path: NonBlank
    skill_id: NonBlank | None = None


NativeSkillsRequest: TypeAlias = Annotated[
    NativeSkillsList | NativeSkillsTarget | NativeSkillsCreate | NativeSkillsUpdate | NativeSkillsUpload, Field(discriminator="action"),
]


class AccountSkills(Request):
    action: Literal["skills"]
    request: NativeSkillsRequest


class SharingList(Page):
    action: Literal["list"]


class SharingCreate(Request):
    action: Literal["create"]
    chat_id: NonBlank
    response_id: NonBlank | None = None
    title: NonBlank
    full_conversation: Annotated[bool, Field(strict=True)] = True
    locale: NonBlank = "zh-CN"


class SharingDelete(Request):
    action: Literal["delete"]
    token: NonBlank


SharingRequest: TypeAlias = Annotated[SharingList | SharingCreate | SharingDelete, Field(discriminator="action")]


class AccountSharing(Request):
    action: Literal["sharing"]
    request: SharingRequest


class LibraryList(Request):
    action: Literal["list"]
    section: Literal["all", "documents", "media", "purchases"] = "all"
    page_size: Annotated[int, Field(ge=1, le=100)] = 30
    next_page_token: NonBlank | None = None


class LibraryTarget(Request):
    action: Literal["get", "delete"]
    asset_id: NonBlank
    section: Literal["all", "documents", "media", "purchases"] = "all"
    max_pages: Annotated[int, Field(ge=1, le=50)] = 20


LibraryRequest: TypeAlias = Annotated[LibraryList | LibraryTarget, Field(discriminator="action")]


class AccountLibrary(Request):
    action: Literal["library_assets"]
    request: LibraryRequest


class CanvasRead(Request):
    action: Literal["get", "versions"]
    artifact_id: NonBlank


class CanvasChatArtifacts(Request):
    action: Literal["chat_artifacts"]
    chat_id: NonBlank
    limit: Annotated[int, Field(ge=1, le=100)] = 10


class CanvasCreate(Request):
    action: Literal["create"]
    prompt: NonBlank
    model: NonBlank = "flash"
    thinking_level: NonBlank = "standard"


class CanvasEdit(Request):
    action: Literal["edit"]
    artifact_id: NonBlank
    text: VerbatimText


class CanvasExport(Request):
    action: Literal["export"]
    artifact_id: NonBlank
    output_path: NonBlank


class AccountCanvas(Request):
    action: Literal["canvas"]
    request: Annotated[CanvasRead | CanvasChatArtifacts | CanvasCreate | CanvasEdit | CanvasExport, Field(discriminator="action")]


AccountRequest: TypeAlias = Annotated[
    AccountRead | AccountPage | AccountUsage | AccountFeatures | AccountSkills | AccountSharing | AccountLibrary | AccountCanvas,
    Field(discriminator="action"),
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
