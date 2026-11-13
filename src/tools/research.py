"""
Deep Research MCP 工具
需要 AI Plus 订阅，研究过程可能需要数分钟。
"""

import asyncio
import html
import json
import logging
import os
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

from ..adapters.mcp_sdk import MCPServer, TextContent

from ..adapters import append_artifact_block, attach_domain_result, domain_failure_text, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    finalize_remote_chat_cleanup,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_model_name
from ..domain import (
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    LongOperationData,
    OperationState,
    new_operation_id,
)
from ..infrastructure.operation_store import OperationStore
from ..infrastructure.account_identity import operation_account_matches
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    artifact_save_failure_result,
)
from ..services.research import (
    RESEARCH_DEFERRED_CLEANUP_SECONDS,
    ResearchRequest,
    await_before_deadline as _await_before_deadline,
    format_research_query as _format_research_query,
    has_native_research_api as _has_native_research_api,
    _failure_from_exception as _research_failure_from_exception,
    operation_state_from_upstream as _operation_state_from_upstream,
    research_domain_result as _research_domain_result,
    research_operation_data as _research_operation_data,
    make_research_cleanup_checkpoints,
    research_chat_id as _research_chat_id,
    research_timed_out_result as _research_timed_out_result,
    research_start_outcome_unknown as _research_start_outcome_unknown,
    research_unknown_result as _research_unknown_result,
    resolve_deep_research_transport_model as _resolve_deep_research_transport_model,
    run_deep_research_start_phase as _run_deep_research_start_phase,
)
from ..services.research_reports import resolve_research_report
from .annotations import MUTATES_LOCAL, MUTATES_REMOTE, READS_PRIVATE_REMOTE

logger = logging.getLogger(__name__)

_CITATION_MARKER_RE = re.compile(r"\[cite:[^\]]+\]")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", flags=re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")
_MD_SECTION_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_MD_TITLE_HEADING_RE = re.compile(r"^#\s+(.+)$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def register_research_tools(mcp: MCPServer):

    async def _run_native_deep_research(
        client: Any,
        query: str,
        model: str,
        model_name: str,
        research_model: Any,
        thinking_level: str,
        model_note: str,
        timeout_seconds: int,
        poll_interval: int,
        wait_for_completion: bool,
        retain_chat: bool,
        delete_after_seconds: int | None,
    ) -> list[TextContent]:
        """Run Deep Research via the client's native plan/start/wait API."""
        operation_id = new_operation_id()
        store = OperationStore()
        store.create(operation_id, "gemini_deep_research", retain_chat=retain_chat,
                     delete_after_seconds=delete_after_seconds, requested_model=model,
                     thinking_level=thinking_level)
        record = store.get(operation_id)
        if record is None or not operation_account_matches(record):
            return domain_text(DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                                   "Operation account could not be verified."),
                               "无法验证研究账号。")

        checkpoints = make_research_cleanup_checkpoints(
            store, record, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
            source="gemini_deep_research", schedule_chat_cleanup=schedule_remote_chat_cleanup,
        )

        start = await _run_deep_research_start_phase(
            client,
            query=query,
            requested_model=model,
            resolved_model=model_name,
            research_model=research_model,
            model_note=model_note,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            operation="gemini_deep_research",
            operation_id=operation_id,
            schedule_chat_cleanup=schedule_remote_chat_cleanup,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            cleanup_source="gemini_deep_research",
            checkpoint=checkpoints.checkpoint,
            late_checkpoint=checkpoints.late_checkpoint,
            early_checkpoint=checkpoints.early_checkpoint,
            claim_confirmation=lambda: operation_account_matches(record)
                and store.claim_research_confirmation(operation_id, expected_phase="plan_created"),
        )
        if not operation_account_matches(record):
            result = _research_unknown_result("gemini_deep_research", operation_id, start.plan, start.chat)
            return domain_text(result, "研究账号已更改；原任务状态尚未确认。", use_result_data=True)
        if start.timed_out is not None:
            timeout_data = start.timed_out.data
            store.update(operation_id, state="timed_out",
                         phase="plan_outcome_unverified" if start.failure_phase == "plan" else "start_returned",
                         upstream_chat_id=timeout_data.upstream_chat_id if timeout_data else None,
                         upstream_operation_id=timeout_data.upstream_operation_id if timeout_data else None,
                         verification_status="completion_not_observed")
            return domain_text(
                start.timed_out,
                _deep_research_timeout_error(timeout_seconds).text,
                use_result_data=True,
            )
        if start.error is not None:
            cid = _research_chat_id(plan=start.plan, chat=start.chat, response=start.start_output)
            if _research_start_outcome_unknown(start):
                store.update(operation_id, state="partial", phase="plan_outcome_unverified", upstream_chat_id=cid,
                             verification_status="start_outcome_unverified")
                result = _research_unknown_result("gemini_deep_research", operation_id, start.plan, start.chat)
                return domain_text(result, "研究请求结果尚未确认；请保留句柄并稍后检查，勿重复启动。",
                                   use_result_data=True)
            store.update(operation_id, state="failed", phase="start_returned", upstream_chat_id=cid,
                         verification_status="start_failed")
            if not start.start_attempted and not retain_chat:
                store.release_cleanup(operation_id)
                if cid:
                    await finalize_remote_chat_cleanup(
                        cid, retain_chat=False, delete_after_seconds=delete_after_seconds,
                        source="gemini_deep_research",
                    )
            failure = _research_failure_from_exception(
                start.error, ResearchRequest(query=query, operation="gemini_deep_research"),
                operation_id=operation_id, plan=start.plan, chat=start.chat, failure_phase=start.failure_phase,
            )
            return domain_text(failure, _deep_research_generic_error(start.error).text, use_result_data=True)
        chat, plan, start_output = start.chat, start.plan, start.start_output

        if not wait_for_completion:
            state = (OperationState.COMPLETED if _has_ready_research_report(start_output)
                     else _operation_state_from_upstream(getattr(start_output, "state", None)))
            if state is OperationState.COMPLETED and not _has_ready_research_report(start_output):
                state = OperationState.PARTIAL
            status = SimpleNamespace(
                state=state.value,
                done=state is OperationState.COMPLETED,
                notes=["caller requested start-only execution"],
            )
            upstream_result = SimpleNamespace(
                plan=plan,
                start_output=start_output,
                final_output=start_output if state is OperationState.COMPLETED else None,
                statuses=[status],
                done=state is OperationState.COMPLETED,
                poll_count=0,
            )
            data = _research_operation_data(
                state,
                operation_id=operation_id,
                plan=plan,
                chat=chat,
                upstream_result=upstream_result,
                latest_upstream_state=state.value,
                poll_count=0,
            )
            store.update(operation_id, state=state.value, phase="start_returned",
                         upstream_chat_id=data.upstream_chat_id, upstream_operation_id=data.upstream_operation_id,
                         verification_status="start_outcome_observed")
            if state is OperationState.COMPLETED:
                return await _save_and_present_research(
                    store, operation_id, upstream_result, query, model, research_model, model_note,
                    waited_for_completion=False,
                )
            if state in {OperationState.FAILED, OperationState.CANCELLED} and not retain_chat:
                store.release_cleanup(operation_id)
                if data.upstream_chat_id:
                    cleanup = await finalize_remote_chat_cleanup(
                        data.upstream_chat_id, retain_chat=False, delete_after_seconds=delete_after_seconds,
                        source="gemini_deep_research",
                    )
                    if cleanup is not None:
                        store.update_cleanup(operation_id, cleanup.state.value)
                        data = replace(data, cleanup=cleanup)
            operation_result = _research_domain_result(data)
            content = _format_deep_research_result(
                query,
                upstream_result,
                model,
                research_model,
                model_note,
                operation_state=state,
                waited_for_completion=False,
            )
            return attach_domain_result(
                [content],
                operation_result,
                use_result_data=True,
            )

        try:
            if _has_ready_research_report(start_output):
                upstream_result = SimpleNamespace(
                    plan=plan, start_output=start_output, final_output=start_output,
                    statuses=[], done=True, poll_count=0,
                )
            elif getattr(plan, "research_id", None):
                upstream_result = await _await_before_deadline(
                    client.wait_for_deep_research(
                        plan,
                        poll_interval=poll_interval,
                        timeout=timeout_seconds,
                    ),
                    timeout=timeout_seconds + poll_interval + 10,
                )
            else:
                upstream_result = await _wait_for_deep_research_by_chat(
                    client=client,
                    plan=plan,
                    chat=chat,
                    start_output=start_output,
                    poll_interval=poll_interval,
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError:
            store.update(operation_id, state="timed_out", phase="report_wait_timed_out",
                         verification_status="completion_not_observed")
            return domain_text(
                _research_timed_out_result(operation_id=operation_id, plan=plan, chat=chat, start_output=start_output),
                _deep_research_timeout_error(timeout_seconds).text,
                use_result_data=True,
            )
        if not operation_account_matches(record):
            result = _research_unknown_result("gemini_deep_research", operation_id, plan, chat)
            return domain_text(result, "研究账号已更改；原任务状态尚未确认。", use_result_data=True)
        upstream_result.start_output = start_output
        report_ready = _has_ready_research_report(getattr(upstream_result, "final_output", None))
        state = (
            OperationState.COMPLETED if report_ready else
            OperationState.PARTIAL if getattr(upstream_result, "done", False) else
            OperationState.TIMED_OUT
        )
        # Rendering must follow the same document evidence as the typed result.
        upstream_result.done = report_ready
        data = _research_operation_data(
            state,
            operation_id=operation_id,
            plan=plan,
            chat=chat,
            upstream_result=upstream_result,
        )
        store.update(operation_id, state=state.value, phase="report_readback",
                     upstream_chat_id=data.upstream_chat_id, upstream_operation_id=data.upstream_operation_id,
                     verification_status="report_observed" if data.report_available else "report_not_observed")
        if data.report_available:
            return await _save_and_present_research(
                store, operation_id, upstream_result, query, model, research_model, model_note,
                waited_for_completion=True,
            )
        operation_result = _research_domain_result(data)
        content = _format_deep_research_result(
            query,
            upstream_result,
            model,
            research_model,
            model_note,
            operation_state=state,
            waited_for_completion=True,
        )
        return attach_domain_result(
            [content],
            operation_result,
            use_result_data=True,
        )

    async def _run_fallback_deep_research(
        client: Any,
        query: str,
        model: str,
        model_name: str,
        thinking_level: str,
        model_note: str,
        timeout_seconds: int,
        retain_chat: bool,
        delete_after_seconds: int | None,
    ) -> list[TextContent]:
        """Fallback when the client lacks the native plan/start/wait API."""
        response = await _await_before_deadline(
            client.generate_content(
                _format_research_query(query, model, model_note),
                model=model_name,
                deep_research=True,
                thinking_level=thinking_level,
                timeout=timeout_seconds,
            ),
            timeout=timeout_seconds,
        )
        cid = _research_chat_id(response=response)
        operation_id = None
        if cid:
            operation_id = new_operation_id()
            store = OperationStore()
            store.create(operation_id, "gemini_deep_research", retain_chat=retain_chat,
                         delete_after_seconds=delete_after_seconds)
            store.update(operation_id, state="accepted", phase="fallback_response_observed",
                         upstream_chat_id=cid, verification_status="start_outcome_unverified")
        scheduled = None
        try:
            scheduled = schedule_remote_chat_cleanup_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=(delete_after_seconds if retain_chat else
                                      max(RESEARCH_DEFERRED_CLEANUP_SECONDS, delete_after_seconds or 0)),
                source="gemini_deep_research:fallback",
            )
        except Exception:
            logger.exception("Fallback research cleanup could not be registered")
        data = _research_operation_data(
            OperationState.ACCEPTED,
            operation_id=operation_id,
            response=response,
        )
        operation_result = _research_domain_result(data)
        cleanup = getattr(scheduled, "cleanup_observation", None)
        if isinstance(cleanup, CleanupObservation):
            data = replace(data, cleanup=cleanup)
            operation_result = replace(operation_result, data=data)
        if cid and (not isinstance(cleanup, CleanupObservation) or cleanup.state in {
                CleanupState.FAILED, CleanupState.INVALID_ID}):
            operation_result = replace(operation_result, warnings=(*operation_result.warnings, DomainWarning(
                code="cleanup_unverified", message="Research chat cleanup was not verified.",
            )))
        text = (
            f"# 📚 Deep Research 计划: {query}\n\n"
            f"- 请求模型: {model}\n"
            f"- 实际研究传输: {model_note}\n\n"
            f"{response.text}\n\n"
            "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API，"
            "这里只能返回已收到的回复，研究运行状态尚未验证。"
        )
        if operation_id:
            text += f"\n\nOperation handle: {operation_id}"
        return domain_text(operation_result, text, use_result_data=True)

    def _deep_research_timeout_error(timeout_seconds: int) -> TextContent:
        return TextContent(
            type="text",
            text=f"❌ Deep Research 超时（{timeout_seconds}秒）。\n\n"
            "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
            "2. 网络和认证状态是否正常？\n"
            "3. 研究主题是否适合在较短超时时间内完成？"
        )

    def _deep_research_generic_error(e: BaseException) -> TextContent:
        return TextContent(
            type="text",
            text=f"❌ Deep Research 失败（{type(e).__name__}）。\n\n"
            "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
            "2. 该功能在您所在的区域是否可用？"
        )

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_deep_research(
        query: str,
        model: str = "flash",
        thinking_level: str = "standard",
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 10,
        wait_for_completion: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        background: bool = True,
        operation_id: str | None = None,
    ) -> list[TextContent]:
        """
        启动 Deep Research 深度研究。

        需要 AI Plus 订阅！

        参数:
        - query: 研究主题或问题
        - model: 模型选择 (thinking/pro)
        - timeout_seconds: 超时时间（默认10分钟）
        - poll_interval_seconds: 研究状态轮询间隔（默认10秒）
        - background: 默认 True，提交后台任务并用返回的 operation_id 查询状态与结果
        - wait_for_completion: True 时显式等待最终报告；该选择优先于后台默认值
        - operation_id: 后台模式可指定句柄；同步模式不接受该参数

        工作流程:
        1. 创建研究计划
        2. 多轮搜索和分析
        3. 生成完整报告（含引用来源）
        """
        if not query.strip():
            return domain_text(DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "query must not be blank."),
                               "研究主题不能为空。")
        if (not background or wait_for_completion) and operation_id is not None:
            return domain_text(DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                                                     "operation_id requires background execution."),
                               "同步研究不接受 operation_id。")
        if background and not wait_for_completion:
            from ..services.background import submit_research
            result = await submit_research(ResearchRequest(
                query=query, model=model, thinking_level=thinking_level, timeout_seconds=timeout_seconds,
                retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                operation="gemini_deep_research", operation_id=operation_id,
            ))
            return domain_text(result, "研究任务已提交。" if result.ok else "研究任务提交失败。",
                               use_result_data=True)
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        research_model, model_note = _resolve_deep_research_transport_model(model, client)

        try:
            logger.info("正在启动 Deep Research")
            poll_interval = max(3, poll_interval_seconds)

            if _has_native_research_api(client):
                return await _run_native_deep_research(
                    client, query, model, model_name, research_model,
                    thinking_level, model_note, timeout_seconds, poll_interval,
                    wait_for_completion, retain_chat, delete_after_seconds,
                )
            return await _run_fallback_deep_research(
                client, query, model, model_name, thinking_level,
                model_note, timeout_seconds, retain_chat, delete_after_seconds,
            )

        except asyncio.TimeoutError:
            logger.error("Deep Research 失败: request timed out")
            data = LongOperationData(
                operation="gemini_deep_research",
                state=OperationState.TIMED_OUT,
            )
            return domain_text(
                _research_domain_result(data),
                _deep_research_timeout_error(timeout_seconds).text,
                use_result_data=True,
            )
        except Exception as e:
            logger.error("Deep Research 失败: error_type=%s", type(e).__name__)
            data = LongOperationData(
                operation="gemini_deep_research",
                state=OperationState.FAILED,
            )
            return domain_text(
                _research_domain_result(data),
                _deep_research_generic_error(e).text,
                use_result_data=True,
            )

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_research_report_actions(
        chat_id: str | None = None,
        response_format: Literal["markdown", "json"] = "markdown",
        operation_id: str | None = None,
    ) -> list[TextContent]:
        """
        列出已完成 Deep Research 报告可用于“从报告创建”的动作。

        目前 Gemini Web 的 READ_CHAT 原始响应可稳定读取报告、来源和沉浸式报告 ID，
        但没有观测到网页下拉菜单的稳定私有 mutation RPC。因此这里返回 MCP 侧
        支持的等价创建动作，并标明 native_web_menu_observed=false。
        """
        source, unavailable = await resolve_research_report(
            chat_id=chat_id, operation_id=operation_id, client_provider=get_gemini_client,
            client_initializer=initialize_client, fetch_report=_fetch_deep_research_immersive_report,
        )
        if source is None:
            assert unavailable is not None
            return domain_text(unavailable,
                domain_failure_text(unavailable) if not unavailable.ok else
                f"❌ 未能在聊天 {chat_id} 中读取 Deep Research 沉浸式报告。" if chat_id else
                "原 operation_id 尚无可验证的本地报告；请先读取同一句柄的 gemini_get_operation_result。",
                use_result_data=True)
        if not source.account_matches():
            return domain_text(DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Report account changed."), "报告账号已更改。")
        payload = _research_report_actions_payload(source.output, source.chat_id)
        if source.operation_id:
            payload.update(operation_id=source.operation_id, report_source="verified_local_artifact")
        result = DomainResult.success(payload, verification_status=(
            "research_artifact_verified" if source.operation_id else "research_document_readback"))
        if response_format == "json":
            return attach_domain_result([TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))],
                                        result, use_result_data=True)
        return domain_text(result, _format_research_report_actions(payload), use_result_data=True)

    @mcp.tool(annotations=MUTATES_LOCAL)
    async def gemini_create_from_research_report(
        chat_id: str | None = None,
        artifact_type: Literal[
            "webpage",
            "infographic",
            "quiz",
            "flashcards",
            "audio_overview",
            "custom_app",
        ] = "webpage",
        output_dir: str = "generated_media/research_artifacts",
        response_format: Literal["markdown", "json"] = "markdown",
        operation_id: str | None = None,
    ) -> list[TextContent]:
        """
        从已完成 Deep Research 报告创建后续产物。

        这是 MCP 侧对 Gemini Web 报告页“创建...”菜单的等价实现：读取报告正文
        和来源，生成本地 artifact。菜单项来自 2026-06-21 网页实测；它不会调用
        未稳定观测到的 Gemini 私有菜单 mutation。
        """
        source, unavailable = await resolve_research_report(
            chat_id=chat_id, operation_id=operation_id, client_provider=get_gemini_client,
            client_initializer=initialize_client, fetch_report=_fetch_deep_research_immersive_report,
        )
        if source is None:
            assert unavailable is not None
            return domain_text(
                unavailable,
                domain_failure_text(unavailable) if not unavailable.ok else
                f"❌ 未能在聊天 {chat_id} 中读取 Deep Research 沉浸式报告。" if chat_id else
                "原 operation_id 尚无可验证的本地报告；请先读取同一句柄的 gemini_get_operation_result。",
                use_result_data=True,
            )
        if not source.account_matches():
            return domain_text(DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Report account changed."), "报告账号已更改。")
        chat_id = source.chat_id
        try:
            artifact = _create_research_report_artifact(
                report_output=source.output,
                artifact_type=artifact_type,
                chat_id=chat_id,
                output_dir=output_dir,
            )
            if source.operation_id:
                artifact.update(operation_id=source.operation_id, report_source="verified_local_artifact")
        except OSError as error:
            data = ArtifactResultData(
                state=ArtifactState.FAILED,
                request_model=artifact_type,
                effective_backend="MCP local renderer",
                observed_backend="filesystem",
                source_chat_id=chat_id,
                media_type="research_report",
            )
            result = artifact_save_failure_result(
                error,
                data,
                logger=logger,
                operation="gemini_create_from_research_report",
            )
            return domain_text(
                result,
                "❌ Research report artifact creation failed. Check the output directory and retry.",
                use_result_data=True,
            )
        except Exception as error:
            data = ArtifactResultData(
                state=ArtifactState.FAILED,
                request_model=artifact_type,
                effective_backend="MCP local renderer",
                source_chat_id=chat_id,
                media_type="research_report",
            )
            result = artifact_exception_result(
                error,
                data,
                logger=logger,
                operation="gemini_create_from_research_report",
            )
            return domain_text(
                result,
                "❌ Research report artifact creation failed. Inspect the diagnostic ID and retry.",
                use_result_data=True,
            )

        if not source.account_matches():
            return domain_text(DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Report account changed."), "报告账号已更改。")
        typed_artifact = artifact_from_local_path(
            _research_artifact_kind(artifact["path"]),
            artifact["path"],
            title=artifact["title"],
            source_chat_id=chat_id,
            request_model=artifact_type,
            effective_backend="MCP local renderer",
            observed_backend="filesystem",
        )
        data = ArtifactResultData(
            state=typed_artifact.state,
            artifacts=(typed_artifact,),
            request_model=artifact_type,
            effective_backend="MCP local renderer",
            observed_backend="filesystem",
            source_chat_id=chat_id,
            media_type="research_report",
        )
        save_failures = ("post_write_verification",) if typed_artifact.state == ArtifactState.FAILED else ()
        result = artifact_result(data, save_failures=save_failures)
        if response_format == "json":
            return attach_domain_result(
                [TextContent(type="text", text=json.dumps(artifact, ensure_ascii=False, indent=2))],
                result,
                use_result_data=True,
            )
        text = _format_research_report_artifact(artifact)
        if typed_artifact.state == ArtifactState.FAILED:
            text = f"❌ Research report artifact could not be verified.\n\n{text}"
        content = append_artifact_block([TextContent(type="text", text=text)], data.artifacts)
        return attach_domain_result(content, result, use_result_data=True)


async def _save_and_present_research(
    store: OperationStore, operation_id: str, upstream: Any, query: str,
    model: str, research_model: Any, model_note: str, *, waited_for_completion: bool,
) -> list[TextContent]:
    from ..services.operations import OperationService

    record = store.get(operation_id)
    if record is None:
        return domain_text(DomainResult.failure(DomainErrorCode.INTERNAL_ERROR,
            "Research metadata could not be read back."), "研究元数据尚未验证。")
    result = await OperationService(store, get_gemini_client, initialize_client,
        finalize_remote_chat_cleanup).save_report(record, upstream.final_output.deep_research_document)
    data = result.data
    if data is None:
        return domain_text(result, "无法验证研究报告或其账号。", use_result_data=True)
    preview = SimpleNamespace(ready=True, content=data.report or "", markdown=data.report or "")
    rendered = SimpleNamespace(plan=upstream.plan, statuses=getattr(upstream, "statuses", ()),
        start_output=getattr(upstream, "start_output", None), done=data.report_available,
        final_output=SimpleNamespace(deep_research_document=preview) if data.report_available else None)
    content = _format_deep_research_result(query, rendered, model, research_model, model_note,
        operation_state=data.state, waited_for_completion=waited_for_completion)
    return append_artifact_block(attach_domain_result([content], result, use_result_data=True), data.artifacts)


async def _wait_for_deep_research_by_chat(
    client,
    plan,
    chat,
    start_output,
    poll_interval: int,
    timeout: int,
):
    """Fallback for Gemini Web responses that omit research_id in the plan."""
    cid = getattr(chat, "cid", "") or getattr(plan, "cid", "")
    started = asyncio.get_running_loop().time()
    checks = 0
    latest_output = None

    while (asyncio.get_running_loop().time() - started) < timeout:
        if cid and hasattr(client, "fetch_latest_chat_response"):
            latest_output = await client.fetch_latest_chat_response(cid, limit=100, match=_has_ready_research_report)
            if _has_ready_research_report(latest_output):
                return SimpleNamespace(
                    plan=plan,
                    start_output=start_output,
                    final_output=latest_output,
                    statuses=[
                        SimpleNamespace(
                            state="completed",
                            done=True,
                            notes=[f"chat history contained a ready research document after {checks + 1} checks"],
                        )
                    ],
                    done=True,
                    poll_count=checks + 1,
                )
        checks += 1
        elapsed = asyncio.get_running_loop().time() - started
        remaining = max(0.0, timeout - elapsed)
        await asyncio.sleep(min(poll_interval, remaining))

    return SimpleNamespace(
        plan=plan,
        start_output=start_output,
        final_output=None,
        statuses=[
            SimpleNamespace(
                state="unknown",
                done=False,
                notes=[
                    (
                        "research_id was not present in Gemini's plan; "
                        f"checked chat history {checks} times"
                    )
                ],
            )
        ],
        done=False,
        poll_count=checks,
    )


def _has_ready_research_report(response: Any) -> bool:
    document = getattr(response, "deep_research_document", None)
    content = getattr(document, "content", "")
    return bool(getattr(document, "ready", False)) and isinstance(content, str) and bool(content.strip())


def _is_research_start_message(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "i'm on it",
            "i’ll let you know",
            "i'll let you know",
            "research is finished",
            "while i'm researching",
            "leave this chat",
            "i've finished the research",
            "i have finished the research",
            "我已经完成了研究",
            "研究完成后",
            "我这就开始",
        )
    )


def _is_research_completion_message(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "i've finished the research",
            "i have finished the research",
            "我已经完成了研究",
        )
    )


async def _request_completed_research_report(chat):
    if not hasattr(chat, "send_message"):
        return None
    prompt = (
        "请把刚才 Deep Research 完成的完整报告正文粘贴出来，并在末尾列出所有来源链接。"
        "不要只说已经完成。"
    )
    try:
        return await _await_before_deadline(chat.send_message(prompt), timeout=180)
    except Exception as e:
        logger.warning("Failed to retrieve completed Deep Research report by follow-up: error_type=%s", type(e).__name__)
        return None


async def _fetch_deep_research_immersive_report(client, cid: str):
    """Read only a ready SDK research document from the specified chat."""
    fetch = getattr(client, "fetch_latest_chat_response", None)
    if not cid or not callable(fetch):
        return None
    try:
        response = await fetch(cid, limit=100, match=_has_ready_research_report)
    except Exception as error:
        logger.warning("Failed to read Deep Research report: error_type=%s", type(error).__name__)
        return None
    if not _has_ready_research_report(response):
        return None
    document = response.deep_research_document
    report = document.content.strip()
    sources = []
    source_numbers = []
    for source in getattr(document, "sources", None) or ():
        url = getattr(source, "url", None)
        title = getattr(source, "title", None)
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                continue
        except ValueError:
            continue
        sources.append({"url": url, "title": title if isinstance(title, str) and title.strip() else url})
        source_id = getattr(source, "id", None)
        source_numbers.append(source_id if isinstance(source_id, int) and source_id > 0 else None)
    markdown = getattr(document, "markdown", None)
    text = markdown.strip() if isinstance(markdown, str) and markdown.strip() else report
    if text == report and sources:
        source_lines = [
            f"{number}. [{source['title']}]({source['url']})"
            for number, source in zip(source_numbers, sources, strict=True) if number is not None
        ]
        if source_lines:
            text += "\n\n## 来源链接\n" + "\n".join(source_lines)
    return SimpleNamespace(
        text=text,
        report=report,
        sources=sources,
        title=getattr(document, "title", None) or _title_from_markdown(report),
        immersive_id=getattr(document, "id", None) or "",
        native_web_menu_observed=False,
    )


def _walk_nested_json(obj: Any, path: tuple[Any, ...] = ()):
    yield path, obj
    if isinstance(obj, str):
        value = obj.strip()
        if value and value[0] in "[{":
            try:
                parsed = json.loads(value)
            except Exception as e:
                logger.debug("Skipping non-JSON string at %s: %s", path, e)
                return
            yield from _walk_nested_json(parsed, path + ("json",))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from _walk_nested_json(item, path + (idx,))
    elif isinstance(obj, dict):
        for key, item in obj.items():
            yield from _walk_nested_json(item, path + (key,))


def _extract_sources_from_node(node: Any) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for _path, item in _walk_nested_json(node):
        if not isinstance(item, list) or len(item) < 3:
            continue
        # 条目形状为 [favicon, url, title]；favicon 仅用于结构校验，不参与输出。
        favicon, url, title = item[0], item[1], item[2]
        if not (isinstance(favicon, str) and isinstance(url, str) and isinstance(title, str)):
            continue
        if not title.strip():
            continue
        try:
            parsed_url = urlsplit(url)
            hostname = parsed_url.hostname
            port = parsed_url.port
        except ValueError:
            continue
        scheme = parsed_url.scheme.lower()
        if scheme not in {"http", "https"} or not hostname:
            continue
        normalized_hostname = hostname.rstrip(".").lower()
        # 来源必须是普通域名：IPv6 字面量、含空格等畸形主机名一律丢弃，
        # 防止绕过下方 google/gstatic 过滤。
        if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", normalized_hostname):
            continue
        is_googleusercontent = normalized_hostname == "googleusercontent.com" or normalized_hostname.endswith(
            ".googleusercontent.com"
        )
        is_gstatic = normalized_hostname == "gstatic.com" or normalized_hostname.endswith(".gstatic.com")
        if is_googleusercontent:
            continue
        if is_gstatic:
            # 先 unquote 再匹配，防止 %66avicon 这类编码绕过；只认已知图标路径，
            # 避免 /favicon-notes 之类的正常 gstatic 页面被误杀。
            favicon_path = unquote(parsed_url.path).lower()
            if favicon_path.startswith(("/favicon.ico", "/faviconv")):
                continue
        # 去重键做归一化：scheme/主机小写、默认端口、结尾斜杠、fragment 差异视为同一来源。
        netloc = parsed_url.netloc.lower()
        default_port = {"http": 80, "https": 443}.get(scheme)
        if port is not None and port == default_port:
            netloc = netloc.rsplit(":", 1)[0]
        dedupe_key = urlunsplit((scheme, netloc, parsed_url.path.rstrip("/") or "/", parsed_url.query, ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        sources.append({"url": url, "title": title})
    return sources


RESEARCH_REPORT_ACTIONS: list[dict[str, str]] = [
    {
        "type": "webpage",
        "label": "网页",
        "description": "Create a local HTML webpage from the ready research report.",
    },
    {
        "type": "infographic",
        "label": "信息图",
        "description": "Create a local HTML infographic from the ready research report.",
    },
    {
        "type": "quiz",
        "label": "测验",
        "description": "Create a local Markdown quiz from the ready research report.",
    },
    {
        "type": "flashcards",
        "label": "抽认卡",
        "description": "Create local Markdown flashcards from the ready research report.",
    },
    {
        "type": "audio_overview",
        "label": "音频概览",
        "description": "Create a local Markdown narration script for an audio overview.",
    },
    {
        "type": "custom_app",
        "label": "描述你自己的应用",
        "description": "Create a local Markdown application specification from the report.",
    },
]


def _research_report_actions_payload(report_output, chat_id: str = "") -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "title": getattr(report_output, "title", "") or _title_from_markdown(getattr(report_output, "report", "")),
        "immersive_id": getattr(report_output, "immersive_id", ""),
        "native_web_menu_observed": bool(getattr(report_output, "native_web_menu_observed", False)),
        "native_web_menu_note": (
            "Observed in Gemini Web on 2026-06-21: 创建 -> 网页, 信息图, 测验, 抽认卡, 音频概览, "
            "plus a custom app description field. READ_CHAT still does not expose a stable private "
            "mutation RPC for invoking those menu items directly."
        ),
        "actions": RESEARCH_REPORT_ACTIONS,
    }


def _format_research_report_actions(payload: dict[str, Any]) -> str:
    lines = [
        "## Deep Research 报告创建动作",
        f"- 标题: {payload.get('title') or '(unknown)'}",
        f"- 沉浸式报告 ID: {payload.get('immersive_id') or '(unknown)'}",
        f"- 原生网页创建菜单项已观测: {'是' if payload.get('native_web_menu_observed') else '否'}",
        "",
        "### MCP 可用动作",
    ]
    for action in payload["actions"]:
        lines.append(f"- `{action['type']}`: {action['label']} - {action['description']}")
    lines.extend(["", payload["native_web_menu_note"]])
    return "\n".join(lines)


def _research_artifact_kind(path: str) -> ArtifactKind:
    return ArtifactKind.WEBPAGE if os.path.splitext(path)[1].lower() == ".html" else ArtifactKind.REPORT


def _create_research_report_artifact(
    report_output,
    artifact_type: str,
    chat_id: str,
    output_dir: str,
) -> dict[str, Any]:
    report = getattr(report_output, "report", "") or getattr(report_output, "text", "")
    sources = getattr(report_output, "sources", []) or []
    title = getattr(report_output, "title", "") or _title_from_markdown(report) or "Deep Research Report"
    content, extension = _render_research_artifact(artifact_type, title, report, sources)
    safe_title = _safe_filename(title)[:80] or "deep-research"
    safe_chat_id = _safe_filename(chat_id) or "chat"
    filename = f"{safe_chat_id}-{safe_title}-{artifact_type}.{extension}"
    path = os.path.abspath(os.path.join(output_dir, filename))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {
        "chat_id": chat_id,
        "artifact_type": artifact_type,
        "title": title,
        "path": path,
        "bytes": len(content.encode("utf-8")),
        "source_count": len(sources),
        "native_web_menu_observed": bool(getattr(report_output, "native_web_menu_observed", False)),
        "native_web_menu_note": (
            "Created by the MCP-side report action matching the observed Gemini Web create menu. "
            "No stable native Gemini private mutation RPC was present in the captured READ_CHAT payload."
        ),
    }


def _render_research_artifact(
    artifact_type: str,
    title: str,
    report: str,
    sources: list[dict[str, str]],
) -> tuple[str, str]:
    sections = _markdown_sections(report)
    if artifact_type == "webpage":
        return _render_report_webpage(title, report, sources, sections), "html"
    if artifact_type == "infographic":
        return _render_infographic(title, report, sources, sections), "html"
    if artifact_type == "quiz":
        return _render_quiz(title, report, sources, sections), "md"
    if artifact_type == "flashcards":
        return _render_flashcards(title, report, sources, sections), "md"
    if artifact_type == "audio_overview":
        return _render_audio_overview(title, report, sources, sections), "md"
    if artifact_type == "custom_app":
        return _render_custom_app_spec(title, report, sources, sections), "md"
    raise ValueError(f"Unsupported artifact_type: {artifact_type}")


def _format_research_report_artifact(artifact: dict[str, Any]) -> str:
    return (
        "## Research Report Artifact Created\n"
        f"- 类型: `{artifact['artifact_type']}`\n"
        f"- 标题: {artifact['title']}\n"
        f"- 文件: {artifact['path']}\n"
        f"- 来源数: {artifact['source_count']}\n"
        f"- 原生网页创建菜单项已观测: {'是' if artifact['native_web_menu_observed'] else '否'}\n\n"
        f"{artifact['native_web_menu_note']}"
    )


def _render_report_webpage(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    summary = _plain_excerpt(report, 520)
    report_points = sections[:6] or [{"heading": "Research takeaways", "body": report}]
    source_items = "\n".join(
        (
            f'<li><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(title)}</a></li>"
        )
        for url, title in _iter_source_links(sources, limit=12)
    )
    points_html = "\n".join(
        (
            "<section>"
            f"<h2>{html.escape(item['heading'])}</h2>"
            f"<p>{html.escape(_plain_excerpt(item['body'], 420))}</p>"
            "</section>"
        )
        for item in report_points
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f4ed; color: #18201c; }}
    header {{ min-height: 62vh; display: grid; align-content: end; padding: 8vw; background: linear-gradient(135deg, #18201c, #2f5d62 48%, #b85c38); color: white; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 48px 24px 72px; }}
    h1 {{ max-width: 900px; margin: 0; font-size: clamp(2.3rem, 7vw, 5.6rem); line-height: .96; }}
    h2 {{ margin: 0 0 12px; font-size: 1.35rem; }}
    p {{ line-height: 1.72; }}
    .dek {{ max-width: 760px; margin-top: 22px; font-size: 1.12rem; color: #f3efe6; }}
    .player {{ display: flex; align-items: center; gap: 14px; margin-top: 36px; }}
    .play {{ width: 54px; height: 54px; border-radius: 50%; border: 0; background: white; color: #18201c; font-size: 1.25rem; }}
    section {{ border-top: 1px solid #d6cfc0; padding: 28px 0; }}
    ol {{ padding-left: 22px; line-height: 1.7; }}
    a {{ color: #245a68; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <p class="dek">{html.escape(summary)}</p>
    <div class="player"><button class="play" aria-label="Open">↗</button><span>Webpage generated from Gemini Deep Research</span></div>
  </header>
  <main>
    {points_html}
    <section>
      <h2>Sources</h2>
      <ol>
        {source_items}
      </ol>
    </section>
  </main>
</body>
</html>
"""


def _render_infographic(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    cards = sections[:6] or [{"heading": title, "body": report}]
    card_html = "\n".join(
        (
            "<article>"
            f"<strong>{idx:02d}</strong>"
            f"<h2>{html.escape(item['heading'])}</h2>"
            f"<p>{html.escape(_plain_excerpt(item['body'], 230))}</p>"
            "</article>"
        )
        for idx, item in enumerate(cards, 1)
    )
    source_count = len(sources)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Infographic</title>
  <style>
    body {{ margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #fbfaf7; color: #17211c; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 56px 24px; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4.4rem); line-height: 1; margin: 0 0 18px; }}
    .meta {{ color: #5d675f; margin-bottom: 34px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }}
    article {{ border: 1px solid #d9d2c7; border-radius: 8px; padding: 22px; background: white; min-height: 220px; }}
    strong {{ color: #b44e32; font-size: .9rem; }}
    h2 {{ font-size: 1.15rem; margin: 12px 0; }}
    p {{ line-height: 1.6; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p class="meta">Infographic generated from Gemini Deep Research · {source_count} sources preserved</p>
    <section class="grid">{card_html}</section>
  </main>
</body>
</html>
"""


def _render_quiz(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    questions = sections[:8] or [{"heading": title, "body": report}]
    lines = [
        f"# 测验：{title}",
        "",
        "根据 Deep Research 报告生成的本地测验草稿。",
    ]
    for idx, item in enumerate(questions, 1):
        lines.extend(
            [
                "",
                f"## Q{idx}. {item['heading']}",
                "问题：以下哪一项最能概括这一节的重点？",
                f"- A. {_plain_excerpt(item['body'], 120)}",
                "- B. 该主题与报告主线无关",
                "- C. 报告没有给出任何证据",
                "",
                "答案：A",
            ]
        )
    lines.extend(["", "## 来源"])
    for idx, (url, title) in enumerate(_iter_source_links(sources, limit=8), 1):
        lines.append(f"{idx}. [{title}]({url})")
    return "\n".join(lines) + "\n"


def _render_flashcards(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    cards = sections[:12] or [{"heading": title, "body": report}]
    lines = [f"# 抽认卡：{title}", ""]
    for idx, item in enumerate(cards, 1):
        lines.extend(
            [
                f"## Card {idx}",
                f"Front: {item['heading']}",
                f"Back: {_plain_excerpt(item['body'], 260)}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_audio_overview(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    beats = sections[:5] or [{"heading": title, "body": report}]
    lines = [
        f"# 音频概览脚本：{title}",
        "",
        "这是 Gemini Web 原生“音频概览”菜单项的本地脚本等价物，不会生成远端音频文件。",
        "",
        "## Opening",
        f"Host A: 今天我们用几分钟拆解这份研究：{title}。",
        f"Host B: 核心背景是：{_plain_excerpt(report, 180)}",
    ]
    for idx, item in enumerate(beats, 1):
        lines.extend(
            [
                "",
                f"## Segment {idx}: {item['heading']}",
                f"Host A: {_plain_excerpt(item['body'], 260)}",
                f"Host B: 听众需要记住的是：{_plain_excerpt(item['body'], 180)}",
            ]
        )
    lines.extend(["", "## Source Trail"])
    for idx, (url, title) in enumerate(_iter_source_links(sources, limit=8), 1):
        lines.append(f"{idx}. [{title}]({url})")
    return "\n".join(lines) + "\n"


def _render_custom_app_spec(
    title: str,
    report: str,
    sources: list[dict[str, str]],
    sections: list[dict[str, str]],
) -> str:
    first_takeaway = sections[0]["heading"] if sections else title
    return (
        f"# 自定义应用规格：{title}\n\n"
        "对应 Gemini Web 创建菜单里的“描述你自己的应用”输入框。\n\n"
        "## Suggested App\n"
        f"Build an interactive research companion for `{title}`.\n\n"
        "## Core Data\n"
        f"- Summary: {_plain_excerpt(report, 360)}\n"
        f"- First module: {first_takeaway}\n"
        f"- Source count: {len(sources)}\n\n"
        "## Expected UI\n"
        "- Report summary\n"
        "- Section navigator\n"
        "- Source-backed cards\n"
        "- Quiz or reflection prompts\n"
    )


def _markdown_sections(report: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in report.splitlines():
        match = _MD_SECTION_HEADING_RE.match(line.strip())
        if match:
            if current_heading and current_body:
                sections.append({"heading": current_heading, "body": "\n".join(current_body).strip()})
            current_heading = match.group(2).strip()
            current_body = []
        elif current_heading:
            current_body.append(line)
    if current_heading and current_body:
        sections.append({"heading": current_heading, "body": "\n".join(current_body).strip()})
    return [item for item in sections if _plain_excerpt(item["body"], 40)]


def _iter_source_links(sources: list[dict[str, str]], limit: int) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for source in sources:
        url = source.get("url") if isinstance(source, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        raw_title = source.get("title") if isinstance(source, dict) else None
        title = raw_title if isinstance(raw_title, str) and raw_title.strip() else url
        links.append((url, title))
        if len(links) >= limit:
            break
    return links


def _plain_excerpt(markdown: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    text = _CITATION_MARKER_RE.sub("", markdown)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_HEADING_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        match = _MD_TITLE_HEADING_RE.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _safe_filename(value: str) -> str:
    value = _SAFE_FILENAME_RE.sub("-", value.strip())
    return value.strip("-._")


def _format_deep_research_result(
    query: str,
    result,
    requested_model: str = "",
    research_model: Any = "",
    model_note: str = "",
    *,
    operation_state: OperationState | None = None,
    waited_for_completion: bool = True,
) -> TextContent:
    plan = result.plan
    statuses = getattr(result, "statuses", []) or []
    final_output = getattr(result, "final_output", None)
    start_output = getattr(result, "start_output", None)
    done = bool(getattr(result, "done", False))
    if operation_state is None:
        operation_state = (
            OperationState.COMPLETED if done else OperationState.TIMED_OUT
        )
    heading = "报告" if done else "状态"

    lines = [
        f"# 📚 Deep Research {heading}: {query}",
        "",
        "## 状态",
        f"- 完成: {'是' if done else '否'}",
        f"- MCP 状态: {operation_state.value}",
    ]
    if requested_model:
        lines.append(f"- 请求模型: {requested_model}")
    if model_note:
        lines.append(f"- 实际研究传输: {model_note}")
    if getattr(plan, "research_id", None):
        lines.append(f"- Research ID: {plan.research_id}")
    if getattr(plan, "title", None):
        lines.append(f"- 标题: {plan.title}")
    if statuses:
        last_status = statuses[-1]
        lines.append(f"- 最新状态: {getattr(last_status, 'state', 'unknown')}")
        notes = getattr(last_status, "notes", []) or []
        if notes:
            lines.append(f"- 最新进度: {notes[-1]}")

    document = getattr(final_output, "deep_research_document", None)
    content = getattr(document, "content", "")
    report_text = ""
    if getattr(document, "ready", False) and isinstance(content, str) and content.strip():
        report_text = getattr(document, "markdown", None) or content
    if report_text.strip():
        lines.extend(["", "## 报告", report_text.strip()])
    else:
        start_text = getattr(start_output, "text", "") if start_output else ""
        plan_text = getattr(plan, "response_text", "") or start_text
        if not done:
            if not waited_for_completion:
                if operation_state is OperationState.ACCEPTED:
                    state_text = "研究确认已发送，运行状态尚未验证"
                else:
                    state_text = "研究已排队" if operation_state is OperationState.QUEUED else "研究已启动"
                prefix = f"{state_text}；本次调用未等待最终报告。"
            elif operation_state is OperationState.TIMED_OUT:
                prefix = "等待已超时，尚未观察到可读取的最终报告。"
            else:
                prefix = "尚未观察到可读取的最终报告。"
            plan_text = prefix + "\n\n" + plan_text
        lines.extend(
            [
                "",
                "## 当前结果",
                plan_text.strip(),
            ]
        )

    if statuses:
        lines.extend(["", "## 轮询记录"])
        for status in statuses[-8:]:
            state = getattr(status, "state", "unknown")
            status_label = "done" if getattr(status, "done", False) else "running"
            note = ""
            notes = getattr(status, "notes", []) or []
            if notes:
                note = f" - {notes[-1]}"
            lines.append(f"- {state} ({status_label}){note}")

    return TextContent(type="text", text="\n".join(lines))
