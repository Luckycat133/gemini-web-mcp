"""
工具模块共享函数
"""

from pathlib import Path
from typing import List, Optional
from mcp.types import TextContent

MAX_IMAGE_ATTACHMENT_BYTES = 25 * 1024 * 1024
IMAGE_ATTACHMENT_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def validate_local_file_path(
    file_path: str,
    *,
    allowed_extensions: Optional[set[str]] = None,
    max_bytes: Optional[int] = None,
) -> tuple[bool, str]:
    """Normalize and validate a local file path before sending it to Gemini."""
    if not file_path or not str(file_path).strip():
        return False, "文件路径不能为空"

    raw_path = str(file_path).strip()
    if any(part == ".." for part in Path(raw_path).parts):
        return False, "检测到路径遍历尝试，不允许使用 '..' 路径段"

    try:
        path = Path(raw_path).expanduser()
        absolute_path = path if path.is_absolute() else Path.cwd() / path
        absolute_path = absolute_path.resolve(strict=False)
    except Exception as e:
        return False, f"路径验证失败: {str(e)}"

    if not absolute_path.exists():
        return False, f"文件未找到: {absolute_path}"
    if not absolute_path.is_file():
        return False, f"路径不是文件: {absolute_path}"

    if allowed_extensions is not None:
        normalized_exts = {ext.lower() for ext in allowed_extensions}
        if absolute_path.suffix.lower() not in normalized_exts:
            allowed = ", ".join(sorted(normalized_exts))
            return False, f"不支持的附件类型: {absolute_path.suffix or '(none)'}；允许: {allowed}"

    if max_bytes is not None:
        try:
            size = absolute_path.stat().st_size
        except OSError as e:
            return False, f"无法读取文件大小: {str(e)}"
        if size > max_bytes:
            return False, f"附件过大: {size} bytes；最大允许 {max_bytes} bytes"

    return True, str(absolute_path)


def validate_image_paths(image_paths: Optional[list[str]]) -> tuple[bool, list[str], str]:
    """Validate optional local image attachment paths."""
    if not image_paths:
        return True, [], ""

    normalized_paths: list[str] = []
    for image_path in image_paths:
        ok, value = validate_local_file_path(
            image_path,
            allowed_extensions=IMAGE_ATTACHMENT_EXTENSIONS,
            max_bytes=MAX_IMAGE_ATTACHMENT_BYTES,
        )
        if not ok:
            return False, [], value
        normalized_paths.append(value)
    return True, normalized_paths, ""


def validate_optional_image_path(image_path: Optional[str]) -> tuple[bool, Optional[str], str]:
    """Validate one optional local image attachment path."""
    if not image_path:
        return True, None, ""
    ok, paths, message = validate_image_paths([image_path])
    if not ok:
        return False, None, message
    return True, paths[0], ""


def extract_remote_chat_id(response) -> Optional[str]:
    cid = getattr(response, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid

    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid
    return None


def parse_response(
    response,
    model: str = "flash",
    text_override: Optional[str] = None,
) -> List[TextContent]:
    """解析 Gemini 响应，提取文本、图片、视频、音乐"""
    result_parts = []
    text = text_override if text_override is not None else getattr(response, "text", "")
    if text:
        result_parts.append(text)

    if hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            info = f"\n\n🖼️ 图片 {i}: {img.title or 'Untitled'}"
            if hasattr(img, "url") and img.url:
                info += f"\nURL: {img.url}"
            if hasattr(img, "alt") and img.alt:
                info += f"\n描述: {img.alt}"
            result_parts.append(info)

    if hasattr(response, "videos") and response.videos:
        for i, vid in enumerate(response.videos, 1):
            info = f"\n\n🎬 视频 {i}: {vid.title or 'Untitled'}"
            if hasattr(vid, "url") and vid.url:
                info += f"\nURL: {vid.url}"
            result_parts.append(info)

    if hasattr(response, "media") and response.media:
        for i, m in enumerate(response.media, 1):
            music_type = {
                "flash-lite": "Lyria 3",
                "flash": "Lyria 3",
                "fast": "Lyria 3",
                "thinking": "Lyria 3",
                "pro": "Lyria 3 Pro",
            }.get(model, "时长取决于当前模型")
            info = f"\n\n🎵 音乐 {i} ({music_type}): {m.title or 'Untitled'}"
            if hasattr(m, "mp3_url") and m.mp3_url:
                info += f"\nMP3: {m.mp3_url}"
            if hasattr(m, "url") and m.url:
                info += f"\nURL: {m.url}"
            result_parts.append(info)

    remote_chat_id = extract_remote_chat_id(response)
    if remote_chat_id:
        result_parts.append(f"\n\nRemote chat ID: {remote_chat_id}")

    return [TextContent(type="text", text="".join(result_parts))]


def get_stream_text_piece(response) -> str:
    """优先使用库提供的 text_delta，回退到完整 text。"""
    if hasattr(response, "text_delta"):
        return getattr(response, "text_delta", "") or ""
    return getattr(response, "text", "") or ""
