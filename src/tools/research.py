"""
Deep Research MCP 工具
需要 AI Plus 订阅，研究过程可能需要数分钟。
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal
import logging

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_research_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_deep_research(
        query: str,
        model: Literal["thinking", "pro"] = "thinking",
        timeout_seconds: int = 600,
    ) -> list[TextContent]:
        """
        启动 Deep Research 深度研究。
        
        需要 AI Plus 订阅！
        
        参数:
        - query: 研究主题或问题
        - model: 模型选择 (thinking/pro)
        - timeout_seconds: 超时时间（默认10分钟）
        
        工作流程:
        1. 创建研究计划
        2. 多轮搜索和分析
        3. 生成完整报告（含引用来源）
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        try:
            # 使用 enhanced prompt 触发 Gemini 的 Deep Research 模式
            enhanced_query = (
                f"Do a Deep Research on this topic: {query}\n\n"
                "Please provide comprehensive analysis, cite sources, "
                "and give detailed information from multiple perspectives."
            )

            logger.info(f"正在启动 Deep Research: {query[:50]}...")
            
            # 发送到 Gemini 并获取结果
            response = await client.generate_content(
                enhanced_query, 
                model=config["name"],
            )

            outputs = [
                TextContent(
                    type="text", 
                    text=f"# 📚 Deep Research 报告: {query}\n\n{response.text}"
                )
            ]

            # 如果有引用来源
            if hasattr(response, 'sources') and response.sources:
                sources_text = "\n".join([f"- {s}" for s in response.sources])
                outputs.append(TextContent(
                    type="text",
                    text=f"\n\n## 📖 参考来源\n{sources_text}"
                ))

            return outputs

        except Exception as e:
            logger.error(f"Deep Research 失败: {e}")
            return [TextContent(
                type="text",
                text=f"❌ Deep Research 失败: {str(e)}\n\n"
                "请确认：\n1. 您的账户是否有 AI Plus 订阅？\n"
                "2. 该功能在您所在的区域是否可用？"
            )]
