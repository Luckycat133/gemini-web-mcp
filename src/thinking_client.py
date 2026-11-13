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
from gemini_webapi.constants import Endpoint, Model, MODEL_HEADER_KEY
from gemini_webapi.exceptions import UsageLimitExceededError, TimeoutError as SDKTimeoutError
from gemini_webapi.types import AvailableModel, ModelOutput

from .services.cleanup_scope import observe_upstream_identity

from .constants import (
    is_generation_failure_reply,
    resolve_learning_mode_config,
    resolve_thinking_level_id,
    resolve_thinking_mode_id,
    supported_learning_modes,
)
from .infrastructure.rpc_contracts import (
    VideoTransportChanged, WebWorkflowTransportChanged, apply_notebook_attachments,
    apply_task_generation_transport, apply_video_generation_transport,
)
from .infrastructure.rpc_parsers import CandidateSourceEvidence, parse_candidate_attribution_sources
from .infrastructure.account_identity import current_account_key
from .infrastructure.model_policy_store import ModelPolicyStore
from .services.model_policy import ModelPolicyError, ModelSelection, current_model_policy


@dataclass(frozen=True)
class WebRequestOptions:
    """Gemini Web fields that are not exposed by gemini-webapi yet."""

    thinking_mode_id: int | None = None
    thinking_level_id: int | None = None
    thinking_level_name: str | None = None
    learning_mode_id: int | None = None
    learning_x9b_field: str | None = None
    learning_x9b_value: int | None = None
    video_generation: bool = False
    task_generation: bool = False
    task_conversation_id: str | None = None
    task_action_choice_id: str | None = None
    notebooks: tuple[tuple[str, str], ...] = ()


_web_request: ContextVar[WebRequestOptions | None] = ContextVar(
    "gemini_web_request",
    default=None,
)
_video_generation: ContextVar[bool] = ContextVar("gemini_video_generation", default=False)
_task_generation: ContextVar[bool] = ContextVar("gemini_task_generation", default=False)
_task_conversation: ContextVar[str | None] = ContextVar("gemini_task_conversation", default=None)
_task_action_choice: ContextVar[str | None] = ContextVar("gemini_task_action_choice", default=None)
_notebook_context: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("gemini_notebook_context", default=())
_candidate_evidence: ContextVar[dict[str, tuple[CandidateSourceEvidence, ...]] | None] = ContextVar(
    "gemini_candidate_evidence", default=None
)
_strict_session_recovery: ContextVar[bool] = ContextVar("gemini_strict_session_recovery", default=False)
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
def strict_session_recovery_scope() -> Iterator[None]:
    """Leave incomplete session streams to identity-bound application recovery."""
    token = _strict_session_recovery.set(True)
    try:
        yield
    finally:
        _strict_session_recovery.reset(token)


def _policy_family(model: AvailableModel) -> str:
    labels = (model.model_name, model.display_name, *model.aliases)
    if any(re.search(r"(?<![a-z0-9])flash[\s_-]*lite(?![a-z0-9])", label.lower()) for label in labels):
        return "lite"
    normalized = model.model_name.strip().lower().replace(" ", "-")
    if re.fullmatch(r"(?:gemini-)?(?:\d+(?:\.\d+)?-)?flash", normalized):
        return "flash"
    return "other"


def _task_model_supported(model: AvailableModel) -> bool:
    """Allow only an unambiguous Task family in discovered model metadata."""
    if _policy_family(model) == "lite":
        return False
    families = set()
    for label in (model.model_name, model.display_name, *model.aliases):
        normalized = re.sub(r"[\s_-]+", "-", label.strip().lower())
        match = re.fullmatch(r"(?:gemini-)?(?:\d+(?:\.\d+)?-)?(flash|argon|pro)", normalized)
        if match:
            families.add(match.group(1))
    return families in ({"flash"}, {"argon"})


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
    workflow = options.task_generation or bool(options.notebooks)
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
        apply_video_generation_transport(inner_request)

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

    async def _batch_execute(self, *args: Any, **kwargs: Any) -> Any:
        scoped = _mutation_dispatch_owner.get()
        owner, provider = scoped if scoped else (None, current_account_key)
        if owner:
            if provider() != owner:
                raise PermissionError("Gemini account changed or is unavailable.")
            kwargs["current_retry"] = 0
        try:
            result = await super()._batch_execute(*args, **kwargs)
        except Exception:
            if owner and provider() != owner:
                raise PermissionError("Gemini account changed or is unavailable.") from None
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
            selected = self._resolve_policy_model("gemini-flash")
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
        if family == "lite" and policy.purpose in {"task", "notebook_chat"}:
            raise ModelPolicyError("Flash-Lite is unavailable for Task workflows.")
        if policy.purpose == "task" and not _task_model_supported(selected):
            raise ModelPolicyError("The requested model is unavailable for Task workflows.")
        exhausted = store.flash_exhausted(account) if family in {"flash", "lite"} else False
        reason = None
        if family == "lite" and not exhausted:
            raise ModelPolicyError("Flash-Lite model unavailable without verified Flash quota exhaustion.")
        if family == "flash" and exhausted and policy.purpose == "query" and policy.allow_fallback:
            selected = self._resolve_policy_model("gemini-flash-lite")
            if _policy_family(selected) != "lite":
                raise ModelPolicyError("The requested fallback model is unavailable.")
            family, reason = "lite", "flash_quota_exhausted"
        kwargs["model"] = selected
        options = _web_request.get()
        selectable = family != "lite"
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
        if isinstance(value, str):
            normalized = value.strip().lower()
            known_families = {"flash", "fast", "3.5 flash", "3.8 flash", "gemini-3-flash",
                              "gemini-3-flash-thinking", "thinking", "gemini-flash", "pro", "3.1 pro",
                              "gemini-3-pro", "gemini-pro", "lite", "3.1 flash-lite", "3.5 flash-lite",
                              "flash-lite", "gemini-flash-lite", "gemini-3-flash-lite",
                              "argon", "4 argon", "gemini-argon", "gemini-4-argon"}
            exact = {matches[0].model_name.lower(), matches[0].model_id.lower(),
                     *(alias.lower() for alias in matches[0].aliases)}
            if normalized not in known_families and normalized not in exact:
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
            "flash": "gemini-flash",
            "fast": "gemini-flash",
            "3.5 flash": "gemini-flash",
            "3.8 flash": "gemini-flash",
            "gemini-3-flash": "gemini-flash",
            "gemini-3-flash-thinking": "gemini-flash",
            "thinking": "gemini-flash",
            "gemini-flash": "gemini-flash",
            "pro": "gemini-pro",
            "3.1 pro": "gemini-pro",
            "gemini-3-pro": "gemini-pro",
            "gemini-pro": "gemini-pro",
            "argon": "gemini-argon",
            "4 argon": "gemini-argon",
            "gemini-argon": "gemini-argon",
            "gemini-4-argon": "gemini-argon",
            "lite": "gemini-flash-lite",
            "3.1 flash-lite": "gemini-flash-lite",
            "3.5 flash-lite": "gemini-flash-lite",
            "flash-lite": "gemini-flash-lite",
            "gemini-flash-lite": "gemini-flash-lite",
            "gemini-3-flash-lite": "gemini-flash-lite",
        }
        canonical = families.get(normalized)
        if canonical is None:
            return super()._resolve_model_by_name(name)
        models = list(getattr(self, "_model_registry", {}).values())
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
        raise ValueError(f"Requested model is not available in the discovered registry: {name}")

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
                return _web_request.set(replace(previous, video_generation=True))
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
            video_generation=_video_generation.get(),
            task_generation=_task_generation.get(),
            task_conversation_id=_task_conversation.get(),
            task_action_choice_id=_task_action_choice.get(),
            notebooks=_notebook_context.get(),
        )
        if not any(
            (
                options.thinking_level_id,
                options.learning_mode_id,
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
    def video_generation_scope(self) -> Iterator[None]:
        """Enable the observed video transport for this task only."""
        token = _video_generation.set(True)
        try:
            yield
        finally:
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
            if (request and (request.task_generation or request.notebooks) and url == Endpoint.GENERATE
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
