"""
图像工具（向后兼容，实际功能已迁移至 media.py）
"""

from .media import register_media_tools

# 为了向后兼容，导出 register_media_tools 作为 register_image_tools
register_image_tools = register_media_tools

__all__ = ["register_image_tools", "register_media_tools"]
