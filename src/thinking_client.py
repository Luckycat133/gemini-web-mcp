"""Gemini Web thinking-level transport compatibility."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import re
from typing import Any, AsyncGenerator, Callable, Iterator

import orjson
from gemini_webapi import GeminiClient
from gemini_webapi.client import ChatSession
from gemini_webapi.constants import Endpoint, GRPC, Model, MODEL_HEADER_KEY
from gemini_webapi.exceptions import UsageLimitExceededError, TimeoutError as SDKTimeoutError
from gemini_webapi.types import AvailableModel, ModelOutput

from .services.cleanup_scope import observe_upstream_identity

from .constants import (
    gemini_4_model_family, is_auto_model_name, is_generation_failure_reply,
    resolve_learning_mode_config,
    resolve_thinking_level_id,
    resolve_thinking_mode_id,
    supported_learning_modes,
)
from .domain.results import classify_exception
from .infrastructure.rpc_contracts import (
    VideoTransportChanged, WebWorkflowTransportChanged, apply_image_generation_transport,
    apply_music_companion_options, apply_music_generation_transport, apply_notebook_attachments,
    apply_task_generation_transport, apply_video_generation_transport,
)
from .infrastructure.rpc_parsers import CandidateSourceEvidence, parse_candidate_attribution_sources
from .infrastructure.media_companions import MediaCompanionCatalog, parse_media_companions
from .infrastructure.account_identity import current_account_key
from .infrastructure.model_policy_store import ModelPolicyStore
from .services.model_policy import (
    ModelPolicyError, ModelSelection, account_model_tier, current_model_policy,
    membership_restriction, model_policy_family,
)


@dataclass(frozen=True)
class WebRequestOptions:
    """Gemini Web fields that are not exposed by gemini-webapi yet."""

    thinking_mode_id: int | None = None
    thinking_level_id: int | None = None
    thinking_level_name: str | None = None
    learning_mode_id: int | None = None
    learning_x9b_field: str | None = None
    learning_x9b_value: int | None = None
    image_generation: bool = False
    music_generation: bool = False
    media_option_ids: tuple[int, ...] = ()
    video_generation: bool = False
    video_duration_seconds: int | None = None
    video_aspect_ratio: str | None = None
    video_format_code: int | None = None
    task_generation: bool = False
    task_conversation_id: str | None = None
    task_action_choice_id: str | None = None
    notebooks: tuple[tuple[str, str], ...] = ()


@dataclass
class _MediaReadback:
    active: bool = True
    rate_limit: Exception | None = None


_web_request: ContextVar[WebRequestOptions | None] = ContextVar(
    "gemini_web_request",
    default=None,
)
_video_generation: ContextVar[bool] = ContextVar("gemini_video_generation", default=False)
_video_duration_seconds: ContextVar[int | None] = ContextVar("gemini_video_duration_seconds", default=None)
_video_aspect_ratio: ContextVar[str | None] = ContextVar("gemini_video_aspect_ratio", default=None)
_task_generation: ContextVar[bool] = ContextVar("gemini_task_generation", default=False)
_task_conversation: ContextVar[str | None] = ContextVar("gemini_task_conversation", default=None)
_task_action_choice: ContextVar[str | None] = ContextVar("gemini_task_action_choice", default=None)
_notebook_context: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("gemini_notebook_context", default=())
_candidate_evidence: ContextVar[dict[str, tuple[CandidateSourceEvidence, ...]] | None] = ContextVar(
    "gemini_candidate_evidence", default=None
)
_strict_session_recovery: ContextVar[bool] = ContextVar("gemini_strict_session_recovery", default=False)
_initializing_client: ContextVar[GeminiClient | None] = ContextVar("gemini_initializing_client", default=None)
_media_readback: ContextVar[_MediaReadback | None] = ContextVar("gemini_media_readback", default=None)
_mutation_dispatch_owner: ContextVar[tuple[str, Callable[[], str | None]] | None] = ContextVar(
    "gemini_mutation_dispatch_owner", default=None
)


@contextmanager
def mutation_scope(*, owner: str | None = None,
                   account_key_provider: Callable[[], str | None] | None = None) -> Iterator[None]:
    """Keep a single account and disable SDK retries during a mutation."""
    provider = account_key_provider or current_account_key
    bound = owner or provider()
    if not bound or provider() != bound:
        raise PermissionError("Gemini account changed or is unavailable.")
    token = _mutation_dispatch_owner.set((bound, provider))
    try:
        yield
    finally:
        _mutation_dispatch_owner.reset(token)


gem_mutation_scope = mutation_scope


@contextmanager
def _media_readback_scope() -> Iterator[None]:
    """Let the media observer handle retries and surface rate limits hidden by SDK parsers."""
    readback = _MediaReadback()
    token = _media_readback.set(readback)
    try:
        yield
        if readback.rate_limit is not None:
            raise readback.rate_limit
    finally:
        readback.active = False
        _media_readback.reset(token)


@contextmanager
def strict_session_recovery_scope() -> Iterator[None]:
    """Leave incomplete session streams to identity-bound application recovery."""
    token = _strict_session_recovery.set(True)
    try:
        yield
    finally:
        _strict_session_recovery.reset(token)


_FLASH_COMPAT_NAMES = frozenset({
    "flash", "fast", "3.5 flash", "3.8 flash", "gemini-3-flash",
    "gemini-3-flash-thinking", "thinking", "gemini-flash",
})


def _policy_family(model: AvailableModel) -> str:
    return model_policy_family(model)


def _task_model_supported(model: AvailableModel) -> bool:
    return model_policy_family(model) in {"flash", "argon", "neon", "carbon"}


def _encode_learning_x9b(field_name: str, value: int) -> list[Any]:
    """Encode the frontend X9b companion request field in JSPB array form."""
    if field_name == "zUa":
        return [[[[[value]]]]]
    if field_name == "QLd":
        return [[[None, [value]]]]
    if field_name == "LYd":
        return [[[None, None, [value]]]]
    if field_name == "h5d":
        return [[[None, None, None, [value]]]]
    raise ValueError(f"unsupported Gemini learning transport field: {field_name}")


def _encode_learning_goa(mode_id: int) -> list[Any]:
    """Encode GOa.H4 selected companion ids in JSPB array form."""
    return [[mode_id]]


def inject_thinking_level(
    request_data: dict[str, Any],
    *,
    mode_id: int,
    level_id: int,
) -> dict[str, Any]:
    """Add the Web UI thinking-level fields to a StreamGenerate request."""
    return inject_web_request_options(
        request_data,
        WebRequestOptions(thinking_mode_id=mode_id, thinking_level_id=level_id),
    )


def inject_web_request_options(
    request_data: dict[str, Any],
    options: WebRequestOptions,
) -> dict[str, Any]:
    """Add Web UI-only fields to a StreamGenerate request."""
    f_req = request_data.get("f.req")
    if options.task_action_choice_id is not None and not options.task_generation:
        raise WebWorkflowTransportChanged("Task confirmation choice requires Task continuation.")
    workflow = options.image_generation or options.music_generation or options.task_generation or bool(options.notebooks)
    if not isinstance(f_req, str):
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow request is unavailable.")
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport request is unavailable")
        return request_data

    try:
        outer_request = orjson.loads(f_req)
    except orjson.JSONDecodeError:
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow envelope changed.") from None
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport envelope changed") from None
        raise
    if not isinstance(outer_request, list) or len(outer_request) < 2:
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow envelope changed.")
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport envelope changed")
        return request_data

    inner_payload = outer_request[1]
    if not isinstance(inner_payload, str):
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow payload is unavailable.")
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport payload is unavailable")
        return request_data

    try:
        inner_request = orjson.loads(inner_payload)
    except orjson.JSONDecodeError:
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow payload changed.") from None
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport payload changed") from None
        raise
    if not isinstance(inner_request, list):
        if workflow:
            raise WebWorkflowTransportChanged("Web workflow request changed.")
        if options.video_generation:
            raise VideoTransportChanged("Video generation transport request shape changed")
        return request_data

    if options.music_generation:
        if options.image_generation or options.video_generation or options.task_generation or options.learning_mode_id or options.notebooks:
            raise WebWorkflowTransportChanged("Music generation requires an ordinary music request.")
        apply_music_generation_transport(inner_request)

    if options.image_generation:
        if options.video_generation or options.task_generation or options.learning_mode_id or options.notebooks:
            raise WebWorkflowTransportChanged("Image generation requires an ordinary image request.")
        apply_image_generation_transport(inner_request)

    if (len(inner_request) >= 81 and not (workflow or options.video_generation or options.learning_mode_id)
            and type(inner_request[79]) is int and inner_request[79] > 0
            and type(inner_request[80]) is int and inner_request[80] == options.thinking_level_id):
        # The SDK already writes low and high to the ordinary request body.
        return request_data
    if len(inner_request) < 81:
        inner_request.extend([None] * (81 - len(inner_request)))

    if options.learning_mode_id:
        if not options.learning_x9b_field or options.learning_x9b_value is None:
            raise ValueError("learning mode transport metadata is incomplete")
        inner_request[54] = _encode_learning_x9b(
            options.learning_x9b_field,
            options.learning_x9b_value,
        )
        inner_request[55] = _encode_learning_goa(options.learning_mode_id)

    if options.thinking_level_id:
        selector = inner_request[79]
        if (type(selector) is not int or selector <= 0) and options.thinking_mode_id:
            inner_request[79] = options.thinking_mode_id
        inner_request[80] = options.thinking_level_id

    if options.video_generation:
        if options.learning_mode_id:
            raise ValueError("Video generation cannot use a learning mode")
        apply_video_generation_transport(
            inner_request, duration_seconds=options.video_duration_seconds, aspect_ratio=options.video_aspect_ratio,
            **({"format_code": options.video_format_code} if options.video_format_code is not None else {}),
        )

    if options.media_option_ids:
        if not (options.image_generation or options.music_generation or options.video_generation):
            raise WebWorkflowTransportChanged("Media companion choices require a media composer.")
        if options.music_generation:
            apply_music_companion_options(inner_request, options.media_option_ids)
        else:
            inner_request[55] = [list(options.media_option_ids)]

    if options.task_generation:
        if options.video_generation or options.learning_mode_id:
            raise ValueError("Task mode cannot be combined with another generation feature.")
        apply_task_generation_transport(inner_request, conversation_id=options.task_conversation_id,
                                        action_choice_id=options.task_action_choice_id)
    if options.notebooks:
        apply_notebook_attachments(inner_request, options.notebooks, use_file_reference=not options.task_generation)

    patched = dict(request_data)
    outer_request[1] = orjson.dumps(inner_request).decode("utf-8")
    patched["f.req"] = orjson.dumps(outer_request).decode("utf-8")
    return patched


class _ObservedChatSession(ChatSession):
    """Expose SDK metadata as it arrives, including before its first output."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in {"metadata", "cid"}:
            observe_upstream_identity(self)


class ThinkingLevelGeminiClient(GeminiClient):
    """Gemini client that carries the current Web UI thinking-level selector."""

    async def _init_rpc(self) -> None:
        token = _initializing_client.set(self)
        try:
            await super()._init_rpc()
        finally:
            _initializing_client.reset(token)

    async def _batch_execute(self, *args: Any, **kwargs: Any) -> Any:
        scoped = _mutation_dispatch_owner.get()
        owner, provider = scoped if scoped else (None, current_account_key)
        readback = _media_readback.get()
        if readback is not None and not readback.active:
            readback = None
        if owner and provider() != owner:
            raise PermissionError("Gemini account changed or is unavailable.")
        if owner or readback is not None or _initializing_client.get() is self:
            # Scoped mutations and media observers own retries. During init,
            # the SDK could close and reenter init while holding the same lock.
            kwargs["current_retry"] = 0
        if readback is not None:
            kwargs["close_on_error"] = False
        try:
            result = await super()._batch_execute(*args, **kwargs)
        except Exception as error:
            if owner and provider() != owner:
                raise PermissionError("Gemini account changed or is unavailable.") from None
            if readback is not None and readback.active and classify_exception(error).error_code == "RATE_LIMITED":
                readback.rate_limit = error
            raise
        if owner and provider() != owner:
            raise PermissionError("Gemini account changed or is unavailable.")
        return result

    def start_chat(self, **kwargs: Any) -> ChatSession:
        return _ObservedChatSession(geminiclient=self, **kwargs)

    def create_cleanup_chat(self) -> ChatSession:
        return self.start_chat()

    async def read_chat(self, cid: str, limit: int = 10) -> Any:
        if _strict_session_recovery.get():
            # The SDK fallback accepts any latest candidate other than the
            # previous one and then rewrites its RID. Do not let it manufacture
            # a completed session reply from another turn. The caller retains
            # observed identities and performs exact read-only recovery.
            raise SDKTimeoutError("The session stream is incomplete; exact-turn recovery is required.")
        return await super().read_chat(cid, limit=limit)

    media_readback_scope = staticmethod(_media_readback_scope)

    async def read_chat_for_media(self, cid: str, limit: int = 10) -> Any:
        with self.media_readback_scope():
            return await self.read_chat(cid, limit=limit)

    async def _generate(self, *args: Any, **kwargs: Any) -> AsyncGenerator[ModelOutput, None]:
        # A failed stream can already have created a remote turn. Replaying it
        # inside the SDK retry decorator could start the generation twice.
        kwargs["current_retry"] = 0
        if not self._running:
            await self.init(timeout=self.timeout, auto_close=self.auto_close, close_delay=self.close_delay,
                            auto_refresh=self.auto_refresh, refresh_interval=self.refresh_interval,
                            verbose=self.verbose, watchdog_timeout=self.watchdog_timeout)
        if not self._running:
            raise ModelPolicyError("The model registry is unavailable before generation.")
        requested = kwargs.get("model")
        if requested is None or requested == Model.UNSPECIFIED:
            requested_name = "flash"
            selected = self._resolve_policy_model("flash")
        elif isinstance(requested, str):
            requested_name = requested
            selected = self._resolve_policy_model(requested)
        elif isinstance(requested, Model):
            requested_name = requested.model_name
            resolved = self._resolve_enum_model(requested)
            selected = self._resolve_policy_model(resolved)
        elif isinstance(requested, dict):
            try:
                descriptor = AvailableModel.from_dict(requested)
            except (ValueError, TypeError) as error:
                raise ModelPolicyError("The requested model is unavailable in the discovered registry.") from error
            requested_name = descriptor.model_name
            selected = self._resolve_policy_model(descriptor)
        else:
            requested_name = str(getattr(requested, "model_name", ""))
            selected = self._resolve_policy_model(requested)
        account = current_account_key()
        policy = current_model_policy()
        store = ModelPolicyStore()
        request_started = store.clock()
        family = _policy_family(selected)
        tier = account_model_tier(self)
        if restriction := membership_restriction(tier, family, policy.purpose):
            raise ModelPolicyError(restriction)
        if family == "lite" and policy.purpose in {"task", "notebook_chat"}:
            raise ModelPolicyError("Flash-Lite is unavailable for Task workflows.")
        if policy.purpose == "task" and not _task_model_supported(selected):
            raise ModelPolicyError("The requested model is unavailable for Task workflows.")
        exhausted = store.flash_exhausted(account) if family in {"flash", "lite"} else False
        reason = None
        if family == "lite" and not exhausted:
            raise ModelPolicyError("Flash-Lite model unavailable without verified Flash quota exhaustion.")
        options = _web_request.get()
        plain_chat = (policy.purpose in {"query", "unspecified"} and not kwargs.get("deep_research")
                      and not kwargs.get("req_file_data") and not _notebook_context.get()
                      and not (options and (options.learning_mode_id or options.image_generation
                                           or options.music_generation or options.video_generation)))
        explicit_hydrogen = (requested_name.strip().lower() not in _FLASH_COMPAT_NAMES
                             and any(gemini_4_model_family(label) == "hydrogen"
                                     for label in (selected.model_name, selected.display_name)))
        if (family == "flash" and exhausted and policy.purpose == "query" and policy.allow_fallback
                and plain_chat and not explicit_hydrogen):
            selected = self._resolve_policy_model("gemini-flash-lite")
            if _policy_family(selected) != "lite":
                raise ModelPolicyError("The requested fallback model is unavailable.")
            family, reason = "lite", "flash_quota_exhausted"
        kwargs["model"] = selected
        options = _web_request.get()
        if options and (options.image_generation or options.music_generation or options.video_generation):
            media_choices = self._media_companion_options(options)
            options = replace(options, media_option_ids=media_choices,
                              video_format_code=2 if options.video_generation and 67 in media_choices else None)
        selectable = family not in {"lite", "auto"}
        if not selectable:
            kwargs["extended_thinking"] = False
            if options is not None:
                options = replace(options, thinking_mode_id=None, thinking_level_id=None, thinking_level_name=None)
        token = _web_request.set(options)
        selection = ModelSelection(requested_model=requested_name, effective_model=selected.model_name,
                                   fallback_reason=reason, thinking_selectable=selectable,
                                   effective_thinking_level=((options.thinking_level_name if options else None)
                                   or ("extended" if kwargs.get("extended_thinking") else "standard"))
                                   if selectable else None)
        completed = False
        observed_success = False
        basic_thinking = (options is None or options.thinking_level_id in (None, 1)) and not kwargs.get(
            "extended_thinking"
        )
        try:
            async for response in super()._generate(*args, **kwargs):
                reply_text = getattr(response, "text", "")
                if reply_text:
                    observed_success = not is_generation_failure_reply(reply_text)
                object.__setattr__(response, "_mcp_model_selection", selection)
                observe_upstream_identity(response)
                yield response
            completed = True
        except UsageLimitExceededError as error:
            setattr(error, "code", "UPSTREAM_LIMIT_EXCEEDED")
            if (family == "flash" and policy.purpose == "query" and account
                    and current_account_key() == account and not kwargs.get("deep_research")
                    and basic_thinking
                    and not kwargs.get("req_file_data")
                    and not (options and options.learning_mode_id)
                    and not _video_generation.get()):
                store.record_flash_exhausted(account)
            raise
        finally:
            _web_request.reset(token)
            if (completed and observed_success and family == "flash" and policy.purpose == "query"
                    and not kwargs.get("deep_research") and basic_thinking
                    and not kwargs.get("req_file_data") and not (options and options.learning_mode_id)
                    and not _video_generation.get() and account and current_account_key() == account):
                store.clear_flash_exhausted(account, observed_before=request_started)

    def _resolve_policy_model(self, value: Any) -> AvailableModel:
        try:
            selected = self._resolve_model_by_name(value) if isinstance(value, str) else value
        except (ValueError, LookupError) as error:
            raise ModelPolicyError("The requested model is unavailable in the discovered registry.") from error
        registry = getattr(self, "_model_registry", {})
        if not isinstance(selected, AvailableModel) or not isinstance(registry, dict):
            raise ModelPolicyError("The requested model is unavailable in the discovered registry.")
        matches = [model for model in registry.values() if isinstance(model, AvailableModel)
                   and model.model_id == selected.model_id]
        if len(matches) != 1 or not matches[0].is_available:
            raise ModelPolicyError("The requested model is unavailable in the discovered registry.")
        return matches[0]

    def _resolve_model_by_name(self, name: str) -> AvailableModel:
        """Resolve known model families without the SDK's ambiguous token fallback.

        SDK 2.1.1 can match ``gemini-3-flash`` to ``gemini-flash-lite``
        before it reaches ``gemini-flash``. Only select a discovered model
        whose exact name or explicit alias identifies the requested family.
        """
        normalized = name.strip().lower()
        families = {
            **dict.fromkeys(_FLASH_COMPAT_NAMES, "gemini-flash"),
            "pro": "gemini-pro",
            "3.1 pro": "gemini-pro",
            "gemini-3-pro": "gemini-pro",
            "gemini-pro": "gemini-pro",
            "lite": "gemini-flash-lite",
            "3.1 flash-lite": "gemini-flash-lite",
            "3.5 flash-lite": "gemini-flash-lite",
            "flash-lite": "gemini-flash-lite",
            "gemini-flash-lite": "gemini-flash-lite",
            "gemini-3-flash-lite": "gemini-flash-lite",
        }
        canonical = families.get(normalized)
        models = list(getattr(self, "_model_registry", {}).values())
        if canonical == "gemini-flash":
            models = [model for model in models if model.is_available]
            policy = current_model_policy()
            media_default = policy.purpose == "media" and normalized in _FLASH_COMPAT_NAMES
            if normalized in {"flash", "fast"} or policy.requested_alias == "flash" or media_default:
                hydrogen = self._gemini_4_family_models(models, "hydrogen")
                if hydrogen:
                    return self._unique_discovered_model(hydrogen, "hydrogen")
                if media_default and _video_generation.get():
                    raise ModelPolicyError(
                        "Hydrogen is required for Gemini Web video generation and is unavailable in the discovered registry."
                    )
        if canonical is None:
            if is_auto_model_name(normalized):
                return self._unique_discovered_model(
                    [model for model in models if model.is_available and model_policy_family(model) == "auto"], name)
            family = gemini_4_model_family(normalized)
            if family:
                return self._resolve_gemini_4_family(models, family)
            exact_matches = [model for model in models if normalized in {
                model.model_id.lower(), model.model_name.lower(), model.display_name.lower(),
            }]
            aliases = [model for model in models if normalized in {alias.lower() for alias in model.aliases}]
            return self._unique_discovered_model(exact_matches or aliases, name)
        for exact in (normalized, canonical):
            matches = [model for model in models if model.model_name.lower() == exact]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(f"Ambiguous discovered model: {name}")
        alias_matches = [
            model for model in models
            if normalized in {str(alias).lower() for alias in model.aliases}
            and re.sub(r"^gemini-(?:\d+(?:\.\d+)?-)?", "", model.model_name.lower())
            == canonical.removeprefix("gemini-")
        ]
        if len(alias_matches) == 1:
            return alias_matches[0]
        if len(alias_matches) > 1:
            raise ValueError(f"Ambiguous discovered model: {name}")
        if canonical == "gemini-flash":
            return self._resolve_gemini_4_family(models, "hydrogen")
        raise ValueError(f"Requested model is not available in the discovered registry: {name}")

    @staticmethod
    def _unique_discovered_model(models: list[AvailableModel], name: str) -> AvailableModel:
        if len(models) == 1:
            return models[0]
        if models:
            raise ValueError(f"Ambiguous discovered model: {name}")
        raise ValueError(f"Requested model is not available in the discovered registry: {name}")

    @staticmethod
    def _gemini_4_family_models(models: list[AvailableModel], family: str) -> list[AvailableModel]:
        matches = []
        for model in models:
            names = {gemini_4_model_family(label) for label in (model.model_name, model.display_name)}
            if names - {None} == {family} and model_policy_family(model) != "lite":
                matches.append(model)
        return matches

    def _resolve_gemini_4_family(self, models: list[AvailableModel], family: str) -> AvailableModel:
        return self._unique_discovered_model(self._gemini_4_family_models(models, family), family)

    def _parse_rpc_results(self, response_text: str, target_id: str) -> Iterator[Any]:
        for body in super()._parse_rpc_results(response_text, target_id):
            if target_id == GRPC.GET_USER_STATUS:
                parsed = parse_media_companions(body)
                self._mcp_media_companions = parsed.value
            yield body

    def _media_companion_options(self, options: WebRequestOptions) -> tuple[int, ...]:
        catalog = getattr(self, "_mcp_media_companions", None)
        if not isinstance(catalog, MediaCompanionCatalog):
            return ()
        mode = 11 if options.video_generation else 14 if options.image_generation else 21
        selected = list(catalog.default_option_ids(mode))
        if mode == 11:
            formats = [group for group in catalog.by_mode.get(mode, ()) if group.id == 21 and group.available]
            if formats:
                video_choices = [choice for group in formats for choice in group.options if choice.id == 67 and choice.available]
                if len(video_choices) != 1:
                    raise ModelPolicyError("Video format is unavailable in the current Web catalog.")
                selected = [item for item in selected if item not in {choice.id for group in formats for choice in group.options}]
                selected.append(video_choices[0].id)
            for group_id, requested in ((11, options.video_duration_seconds), (12, options.video_aspect_ratio)):
                if requested is None:
                    continue
                groups = [group for group in catalog.by_mode.get(mode, ()) if group.id == group_id and group.available]
                if len(groups) != 1:
                    raise ModelPolicyError("The requested video option is unavailable in the current Web catalog.")
                group = groups[0]
                matches = [choice for choice in group.options if choice.available and (
                    re.search(rf"(?<![0-9]){requested}(?![0-9])", choice.title) if group_id == 11
                    else str(requested) in choice.title)]
                if len(matches) != 1:
                    raise ModelPolicyError("The requested video option is unavailable in the current Web catalog.")
                selected = [item for item in selected if item not in {choice.id for choice in group.options}]
                selected.append(matches[0].id)
        return tuple(dict.fromkeys(selected))

    def _parse_candidate(self, candidate_data: list[Any], cid: str, rid: str, rcid: str) -> Any:
        result = super()._parse_candidate(candidate_data, cid, rid, rcid)
        evidence = _candidate_evidence.get()
        if evidence is not None:
            evidence[rcid] = parse_candidate_attribution_sources(candidate_data)
        return result

    async def init(self, *args: Any, **kwargs: Any) -> None:
        await super().init(*args, **kwargs)
        self._install_thinking_transport()

    async def generate_content(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        learning_mode: str | None = None,
        **kwargs: Any,
    ) -> ModelOutput:
        if _video_generation.get() or _task_generation.get():
            if kwargs.get("deep_research"):
                raise ValueError("A dedicated generation feature cannot start Deep Research")
            kwargs["deep_research"] = True
        token = self._set_web_request(model, thinking_level, learning_mode)
        try:
            self._apply_native_thinking_level(kwargs, thinking_level)
            args, kwargs = self._with_learning_prompt(args, kwargs, learning_mode)
            if model is None:
                return await super().generate_content(*args, **kwargs)
            return await super().generate_content(*args, model=model, **kwargs)
        finally:
            _web_request.reset(token)

    async def generate_content_stream(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        learning_mode: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ModelOutput, None]:
        if _video_generation.get() or _task_generation.get():
            if kwargs.get("deep_research"):
                raise ValueError("A dedicated generation feature cannot start Deep Research")
            kwargs["deep_research"] = True
        token = self._set_web_request(model, thinking_level, learning_mode)
        evidence_token = _candidate_evidence.set({})
        try:
            self._apply_native_thinking_level(kwargs, thinking_level)
            args, kwargs = self._with_learning_prompt(args, kwargs, learning_mode)
            if model is None:
                stream = super().generate_content_stream(*args, **kwargs)
            else:
                stream = super().generate_content_stream(*args, model=model, **kwargs)
            async for output in stream:
                evidence = _candidate_evidence.get() or {}
                candidates = getattr(output, "candidates", ())
                if evidence and isinstance(candidates, (list, tuple)):
                    for candidate in candidates:
                        rcid = getattr(candidate, "rcid", None)
                        if isinstance(rcid, str) and rcid in evidence:
                            object.__setattr__(candidate, "_retrieved_sources", evidence.pop(rcid))
                yield output
        finally:
            _candidate_evidence.reset(evidence_token)
            _web_request.reset(token)

    @staticmethod
    def _apply_native_thinking_level(kwargs: dict[str, Any], thinking_level: str | None) -> None:
        """Keep the SDK model-header selector aligned with the active project level."""
        options = _web_request.get()
        level = resolve_thinking_level_id(thinking_level) if thinking_level is not None else (
            options.thinking_level_id if options else None
        )
        if level is not None:
            kwargs["extended_thinking"] = level == 2

    def _set_web_request(
        self,
        model: Any,
        thinking_level: str | None,
        learning_mode: str | None,
    ):
        previous = _web_request.get()
        if thinking_level is None and learning_mode is None and previous:
            if _video_generation.get() and previous.learning_mode_id:
                raise ValueError("Video generation cannot use a learning mode")
            if _video_generation.get():
                return _web_request.set(replace(previous, video_generation=True,
                    video_duration_seconds=_video_duration_seconds.get(), video_aspect_ratio=_video_aspect_ratio.get()))
            return _web_request.set(previous)

        level_id = resolve_thinking_level_id(thinking_level)
        if thinking_level is not None and level_id is None:
            raise ValueError("thinking_level 仅支持 low/medium/high 或 standard/extended。")

        learning_config = resolve_learning_mode_config(learning_mode)
        if learning_mode is not None and learning_config is None:
            raise ValueError(f"learning_mode 仅支持 {supported_learning_modes()}。")
        if _video_generation.get() and learning_config is not None:
            raise ValueError("Video generation cannot use a learning mode")

        mode_id = resolve_thinking_mode_id(model)
        options = WebRequestOptions(
            thinking_mode_id=mode_id,
            thinking_level_id=level_id,
            thinking_level_name=({"低": "low", "中": "medium", "高": "high", "标准": "standard",
                                  "扩展": "extended"}.get(thinking_level.strip().lower(),
                                                        thinking_level.strip().lower())
                                 if thinking_level is not None else None),
            learning_mode_id=(
                int(learning_config["id"]) if learning_config is not None else None  # type: ignore[call-overload]
            ),
            learning_x9b_field=(
                str(learning_config["x9b_field"]) if learning_config is not None else None
            ),
            learning_x9b_value=(
                int(learning_config["x9b_value"]) if learning_config is not None else None  # type: ignore[call-overload]
            ),
            image_generation=bool(previous and previous.image_generation),
            music_generation=bool(previous and previous.music_generation),
            video_generation=_video_generation.get(),
            video_duration_seconds=_video_duration_seconds.get(),
            video_aspect_ratio=_video_aspect_ratio.get(),
            task_generation=_task_generation.get(),
            task_conversation_id=_task_conversation.get(),
            task_action_choice_id=_task_action_choice.get(),
            notebooks=_notebook_context.get(),
        )
        if not any(
            (
                options.thinking_level_id,
                options.learning_mode_id,
                options.image_generation,
                options.music_generation,
                options.video_generation,
                options.task_generation,
                options.notebooks,
            )
        ):
            return _web_request.set(None)
        return _web_request.set(options)

    @contextmanager
    def thinking_scope(self, model: Any, thinking_level: str) -> Iterator[None]:
        """Keep a thinking level active through upstream helper workflows."""
        token = self._set_web_request(model, thinking_level, None)
        try:
            yield
        finally:
            _web_request.reset(token)

    @contextmanager
    def image_generation_scope(self) -> Iterator[None]:
        """Select the Web image tool for this request."""
        token = _web_request.set(replace(_web_request.get() or WebRequestOptions(), image_generation=True))
        try:
            yield
        finally:
            _web_request.reset(token)

    @contextmanager
    def music_generation_scope(self) -> Iterator[None]:
        """Select the Web Create music tool independently of chat thinking."""
        token = _web_request.set(replace(_web_request.get() or WebRequestOptions(), music_generation=True))
        try:
            yield
        finally:
            _web_request.reset(token)

    @contextmanager
    def video_generation_scope(self, *, duration_seconds: int | None = None,
                               aspect_ratio: str | None = None) -> Iterator[None]:
        """Select the observed Web video mode and optional composer choices."""
        token = _video_generation.set(True)
        duration_token = _video_duration_seconds.set(duration_seconds)
        aspect_token = _video_aspect_ratio.set(aspect_ratio)
        try:
            yield
        finally:
            _video_aspect_ratio.reset(aspect_token)
            _video_duration_seconds.reset(duration_token)
            _video_generation.reset(token)

    @contextmanager
    def task_generation_scope(self, conversation_id: str | None = None, *,
                              action_choice_id: str | None = None) -> Iterator[None]:
        """Select the observed Task workflow without changing model or preferences."""
        token = _task_generation.set(True)
        context_token = _task_conversation.set(conversation_id)
        choice_token = _task_action_choice.set(action_choice_id)
        try:
            yield
        finally:
            _task_generation.reset(token)
            _task_conversation.reset(context_token)
            _task_action_choice.reset(choice_token)

    @contextmanager
    def notebook_scope(self, notebooks: tuple[tuple[str, str], ...]) -> Iterator[None]:
        """Carry freshly resolved notebook references for this request only."""
        token = _notebook_context.set(notebooks)
        try:
            yield
        finally:
            _notebook_context.reset(token)

    def _with_learning_prompt(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        learning_mode: str | None,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        config = resolve_learning_mode_config(learning_mode)
        if not config:
            return args, kwargs

        prefix = str(config["prompt_prefix"])
        if "prompt" in kwargs:
            patched = dict(kwargs)
            patched["prompt"] = self._prefix_learning_prompt(prefix, patched.get("prompt"))
            return args, patched

        if args:
            patched_args = list(args)
            patched_args[0] = self._prefix_learning_prompt(prefix, patched_args[0])
            return tuple(patched_args), kwargs

        return args, kwargs

    @staticmethod
    def _prefix_learning_prompt(prefix: str, prompt: Any) -> Any:
        if not isinstance(prompt, str) or prompt.startswith(prefix):
            return prompt
        return f"{prefix}{prompt}"

    def _install_thinking_transport(self) -> None:
        session = self.client
        if not session or getattr(session, "_mcp_thinking_stream", False):
            return

        stream = session.stream

        def stream_with_thinking(method: str, url: str, *args: Any, **kwargs: Any):
            request = _web_request.get()
            data = kwargs.get("data")
            if (request and (request.image_generation or request.music_generation or request.task_generation or request.notebooks) and url == Endpoint.GENERATE
                    and not isinstance(data, dict)):
                raise WebWorkflowTransportChanged("Web workflow request is unavailable.")
            if request and request.video_generation and url == Endpoint.GENERATE and not isinstance(data, dict):
                raise VideoTransportChanged("Video generation transport request is unavailable")
            if request and url == Endpoint.GENERATE and isinstance(data, dict):
                kwargs["data"] = inject_web_request_options(data, request)
                headers = dict(kwargs.get("headers") or {})
                if request.thinking_level_id is not None:
                    raw_header = headers.get(MODEL_HEADER_KEY)
                    if raw_header is None:
                        if request.thinking_level_id == 4:
                            raise ValueError("Thinking-level model header is unavailable.")
                    else:
                        try:
                            model_header = orjson.loads(raw_header)
                            # The SDK appends thinking level and session ID to
                            # model headers with either capacity layout.
                            if (not isinstance(model_header, list) or len(model_header) < 17
                                    or type(model_header[-3]) is not int or type(model_header[-2]) is not int
                                    or not isinstance(model_header[-1], str)):
                                raise ValueError
                            if model_header[-2] != request.thinking_level_id:
                                model_header[-2] = request.thinking_level_id
                                headers[MODEL_HEADER_KEY] = orjson.dumps(model_header).decode()
                        except (ValueError, TypeError):
                            raise ValueError("Thinking-level model header has changed.") from None
                headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
                kwargs["headers"] = headers
            return stream(method, url, *args, **kwargs)

        session.stream = stream_with_thinking
        session._mcp_thinking_stream = True
