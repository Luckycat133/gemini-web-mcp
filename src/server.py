#!/usr/bin/env python3
"""
Gemini Web 逆向 MCP 服务器
支持: 文本对话、Deep Research、媒体生成、文件分析
"""

import json
import logging
import os
from typing import Literal

from . import __version__
from .adapters import MCPServer, TextContent, domain_error_boundary, domain_text
from .client_wrapper import (
    get_cookie_from_browser,
    get_cookie_status,
    init_startup_infrastructure,
    list_browser_cookie_profiles,
    reset_client_async,
    server_lifespan,
)
from .domain import DomainResult
from .error_handler import format_error_response, handle_error
from .services.doctor import doctor_payload as _doctor_payload, format_doctor_markdown as _format_doctor_markdown
from .services.manifest import (
    ManifestScope,
    format_tool_manifest_markdown as _format_tool_manifest_markdown,
    tool_manifest_payload as _tool_manifest_payload,
)
from .tools import groups_enable_manage, register_tools
from .tools.annotations import READ_ONLY_LOCAL

ResponseFormat = Literal["markdown", "json"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 根据环境变量选择加载的工具组
TOOL_GROUPS = os.environ.get("GEMINI_TOOLS", "core").split(",")

mcp = MCPServer(
    lifespan=server_lifespan,
    name="Gemini Web MCP Server",
    version=__version__,
    instructions=f"""
# Gemini Web MCP Server (v{__version__})

## 可用模型
- flash-lite → Web UI 3.5 Flash-Lite
- flash / fast → Web UI 3.8 Flash
- pro → Web UI 3.1 Pro
- argon → Web UI 4 Argon（仅按运行时注册表解析，不猜测 ID）
- 默认 Flash；Argon 适合写作、创意和特别困难的推理
- thinking_level=low/medium/high；standard/extended 兼容低/高；Lite 仅在当前账号基础 Flash 额度耗尽时允许，思考强度不可选
- thinking 保留为旧兼容别名

## 工具组（可配置）
- model/chat: 只调用 Gemini 模型
- history: 只读历史 list/scan/search/read/export
- history-organize: 历史整理 + native Notebook 移动
- account-read: 账号 Web surface 只读盘点
- scheduled-admin: 明确授权后管理定时操作
- core: 通用内容工作流，包含聊天、媒体、文件/URL、Deep Research
- manage/all: 完整维护/验证工具面，不建议普通 agent 默认使用
- prompts: 本地提示词库存取

Cookie 辅助工具始终可用，不依赖额外工具组。

## 主要功能
- 💬 对话: 单次对话、多轮会话、Temporary chat、Gem 对话
- 🎨 媒体生成: 图像、音乐；视频请求须以实际产物验证
-   图像: Flash-Lite → Nano Banana 2 Lite，Flash / Pro → Nano Banana 2
-   音乐为 Lyria；具体版本以可观察的上游证据为准
- 🖼️ 参考图像: 媒体生成可附带图像输入
- 📎 文件/URL 分析
- 🔎 Deep Research

## 错误处理
遇到问题时会自动提供解决方案和可使用的工具
""",
)

# 选择性注册工具组 — 默认只加载底座（chat/media/file/research）
# 若 GEMINI_TOOLS 指定了任何按需工具组，则切换为 full 模式
_register_mode = "full" if groups_enable_manage(TOOL_GROUPS) else "default"
register_tools(mcp, TOOL_GROUPS, mode=_register_mode)


def _tool_groups_include_manage() -> bool:
    return _register_mode == "full"


# ── 基础设施工具（仅在 full 模式下向 agent 暴露；default 模式下仅供后台自动化） ──


@mcp.tool(annotations=READ_ONLY_LOCAL)
async def gemini_get_tool_manifest(
    scope: ManifestScope = "all",
    response_format: ResponseFormat = "markdown",
) -> list[TextContent]:
    """返回面向 agent 的 Gemini MCP 工具清单。"""
    payload = _tool_manifest_payload(scope)
    if response_format == "json":
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
    return [TextContent(type="text", text=_format_tool_manifest_markdown(payload))]


@mcp.tool()
@domain_error_boundary("gemini_reset", logger)
async def gemini_reset() -> list[TextContent]:
    """重置客户端"""
    await reset_client_async()
    return domain_text(
        DomainResult.success(
            {"client_state": "reset"},
            verification_status="local_state_reset",
        ),
        "✅ 客户端已重置",
        use_result_data=True,
    )


@mcp.tool()
async def gemini_doctor(
    browser: str = "chrome",
    validate_browser: bool = False,
    response_format: ResponseFormat = "markdown",
) -> list[TextContent]:
    """运行安全预检，不输出 Cookie 原值。"""
    payload = _doctor_payload(browser=browser, validate_browser=validate_browser)
    if response_format == "json":
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]
    return [TextContent(type="text", text=_format_doctor_markdown(payload))]


@mcp.tool()
async def gemini_get_cookie_status() -> list[TextContent]:
    """获取 Cookie 状态"""
    status = get_cookie_status()
    if not status.get("available", False):
        return [TextContent(type="text", text="⚠️ Cookie Manager 不可用")]

    has_cookie = bool(status.get("has_cookie"))
    needs_refresh = bool(status.get("needs_refresh", False))
    cookie_text = "已设置" if has_cookie else "未设置"
    refresh_text = ("需要刷新" if needs_refresh else "无需刷新") if has_cookie else "暂无可刷新凭据"
    auto_load = status.get("browser_auto_load")
    auto_load_text = "开启" if auto_load is True else "关闭" if auto_load is False else "未知"
    auth_reason = status.get("browser_auth_reason")
    auth_note = f"\n浏览器认证诊断: {auth_reason}" if auth_reason else ""
    return [TextContent(type="text", text=f"""📊 Cookie 状态
状态: {status.get('status', 'unknown')}（本地凭据状态）
Cookie: {cookie_text}
来源: {status.get('source', 'unknown')}
浏览器自动读取: {auto_load_text}
刷新: {refresh_text}{auth_note}""")]


@mcp.tool()
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
                error_code = f" [{item['error_code']}]" if item.get("error_code") else ""
                lines.append(f"- error: {item['error']}{error_code}")
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


@mcp.tool()
async def gemini_get_cookie_from_browser(browser: str = "chrome", profile: str = "") -> list[TextContent]:
    """从浏览器获取 Cookie"""
    try:
        success = get_cookie_from_browser(browser, profile=profile)
        if success:
            suffix = f" profile={profile}" if profile else ""
            return [TextContent(type="text", text=f"✅ 已从 {browser}{suffix} 获取 Cookie")]
        else:
            reason = get_cookie_status().get("browser_auth_reason")
            if reason:
                return [TextContent(type="text", text=f"❌ 浏览器 Cookie 获取失败 [{reason}]")]
            return [TextContent(type="text", text="❌ 获取失败，请确保浏览器已登录 Gemini 网页版")]
    except Exception as e:
        error_info = handle_error(e)
        return [format_error_response(error_info)]



def main():
    """启动服务器 — 基础设施后台自动化，AI 只看到默认底座工具。"""
    if os.environ.get("GEMINI_TOOLS", "function").strip() in {"", "function", "functions"}:
        from .surfaces.function import main as function_main
        function_main()
        return
    logger.info("🚀 启动 Gemini Web MCP Server (v%s)", __version__)
    logger.info(f"🔧 加载工具组: {', '.join(TOOL_GROUPS)} (mode=default)")

    # ── 基础设施自动化（AI 不可见）──────────────────────────────
    init_startup_infrastructure()

    # 运行健康预检，异常时仅记录日志，不阻塞启动
    try:
        _doctor_payload(browser="chrome", validate_browser=False)
        logger.info("✅ 基础设施自动化就绪")
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ 基础设施自动化部分失败: %s", exc)

    mcp.run()


if __name__ == "__main__":
    main()
