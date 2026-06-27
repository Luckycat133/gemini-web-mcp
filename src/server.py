#!/usr/bin/env python3
"""
Gemini Web 逆向 MCP 服务器
支持: 文本对话、Deep Research、媒体生成、文件分析
版本: 2.0 (2026.5)
"""

import logging
import os
import json
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .tools import register_tools
from .tools.annotations import MUTATES_LOCAL, READ_ONLY_LOCAL
from .tools.manage import (
    ManifestScope,
    ResponseFormat,
    _format_tool_manifest_markdown,
    _tool_manifest_payload,
)
from .client_wrapper import (
    reset_client,
    get_cookie_from_browser,
    get_cookie_status,
    list_browser_cookie_profiles,
    init_cookie_manager_integration
)
from .error_handler import handle_error, format_error_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 根据环境变量选择加载的工具组
TOOL_GROUPS = os.environ.get("GEMINI_TOOLS", "core").split(",")

mcp = FastMCP(
    "Gemini Web MCP Server",
    instructions="""
# Gemini Web MCP Server (v2.0)

## 可用模型
- flash-lite → Web UI 3.1 Flash-Lite
- flash / fast → Web UI 3.5 Flash
- pro → Web UI 3.1 Pro
- 三个 Web UI 模型都支持 thinking_level=standard/extended
- thinking 保留为旧兼容别名

## 工具组（可配置）
- core: 推荐默认工具面，适合大多数 AI 客户端
- manage: 历史对话 / Gems / 运行时模型
- prompts: 本地提示词库存取

Cookie 辅助工具始终可用，不依赖额外工具组。

## 主要功能
- 💬 对话: 单次对话、多轮会话、Temporary chat、Gem 对话
- 🎨 媒体生成: 图像、视频、音乐
-   图像首轮生成统一走 Nano Banana 2
-   音乐按模型分流：flash 系列 → Lyria 3，pro → Lyria 3 Pro
- 🖼️ 参考图像: 媒体生成可附带图像输入
- 📎 文件/URL 分析
- 🔎 Deep Research

## 错误处理
遇到问题时会自动提供解决方案和可使用的工具
""",
)

# 选择性注册工具组
register_tools(mcp, TOOL_GROUPS)


def _tool_groups_include_manage() -> bool:
    return any(group.strip() in {"all", "manage"} for group in TOOL_GROUPS)


if not _tool_groups_include_manage():

    @mcp.tool(annotations=READ_ONLY_LOCAL)
    async def gemini_get_tool_manifest(
        scope: ManifestScope = "all",
        response_format: ResponseFormat = "markdown",
    ) -> list[TextContent]:
        """
        返回面向 agent 的 Gemini MCP 工具清单。

        默认 core/prompts 配置也会暴露这个只读入口；manage/all 配置由 manage 工具组注册。
        """
        payload = _tool_manifest_payload(scope)
        if response_format == "json":
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
        return [TextContent(type="text", text=_format_tool_manifest_markdown(payload))]


@mcp.tool(annotations=MUTATES_LOCAL)
async def gemini_reset() -> list[TextContent]:
    """重置客户端"""
    reset_client()
    return [TextContent(type="text", text="✅ 客户端已重置")]


@mcp.tool(annotations=READ_ONLY_LOCAL)
async def gemini_get_cookie_status() -> list[TextContent]:
    """获取 Cookie 状态"""
    status = get_cookie_status()
    if not status.get("available", False):
        return [TextContent(type="text", text="⚠️ Cookie Manager 不可用")]

    has_cookie = bool(status.get("has_cookie"))
    needs_refresh = bool(status.get("needs_refresh", False))
    cookie_text = "已设置" if has_cookie else "未设置"
    refresh_text = "需要刷新" if needs_refresh else "无需刷新"
    return [TextContent(type="text", text=f"""📊 Cookie 状态
状态: {status.get('status', 'unknown')}
Cookie: {cookie_text}
来源: {status.get('source', 'unknown')}
刷新: {refresh_text}""")]


@mcp.tool(annotations=READ_ONLY_LOCAL)
async def gemini_list_browser_cookie_profiles(
    browser: str = "chrome",
    validate: bool = True,
    response_format: ResponseFormat = "markdown",
) -> list[TextContent]:
    """列出本地浏览器 Cookie profile 诊断信息，不返回 Cookie 值。"""
    try:
        profiles = list_browser_cookie_profiles(browser, validate=validate)
        if response_format == "json":
            return [TextContent(type="text", text=json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2))]

        lines = [f"## {browser} Cookie Profiles"]
        if not profiles:
            lines.append("- No profiles found")
        for item in profiles:
            if item.get("error"):
                lines.append(f"- error: {item['error']}")
                continue
            profile = item.get("profile", "unknown")
            psid = "yes" if item.get("has_psid") else "no"
            psidts = "yes" if item.get("has_psidts") else "no"
            count = item.get("cookie_count", 0)
            selected = "yes" if item.get("chrome_selected_profile") else "no"
            selected_dir = item.get("chrome_selected_profile_directory") or "unknown"
            account = item.get("account_status", "unvalidated")
            available = item.get("account_available")
            scheduled_count = item.get("scheduled_registry_count", "unvalidated")
            available_text = "yes" if available is True else "no" if available is False else "unknown"
            lines.append(
                f"- {profile}: psid={psid}, psidts={psidts}, cookies={count}, "
                f"chrome_selected={selected}, selected_dir={selected_dir}, "
                f"account={account}, available={available_text}, scheduled_registry_count={scheduled_count}"
            )
        return [TextContent(type="text", text="\n".join(lines))]
    except Exception as e:
        error_info = handle_error(e)
        return [format_error_response(error_info)]


@mcp.tool(annotations=MUTATES_LOCAL)
async def gemini_get_cookie_from_browser(browser: str = "chrome", profile: str = "") -> list[TextContent]:
    """从浏览器获取 Cookie"""
    try:
        success = get_cookie_from_browser(browser, profile=profile)
        if success:
            suffix = f" profile={profile}" if profile else ""
            return [TextContent(type="text", text=f"✅ 已从 {browser}{suffix} 获取 Cookie")]
        else:
            return [TextContent(type="text", text=f"❌ 获取失败，请确保已登录 gemini.google.com")]
    except Exception as e:
        error_info = handle_error(e)
        return [format_error_response(error_info)]


def main():
    """启动服务器"""
    logger.info(f"🚀 启动 Gemini Web MCP Server (v2.0)")
    logger.info(f"🔧 加载工具组: {', '.join(TOOL_GROUPS)}")
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
