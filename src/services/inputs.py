"""Shared local attachment validation before any provider access."""

from pathlib import Path
from typing import Optional

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
