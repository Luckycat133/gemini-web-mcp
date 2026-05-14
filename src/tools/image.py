"""
图像编辑 MCP 工具
支持: 提示词编辑、图像变体生成、参考图像上传
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client, load_images
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_image_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_edit_image(
        prompt: str,
        image_path: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """
        使用提示词编辑图像。
        
        参数说明:
        - prompt: 编辑提示词，描述您想要对图像进行的修改
        - image_path: 原始图像的文件路径
        - model: 选择编辑质量 (fast/thinking/pro)
        
        提示词示例:
        - "将天空改为日落时分的橙色"
        - "把猫换成狗，保持相同的背景"
        - "添加一些花朵在前景"
        - "把图像变成水彩画风格"
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        full_prompt = f"Edit this image: {prompt}"

        contents = [full_prompt]
        
        images = load_images([image_path])
        contents.extend(images)

        logger.info(f"正在编辑图像...")
        response = await client.generate_content(contents, model=config["name"])

        outputs = []
        result_parts = [response.text] if response.text else []

        if hasattr(response, "images") and response.images:
            for i, img in enumerate(response.images, 1):
                img_info = f"\n\n🖼️ 编辑结果 {i}: {img.title or 'Untitled'}"
                if hasattr(img, "url") and img.url:
                    img_info += f"\nURL: {img.url}"
                if hasattr(img, "alt") and img.alt:
                    img_info += f"\n描述: {img.alt}"
                result_parts.append(img_info)
        else:
            result_parts.append("\n\n⚠️ 未生成编辑后的图像，请尝试调整提示词。")

        outputs.append(TextContent(type="text", text="".join(result_parts)))
        return outputs

    @mcp.tool()
    async def gemini_variations(
        prompt: Optional[str] = None,
        image_path: Optional[str] = None,
        num_variations: int = 1,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """
        生成图像变体。
        
        参数说明:
        - prompt: 可选，描述您想要的变体风格
        - image_path: 可选，作为参考的原始图像路径
        - num_variations: 生成变体数量 (默认 1，最多 4)
        - model: 选择生成质量 (fast/thinking/pro)
        
        使用方式:
        1. 仅用 prompt: 基于提示词生成变体
        2. 仅用 image_path: 基于参考图像生成变体
        3. 两者都用: 结合参考图像和提示词生成变体
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        num_variations = min(max(1, num_variations), 4)

        base_prompt = "Generate image variations"
        if prompt:
            base_prompt += f" with style: {prompt}"
        
        contents = [base_prompt]
        
        if image_path:
            images = load_images([image_path])
            contents.extend(images)

        logger.info(f"正在生成 {num_variations} 个图像变体...")
        response = await client.generate_content(contents, model=config["name"])

        outputs = []
        result_parts = [response.text] if response.text else []

        if hasattr(response, "images") and response.images:
            for i, img in enumerate(response.images, 1):
                img_info = f"\n\n🖼️ 变体 {i}: {img.title or 'Untitled'}"
                if hasattr(img, "url") and img.url:
                    img_info += f"\nURL: {img.url}"
                if hasattr(img, "alt") and img.alt:
                    img_info += f"\n描述: {img.alt}"
                result_parts.append(img_info)
        else:
            result_parts.append("\n\n⚠️ 未生成图像变体，请尝试调整参数。")

        outputs.append(TextContent(type="text", text="".join(result_parts)))
        return outputs

