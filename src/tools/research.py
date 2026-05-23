"""
Deep Research MCP 工具
需要 AI Plus 订阅，研究过程可能需要数分钟。
"""

import asyncio
from types import SimpleNamespace
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
import logging

from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_model_name

logger = logging.getLogger(__name__)


def register_research_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_deep_research(
        query: str,
        model: str = "flash",
        thinking_level: str = "extended",
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 10,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
    ) -> list[TextContent]:
        """
        启动 Deep Research 深度研究。
        
        需要 AI Plus 订阅！
        
        参数:
        - query: 研究主题或问题
        - model: 模型选择 (thinking/pro)
        - timeout_seconds: 超时时间（默认10分钟）
        - poll_interval_seconds: 研究状态轮询间隔（默认10秒）
        
        工作流程:
        1. 创建研究计划
        2. 多轮搜索和分析
        3. 生成完整报告（含引用来源）
        """
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        model_name = resolve_model_name(model)

        try:
            logger.info(f"正在启动 Deep Research: {query[:50]}...")

            poll_interval = max(3, poll_interval_seconds)
            if all(
                hasattr(client, attr)
                for attr in (
                    "create_deep_research_plan",
                    "start_deep_research",
                    "wait_for_deep_research",
                )
            ):
                chat = client.start_chat(model=model_name)
                thinking_scope = getattr(client, "thinking_scope", None)
                scope = (
                    thinking_scope(model_name, thinking_level)
                    if thinking_scope
                    else _null_scope()
                )
                with scope:
                    plan = await asyncio.wait_for(
                        client.create_deep_research_plan(
                            query,
                            chat=chat,
                            model=model_name,
                        ),
                        timeout=min(timeout_seconds, 120),
                    )
                    start_output = await asyncio.wait_for(
                        client.start_deep_research(plan, chat=chat),
                        timeout=min(timeout_seconds, 120),
                    )
                if getattr(plan, "research_id", None):
                    result = await asyncio.wait_for(
                        client.wait_for_deep_research(
                            plan,
                            poll_interval=poll_interval,
                            timeout=timeout_seconds,
                        ),
                        timeout=timeout_seconds + poll_interval + 10,
                    )
                else:
                    result = await _wait_for_deep_research_by_chat(
                        client=client,
                        plan=plan,
                        chat=chat,
                        start_output=start_output,
                        poll_interval=poll_interval,
                        timeout=timeout_seconds,
                    )
                result.start_output = start_output
                schedule_remote_chat_cleanup(
                    getattr(chat, "cid", None) or getattr(plan, "cid", None),
                    retain_chat=retain_chat,
                    delete_after_seconds=delete_after_seconds,
                    source="gemini_deep_research",
                )
                return [_format_deep_research_result(query, result)]

            response = await asyncio.wait_for(
                client.generate_content(
                    query,
                    model=model_name,
                    deep_research=True,
                    thinking_level=thinking_level,
                    timeout=timeout_seconds,
                ),
                timeout=timeout_seconds,
            )
            schedule_remote_chat_cleanup_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source="gemini_deep_research:fallback",
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"# 📚 Deep Research 计划: {query}\n\n"
                        f"{response.text}\n\n"
                        "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API，"
                        "这里只能返回研究计划。"
                    ),
                )
            ]

        except asyncio.TimeoutError:
            logger.error("Deep Research 失败: request timed out")
            return [TextContent(
                type="text",
                text=f"❌ Deep Research 超时（{timeout_seconds}秒）。\n\n"
                "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
                "2. 网络和认证状态是否正常？\n"
                "3. 研究主题是否适合在较短超时时间内完成？"
            )]
        except Exception as e:
            logger.error(f"Deep Research 失败: {e}")
            return [TextContent(
                type="text",
                text=f"❌ Deep Research 失败: {str(e)}\n\n"
                "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
                "2. 该功能在您所在的区域是否可用？"
            )]


async def _wait_for_deep_research_by_chat(
    client,
    plan,
    chat,
    start_output,
    poll_interval: int,
    timeout: int,
):
    """Fallback for Gemini Web responses that omit research_id in the plan."""
    start_text = (getattr(start_output, "text", "") or "").strip()
    cid = getattr(chat, "cid", "") or getattr(plan, "cid", "")
    started = asyncio.get_running_loop().time()
    checks = 0
    latest_output = None

    while (asyncio.get_running_loop().time() - started) < timeout:
        if cid and hasattr(client, "fetch_latest_chat_response"):
            latest_output = await client.fetch_latest_chat_response(cid)
            latest_text = (getattr(latest_output, "text", "") or "").strip()
            if latest_text and latest_text != start_text and not _is_research_start_message(latest_text):
                return SimpleNamespace(
                    plan=plan,
                    start_output=start_output,
                    final_output=latest_output,
                    statuses=[
                        SimpleNamespace(
                            state="completed",
                            done=True,
                            notes=[f"chat history produced final output after {checks + 1} checks"],
                        )
                    ],
                    done=True,
                )
        checks += 1
        await asyncio.sleep(poll_interval)

    return SimpleNamespace(
        plan=plan,
        start_output=start_output,
        final_output=None,
        statuses=[
            SimpleNamespace(
                state="running",
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
    )


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
        )
    )


class _null_scope:
    """Context manager fallback for test doubles and older clients."""

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _format_deep_research_result(query: str, result) -> TextContent:
    plan = result.plan
    statuses = getattr(result, "statuses", []) or []
    final_output = getattr(result, "final_output", None)
    start_output = getattr(result, "start_output", None)
    heading = "报告" if getattr(result, "done", False) else "状态"

    lines = [
        f"# 📚 Deep Research {heading}: {query}",
        "",
        "## 状态",
        f"- 完成: {'是' if getattr(result, 'done', False) else '否'}",
    ]
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

    report_text = getattr(final_output, "text", "") if final_output else ""
    if not getattr(result, "done", False) and _is_research_start_message(report_text):
        report_text = ""
    if report_text.strip():
        lines.extend(["", "## 报告", report_text.strip()])
    else:
        start_text = getattr(start_output, "text", "") if start_output else ""
        plan_text = getattr(plan, "response_text", "") or start_text
        if not getattr(result, "done", False):
            plan_text = (
                "研究已启动，但超时时间内还没有可读取的最终报告。\n\n"
                + plan_text
            )
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
            done = "done" if getattr(status, "done", False) else "running"
            note = ""
            notes = getattr(status, "notes", []) or []
            if notes:
                note = f" - {notes[-1]}"
            lines.append(f"- {state} ({done}){note}")

    return TextContent(type="text", text="\n".join(lines))
