"""
对话相关 MCP 工具
支持: gemini-3-flash, gemini-3-flash-thinking, gemini-3.1-pro
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Optional, Literal
import uuid
import logging

from ..client_wrapper import (
    get_gemini_client,
    initialize_client,
    store_session,
    get_session,
    remove_session,
    list_sessions,
    load_images,
)
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_chat_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_chat(
        message: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """
        使用 Gemini 进行单次对话（非流式）。
        
        模型选择:
        - fast: Gemini 3 Flash，快速响应，音乐生成=30秒片段
        - thinking: Gemini 3 Flash Thinking，带推理链，音乐生成=完整歌曲
        - pro: Gemini 3.1 Pro，最强能力，音乐生成=完整歌曲
        
        媒体生成:
        - 所有模型支持图像生成 (Nano Banana 2)
        - 所有模型支持视频生成 (Veo 3.1)
        - 音乐时长由选择的聊天模型决定
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        # 构建输入
        contents = [message]
        if image_paths:
            images = load_images(image_paths)
            contents.extend(images)

        # 生成响应
        logger.info(f"正在使用 {config['name']} 生成响应...")
        response = await client.generate_content(contents, model=config["name"])

        # 解析输出
        outputs = []
        result_parts = []

        # 文本
        if response.text:
            result_parts.append(response.text)

        # 生成的图像
        if hasattr(response, "images") and response.images:
            for i, img in enumerate(response.images, 1):
                img_info = f"\n\n🖼️ 生成图片 {i}: {img.title or 'Untitled'}"
                if hasattr(img, "url") and img.url:
                    img_info += f"\nURL: {img.url}"
                result_parts.append(img_info)

        # 生成的视频 (Veo 3.1)
        if hasattr(response, "videos") and response.videos:
            for i, vid in enumerate(response.videos, 1):
                vid_info = f"\n\n🎬 生成视频 {i}:"
                if hasattr(vid, "url") and vid.url:
                    vid_info += f"\nURL: {vid.url}"
                if hasattr(vid, "duration"):
                    vid_info += f"\n时长: {vid.duration}秒"
                result_parts.append(vid_info)

        # 生成的音乐 (Lyria 3)
        if hasattr(response, "audio_url") and response.audio_url:
            music_type = "30s clip" if model == "fast" else "full song (~3min)"
            result_parts.append(f"\n\n🎵 生成音乐 ({music_type}): {response.audio_url}")
        elif hasattr(response, "lyrics") and response.lyrics:
            result_parts.append(f"\n\n🎵 歌词:\n{response.lyrics}")

        outputs.append(TextContent(type="text", text="".join(result_parts)))
        return outputs

    @mcp.tool()
    async def gemini_start_chat(
        system_instruction: str = "",
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """
        创建一个新的多轮对话会话。
        后续使用 gemini_send_message 在会话中继续对话。
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        session = client.start_chat(
            system_instruction=system_instruction,
            model=config["name"],
        )

        session_id = str(uuid.uuid4())[:8]
        store_session(session_id, session, model)

        return [
            TextContent(
                type="text",
                text=f"✅ 会话创建成功！\n\n会话 ID: {session_id}\n模型: {config['name']}\n\n使用 gemini_send_message 继续对话。",
            )
        ]

    @mcp.tool()
    async def gemini_send_message(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """在现有会话中发送消息，保持上下文连贯"""
        session_data = get_session(session_id)

        if not session_data:
            return [
                TextContent(
                    type="text", text=f"❌ 错误: 会话 {session_id} 不存在，请先使用 gemini_start_chat 创建。"
                )
            ]

        session = session_data["session"]

        # 构建输入
        contents = [message]
        if image_paths:
            images = load_images(image_paths)
            contents.extend(images)

        response = await session.send_message(contents)

        return [TextContent(type="text", text=response.text)]

    @mcp.tool()
    async def gemini_reset_session(session_id: str) -> list[TextContent]:
        """重置并移除指定会话"""
        remove_session(session_id)
        return [
            TextContent(type="text", text=f"✅ 会话 {session_id} 已重置。")
        ]

    @mcp.tool()
    async def gemini_list_sessions() -> list[TextContent]:
        """列出所有活跃会话"""
        sessions = list_sessions()

        if not sessions:
            return [TextContent(type="text", text="暂无活跃会话。")]

        session_list = ["活跃会话:"]
        for i, (sid, data) in enumerate(sessions.items(), 1):
            config = MODEL_CONFIG[data["model"]]
            session_list.append(f"{i}. {sid} - {config['name']}")

        return [TextContent(type="text", text="\n".join(session_list))]

    @mcp.tool()
    async def gemini_chat_stream(
        message: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """
        使用 Gemini 进行单次对话（流式）。
        
        模型选择:
        - fast: Gemini 3 Flash，快速响应，音乐生成=30秒片段
        - thinking: Gemini 3 Flash Thinking，带推理链，音乐生成=完整歌曲
        - pro: Gemini 3.1 Pro，最强能力，音乐生成=完整歌曲
        
        媒体生成:
        - 所有模型支持图像生成 (Nano Banana 2)
        - 所有模型支持视频生成 (Veo 3.1)
        - 音乐时长由选择的聊天模型决定
        
        注：此工具会收集完整的流响应并一次性返回（保持与 MCP 协议兼容性）
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        # 构建输入
        contents = [message]
        if image_paths:
            images = load_images(image_paths)
            contents.extend(images)

        # 生成流式响应
        logger.info(f"正在使用 {config['name']} 生成流式响应...")
        
        full_text = ""
        final_response = None
        
        # 收集所有流式响应片段
        async for response in client.generate_content_stream(contents, model=config["name"]):
            if response.text:
                full_text += response.text
            final_response = response

        # 解析输出
        outputs = []
        result_parts = []

        # 文本
        if full_text:
            result_parts.append(full_text)

        # 生成的图像
        if final_response and hasattr(final_response, "images") and final_response.images:
            for i, img in enumerate(final_response.images, 1):
                img_info = f"\n\n🖼️ 生成图片 {i}: {img.title or 'Untitled'}"
                if hasattr(img, "url") and img.url:
                    img_info += f"\nURL: {img.url}"
                result_parts.append(img_info)

        # 生成的视频 (Veo 3.1)
        if final_response and hasattr(final_response, "videos") and final_response.videos:
            for i, vid in enumerate(final_response.videos, 1):
                vid_info = f"\n\n🎬 生成视频 {i}:"
                if hasattr(vid, "url") and vid.url:
                    vid_info += f"\nURL: {vid.url}"
                if hasattr(vid, "duration"):
                    vid_info += f"\n时长: {vid.duration}秒"
                result_parts.append(vid_info)

        # 生成的音乐 (Lyria 3)
        if final_response and hasattr(final_response, "audio_url") and final_response.audio_url:
            music_type = "30s clip" if model == "fast" else "full song (~3min)"
            result_parts.append(f"\n\n🎵 生成音乐 ({music_type}): {final_response.audio_url}")
        elif final_response and hasattr(final_response, "lyrics") and final_response.lyrics:
            result_parts.append(f"\n\n🎵 歌词:\n{final_response.lyrics}")

        outputs.append(TextContent(type="text", text="".join(result_parts)))
        return outputs

    @mcp.tool()
    async def gemini_send_message_stream(
        session_id: str,
        message: str,
        image_paths: Optional[list[str]] = None,
    ) -> list[TextContent]:
        """在现有会话中发送消息（流式），保持上下文连贯
        
        注：此工具会收集完整的流响应并一次性返回（保持与 MCP 协议兼容性）
        """
        session_data = get_session(session_id)

        if not session_data:
            return [
                TextContent(
                    type="text", text=f"❌ 错误: 会话 {session_id} 不存在，请先使用 gemini_start_chat 创建。"
                )
            ]

        session = session_data["session"]

        # 构建输入
        contents = [message]
        if image_paths:
            images = load_images(image_paths)
            contents.extend(images)

        full_text = ""
        
        # 收集所有流式响应片段
        async for response in session.send_message_stream(contents):
            if response.text:
                full_text += response.text

        return [TextContent(type="text", text=full_text)]
