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
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

from ..adapters.mcp_sdk import MCPServer, TextContent

from ..adapters import append_artifact_block, attach_domain_result, domain_text
from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup,
)
from ..constants import resolve_model_name
from ..domain import (
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    DomainErrorCode,
    DomainResult,
    LongOperationData,
    OperationState,
)
from ..services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_result,
    artifact_save_failure_result,
)
from ..services.research import (
    ResearchRequest,
    ResearchService,
    ResearchServiceDependencies,
    await_before_deadline as _await_before_deadline,
    has_native_research_api as _has_native_research_api,
    is_research_completion_message as _completion_message,
    is_research_start_message as _is_research_start_message,
    research_domain_result as _research_domain_result,
    research_report_body as _research_report_body,
    resolve_deep_research_transport_model as _resolve_deep_research_transport_model,
    wait_for_deep_research_by_chat as _wait_by_chat,
)
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


def _build_research_service() -> ResearchService:
    return ResearchService(ResearchServiceDependencies(
        client_provider=lambda: get_gemini_client(), client_initializer=lambda: initialize_client(),
        cleanup_due_remote_chats=lambda client: cleanup_due_remote_chats(client),
        schedule_chat_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup(*args, **kwargs),
        resolve_model=resolve_model_name,
    ))


def _operation_text(result: DomainResult[LongOperationData]) -> str:
    if not result.ok:
        return f"❌ Deep Research 失败: {result.error.message if result.error else 'Unknown error'}"
    if result.data is None:
        return "Deep Research has no recorded result."
    return f"Deep Research is {result.data.state.value}. Operation handle: {result.data.operation_id}"


def register_research_tools(mcp: MCPServer):

    async def _run_native_deep_research(
        client: Any, query: str, model: str, model_name: str, research_model: Any,
        thinking_level: str, model_note: str, timeout_seconds: int, poll_interval: int,
        wait_for_completion: bool, retain_chat: bool, delete_after_seconds: int | None,
        idempotency_key: str | None = None,
    ) -> list[TextContent]:
        service = _build_research_service()
        request = ResearchRequest(query=query, model=model, thinking_level=thinking_level,
                                  timeout_seconds=timeout_seconds, retain_chat=retain_chat,
                                  delete_after_seconds=delete_after_seconds,
                                  cleanup_source="gemini_deep_research", operation="gemini_deep_research",
                                  idempotency_key=idempotency_key)
        reserved, context = service.reserve(request)
        if context is None:
            return domain_text(reserved, _operation_text(reserved), use_result_data=True)
        execution = await service.execute_native(
            client, request, context, wait_for_completion=wait_for_completion,
            poll_interval=poll_interval, fetch_report=_fetch_deep_research_immersive_report,
            request_report=_request_completed_research_report,
        )
        result = execution.result
        if execution.upstream_result is None:
            text = (_deep_research_timeout_error(timeout_seconds).text
                    if result.meta.operation_state == OperationState.TIMED_OUT
                    else _operation_text(result))
            return domain_text(result, text, use_result_data=True)
        content = _format_deep_research_result(
            query, execution.upstream_result, model, research_model, model_note,
            operation_state=result.meta.operation_state, waited_for_completion=wait_for_completion,
        )
        rendered = append_artifact_block([content], result.data.artifacts if result.data else ())
        return attach_domain_result(rendered, result, use_result_data=True)

    async def _run_fallback_deep_research(
        client: Any, query: str, model: str, model_name: str, thinking_level: str,
        model_note: str, timeout_seconds: int, retain_chat: bool,
        delete_after_seconds: int | None, idempotency_key: str | None = None,
    ) -> list[TextContent]:
        execution = await _build_research_service().execute_fallback(client, ResearchRequest(
            query=query, model=model, thinking_level=thinking_level, timeout_seconds=timeout_seconds,
            retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
            cleanup_source="gemini_deep_research:fallback", operation="gemini_deep_research",
            idempotency_key=idempotency_key,
        ))
        if execution.upstream_result is None:
            text = (_deep_research_timeout_error(timeout_seconds).text
                    if execution.result.meta.operation_state == OperationState.TIMED_OUT
                    else _operation_text(execution.result))
            return domain_text(execution.result, text, use_result_data=True)
        text = (
            f"# 📚 Deep Research 计划: {query}\n\n"
            f"- 请求模型: {model}\n"
            f"- 实际研究传输: {model_note}\n\n"
            f"{execution.upstream_result.text}\n\n"
            "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API，"
            "这里只能返回研究计划。"
        )
        return domain_text(execution.result, text, use_result_data=True)

    def _deep_research_timeout_error(timeout_seconds: int) -> TextContent:
        return TextContent(
            type="text",
            text=f"❌ Deep Research 超时（{timeout_seconds}秒）。\n\n"
            "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
            "2. 网络和认证状态是否正常？\n"
            "3. 研究主题是否适合在较短超时时间内完成？"
        )

    def _deep_research_generic_error(e: Exception) -> TextContent:
        return TextContent(
            type="text",
            text=f"❌ Deep Research 失败: {str(e)}\n\n"
            "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
            "2. 该功能在您所在的区域是否可用？"
        )

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_deep_research(
        query: str = "",
        model: str = "flash",
        thinking_level: str = "extended",
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 10,
        wait_for_completion: bool = True,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        action: Literal["start", "status", "result", "cancel"] = "start",
        operation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> list[TextContent]:
        """
        启动 Deep Research 深度研究。

        需要 AI Plus 订阅！

        参数:
        - query: 研究主题或问题
        - model: 模型选择 (thinking/pro)
        - timeout_seconds: 超时时间（默认10分钟）
        - poll_interval_seconds: 研究状态轮询间隔（默认10秒）
        - wait_for_completion: 是否等待最终报告；False 时返回 queued/running 状态与上游 ID

        工作流程:
        1. 创建研究计划
        2. 多轮搜索和分析
        3. 生成完整报告（含引用来源）
        """
        if action != "start":
            if not operation_id or not operation_id.strip():
                result: DomainResult[LongOperationData] = DomainResult.failure(
                    DomainErrorCode.INVALID_ARGUMENT, "operation_id is required for this action.",
                    verification_status="input_rejected",
                )
            else:
                result = await getattr(_build_research_service(), action)(operation_id)
            rendered = append_artifact_block(domain_text(result, _operation_text(result), use_result_data=True),
                                             result.data.artifacts if result.data else ())
            return attach_domain_result(rendered, result, use_result_data=True)
        if not query.strip():
            return domain_text(DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "query must not be blank.",
                                                   verification_status="input_rejected"),
                               "query must not be blank.", use_result_data=True)
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)
        research_model, model_note = _resolve_deep_research_transport_model(model)

        try:
            logger.info("正在启动 Deep Research")
            poll_interval = max(3, poll_interval_seconds)

            if _has_native_research_api(client):
                return await _run_native_deep_research(
                    client, query, model, model_name, research_model,
                    thinking_level, model_note, timeout_seconds, poll_interval,
                    wait_for_completion, retain_chat, delete_after_seconds, idempotency_key,
                )
            return await _run_fallback_deep_research(
                client, query, model, model_name, thinking_level,
                model_note, timeout_seconds, retain_chat, delete_after_seconds, idempotency_key,
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
            logger.error(f"Deep Research 失败: {e}")
            data = LongOperationData(
                operation="gemini_deep_research",
                state=OperationState.FAILED,
            )
            return domain_text(
                _research_domain_result(data, message=str(e)),
                _deep_research_generic_error(e).text,
                use_result_data=True,
            )

    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    async def gemini_list_research_report_actions(
        chat_id: str,
        response_format: Literal["markdown", "json"] = "markdown",
    ) -> list[TextContent]:
        """
        列出已完成 Deep Research 报告可用于“从报告创建”的动作。

        目前 Gemini Web 的 READ_CHAT 原始响应可稳定读取报告、来源和沉浸式报告 ID，
        但没有观测到网页下拉菜单的稳定私有 mutation RPC。因此这里返回 MCP 侧
        支持的等价创建动作，并标明 native_web_menu_observed=false。
        """
        client = get_gemini_client()
        await initialize_client()
        report_output = await _fetch_deep_research_immersive_report(client, chat_id)
        if not report_output:
            return [TextContent(type="text", text=f"❌ 未能在聊天 {chat_id} 中读取 Deep Research 沉浸式报告。")]

        payload = _research_report_actions_payload(report_output, chat_id)
        if response_format == "json":
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=_format_research_report_actions(payload))]

    @mcp.tool(annotations=MUTATES_LOCAL)
    async def gemini_create_from_research_report(
        chat_id: str,
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
    ) -> list[TextContent]:
        """
        从已完成 Deep Research 报告创建后续产物。

        这是 MCP 侧对 Gemini Web 报告页“创建...”菜单的等价实现：读取报告正文
        和来源，生成本地 artifact。菜单项来自 2026-06-21 网页实测；它不会调用
        未稳定观测到的 Gemini 私有菜单 mutation。
        """
        client = get_gemini_client()
        await initialize_client()
        report_output = await _fetch_deep_research_immersive_report(client, chat_id)
        if not report_output:
            data = ArtifactResultData(
                state=ArtifactState.EMPTY,
                effective_backend="MCP local renderer",
                source_chat_id=chat_id,
                media_type="research_report",
            )
            return domain_text(
                artifact_result(data),
                f"❌ 未能在聊天 {chat_id} 中读取 Deep Research 沉浸式报告。",
                use_result_data=True,
            )

        try:
            artifact = _create_research_report_artifact(
                report_output=report_output,
                artifact_type=artifact_type,
                chat_id=chat_id,
                output_dir=output_dir,
            )
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


async def _wait_for_deep_research_by_chat(
    client,
    plan,
    chat,
    start_output,
    poll_interval: int,
    timeout: int,
):
    """Compatibility adapter for the shared, deadline-bounded research poller."""
    return await _wait_by_chat(
        client, plan, chat, start_output,
        poll_interval=poll_interval,
        timeout=timeout,
        fetch_report=_fetch_deep_research_immersive_report,
        request_report=_request_completed_research_report,
    )

def _is_research_completion_message(text: str) -> bool:
    return _completion_message(text)


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
        logger.warning("Failed to retrieve completed Deep Research report by follow-up: %s", e)
        return None


async def _fetch_deep_research_immersive_report(client, cid: str):
    if not cid or not hasattr(client, "_batch_execute"):
        return None
    try:
        from gemini_webapi.constants import GRPC
        from gemini_webapi.types import RPCData
        from gemini_webapi.utils import extract_json_from_response
    except ImportError:
        return None

    try:
        response = await client._batch_execute(
            [
                RPCData(
                    rpcid=GRPC.READ_CHAT,
                    payload=json.dumps([cid, 20, None, 1, [1], [4], None, 1]),
                )
            ]
        )
        parsed = extract_json_from_response(response.text)
    except Exception as e:
        logger.warning("Failed to fetch raw Deep Research chat payload: %s", e)
        return None

    extracted = _extract_deep_research_immersive_report(parsed)
    if not extracted:
        return None

    text = extracted["report"].strip()
    sources = extracted.get("sources", [])
    if sources:
        text += "\n\n## 来源链接\n"
        for idx, source in enumerate(sources, 1):
            title = source.get("title") or source["url"]
            text += f"{idx}. [{title}]({source['url']})\n"
    return SimpleNamespace(
        text=text,
        report=extracted["report"].strip(),
        sources=sources,
        title=extracted.get("title", ""),
        immersive_id=extracted.get("immersive_id", ""),
        native_web_menu_observed=False,
    )


def _extract_deep_research_immersive_report(payload: Any) -> dict[str, Any] | None:
    best_report = ""
    best_sources: list[dict[str, str]] = []
    best_title = ""
    best_immersive_id = ""

    for _path, node in _walk_nested_json(payload):
        if not isinstance(node, list) or len(node) < 5:
            continue
        report = node[4]
        if not isinstance(report, str) or len(report) < 1000:
            continue
        if not ("[cite:" in report or "##" in report):
            continue
        if len(report) > len(best_report):
            best_report = report
            best_sources = _extract_sources_from_node(node)
            best_title = node[2] if len(node) > 2 and isinstance(node[2], str) else _title_from_markdown(report)
            best_immersive_id = node[0] if node and isinstance(node[0], str) else ""

    if not best_report:
        return None
    return {
        "report": best_report,
        "sources": best_sources,
        "title": best_title,
        "immersive_id": best_immersive_id,
    }


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
        "description": "Observed Gemini Web create-menu item: create a webpage from the report.",
    },
    {
        "type": "infographic",
        "label": "信息图",
        "description": "Observed Gemini Web create-menu item: create an infographic from the report.",
    },
    {
        "type": "quiz",
        "label": "测验",
        "description": "Observed Gemini Web create-menu item: create a quiz from the report.",
    },
    {
        "type": "flashcards",
        "label": "抽认卡",
        "description": "Observed Gemini Web create-menu item: create flashcards from the report.",
    },
    {
        "type": "audio_overview",
        "label": "音频概览",
        "description": "Observed Gemini Web create-menu item: create an audio overview from the report.",
    },
    {
        "type": "custom_app",
        "label": "描述你自己的应用",
        "description": "Observed Gemini Web create-menu custom text field for describing a custom app.",
    },
]


def _research_report_actions_payload(report_output, chat_id: str = "") -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "title": getattr(report_output, "title", "") or _title_from_markdown(getattr(report_output, "report", "")),
        "immersive_id": getattr(report_output, "immersive_id", ""),
        "native_web_menu_observed": True,
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
        f"- 原生网页下拉菜单 RPC 已观测: {'是' if payload.get('native_web_menu_observed') else '否'}",
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
        "native_web_menu_observed": True,
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
        f"- 原生网页下拉菜单 RPC 已观测: {'是' if artifact['native_web_menu_observed'] else '否'}\n\n"
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
    provider_done = bool(getattr(result, "done", False))
    if operation_state is None:
        operation_state = (
            OperationState.COMPLETED if provider_done else OperationState.TIMED_OUT
        )
    done = operation_state == OperationState.COMPLETED
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

    report_text = ((_research_report_body(final_output, provider_completed=provider_done) or "")
                   if final_output else "")
    if not done and _is_research_start_message(report_text):
        report_text = ""
    if report_text.strip():
        lines.extend(["", "## 报告", report_text.strip()])
    else:
        start_text = getattr(start_output, "text", "") if start_output else ""
        plan_text = getattr(plan, "response_text", "") or start_text
        if not done:
            if not waited_for_completion:
                state_text = "研究已排队" if operation_state is OperationState.QUEUED else "研究已启动"
                prefix = f"{state_text}；本次调用未等待最终报告。"
            elif operation_state is OperationState.TIMED_OUT:
                prefix = "研究已启动，但超时时间内还没有可读取的最终报告。"
            else:
                prefix = "研究仍在进行，尚未读取到最终报告。"
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
