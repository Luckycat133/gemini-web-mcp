"""
预设提示词库 MCP 工具
支持提示词的 CRUD 操作和分类管理
"""

import asyncio
import logging
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Optional

from ..adapters.mcp_sdk import MCPServer, TextContent
from ..adapters.prompt_results import PromptToolResult, prompt_result
from ..domain.prompt_requests import PromptsRequest
from ..services.prompt_requests import execute_prompt_request
from ..services.prompts import PromptLibrary
from .annotations import DESTRUCTIVE_LOCAL

logger = logging.getLogger(__name__)

DEFAULT_PROMPTS_FILE = "prompts.json"


class PromptManager:
    """Preserve the flat tool's names, defaults and text using the shared library."""

    def __init__(self, file_path: str = DEFAULT_PROMPTS_FILE):
        self.file_path = file_path
        self.library = PromptLibrary(file_path)

    @property
    def prompts(self) -> dict[str, dict[str, Any]]:
        return self.library.current()

    def create_prompt(self, name: str, content: str, category: str = "通用", description: str = "") -> str:
        return self.library.create(name, content, category, description)

    def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        return self.library.get(prompt_id)

    def list_prompts(self, category: str | None = None) -> list[dict[str, Any]]:
        return self.library.list(category)

    def list_categories(self) -> list[str]:
        return sorted({row["category"] for row in self.prompts.values()})

    def update_prompt(
        self, prompt_id: str, name: str | None = None, content: str | None = None,
        category: str | None = None, description: str | None = None,
    ) -> bool:
        return self.library.update(prompt_id, name=name, content=content, category=category, description=description)

    def delete_prompt(self, prompt_id: str) -> bool:
        return self.library.delete(prompt_id)


_prompt_manager: Optional[PromptManager] = None
_prompt_manager_lock = threading.Lock()


def get_prompt_manager() -> PromptManager:
    global _prompt_manager
    with _prompt_manager_lock:
        if _prompt_manager is None:
            _prompt_manager = PromptManager()
        return _prompt_manager



@dataclass(frozen=True)
class _PromptFields:
    """Optional prompt attributes shared by the create/update actions."""

    name: Optional[str]
    content: Optional[str]
    category: Optional[str]
    description: Optional[str]


def _prompt_list_text(manager: PromptManager, category: Optional[str]) -> list[TextContent]:
    prompts = manager.list_prompts(category=category)
    if not prompts:
        cat_text = f" (分类: {category})" if category else ""
        return [TextContent(type="text", text=f"暂无提示词{cat_text}。")]

    prompt_list = ["## 📝 预设提示词"]
    if category:
        prompt_list.append(f"**分类**: {category}")
    prompt_list.append("")

    for i, item in enumerate(prompts, 1):
        prompt_list.append(f"{i}. {item['name']} (ID: {item['id']})")
        prompt_list.append(f"   分类: {item['category']}")
        if item.get('description'):
            prompt_list.append(f"   描述: {item['description']}")
        prompt_list.append("")

    return [TextContent(type="text", text="\n".join(prompt_list))]


def _prompt_categories_text(manager: PromptManager) -> list[TextContent]:
    counts = Counter(p["category"] for p in manager.prompts.values())
    categories = sorted(counts)
    if not categories:
        return [TextContent(type="text", text="暂无分类。")]

    category_list = ["## 🏷️ 提示词分类"]
    for i, cat in enumerate(categories, 1):
        count = counts[cat]
        category_list.append(f"{i}. {cat} ({count} 个提示词)")

    return [TextContent(type="text", text="\n".join(category_list))]


def _prompt_get_text(manager: PromptManager, prompt_id: Optional[str]) -> list[TextContent]:
    if not prompt_id:
        return [TextContent(type="text", text="❌ 需要提供 prompt_id。")]

    prompt = manager.get_prompt(prompt_id)
    if not prompt:
        return [TextContent(type="text", text=f"❌ 未找到 ID 为 {prompt_id} 的提示词。")]

    prompt_detail = f"""## {prompt['name']}
**ID**: {prompt['id']}
**分类**: {prompt['category']}
**创建时间**: {prompt['created_at']}
**更新时间**: {prompt['updated_at']}

### 描述
{prompt.get('description', '无描述')}

### 提示词内容
```
{prompt['content']}
```
"""
    return [TextContent(type="text", text=prompt_detail)]


def _prompt_create_text(manager: PromptManager, fields: _PromptFields) -> list[TextContent]:
    if not fields.name or not fields.content:
        return [TextContent(type="text", text="❌ 创建提示词需要提供 name 和 content。")]

    prompt_id = manager.create_prompt(
        name=fields.name,
        content=fields.content,
        category=fields.category or "通用",
        description=fields.description or ""
    )
    return [TextContent(
        type="text",
        text=f"✅ 提示词创建成功！\nID: {prompt_id}\n名称: {fields.name}\n分类: {fields.category or '通用'}"
    )]


def _prompt_update_text(manager: PromptManager, prompt_id: Optional[str], fields: _PromptFields) -> list[TextContent]:
    if not prompt_id:
        return [TextContent(type="text", text="❌ 更新提示词需要提供 prompt_id。")]

    success = manager.update_prompt(
        prompt_id=prompt_id,
        name=fields.name,
        content=fields.content,
        category=fields.category,
        description=fields.description
    )
    if success:
        return [TextContent(type="text", text=f"✅ 提示词 {prompt_id} 更新成功。")]
    else:
        return [TextContent(type="text", text=f"❌ 未找到 ID 为 {prompt_id} 的提示词。")]


def _prompt_delete_text(manager: PromptManager, prompt_id: Optional[str]) -> list[TextContent]:
    if not prompt_id:
        return [TextContent(type="text", text="❌ 删除提示词需要提供 prompt_id。")]

    success = manager.delete_prompt(prompt_id)
    if success:
        return [TextContent(type="text", text=f"✅ 提示词 {prompt_id} 删除成功。")]
    else:
        return [TextContent(type="text", text=f"❌ 未找到 ID 为 {prompt_id} 的提示词。")]


def register_prompts_tools(mcp: MCPServer):
    @mcp.tool(annotations=DESTRUCTIVE_LOCAL, meta={"actionSemantics": {
        "read": ["list", "categories", "get", "render"],
        "mutation": ["create", "update", "delete"],
        "scope": "local",
    }})
    async def gemini_prompts(request: PromptsRequest) -> PromptToolResult:
        """Manage the local prompts.json library or render a saved template locally.

        These actions do not call Gemini. Mutations use the shared atomic storage;
        prompt_id identifies get/render/update/delete targets.
        """
        result = await asyncio.to_thread(execute_prompt_request, lambda: get_prompt_manager().library, request)
        return prompt_result(result)

    @mcp.tool(annotations=DESTRUCTIVE_LOCAL)
    async def gemini_manage_prompts(
        action: Literal["list", "list_categories", "get", "create", "update", "delete"],
        prompt_id: Optional[str] = None,
        name: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
    ) -> list[TextContent]:
        """
        管理预设提示词库。

        参数:
        - action: list, list_categories, get, create, update, delete
        - prompt_id: 提示词 ID（get/update/delete 时需要）
        - name: 提示词名称（create/update 时需要）
        - content: 提示词内容（create/update 时需要）
        - category: 分类（可选，默认"通用"）
        - description: 描述（可选）
        """
        return await asyncio.to_thread(_dispatch_prompt_text, action, prompt_id, name, content, category, description)


def _dispatch_prompt_text(
    action: str, prompt_id: str | None, name: str | None, content: str | None,
    category: str | None, description: str | None,
) -> list[TextContent]:
    try:
        manager = get_prompt_manager()
        if action == "list":
            return _prompt_list_text(manager, category)
        if action == "list_categories":
            return _prompt_categories_text(manager)
        if action == "get":
            return _prompt_get_text(manager, prompt_id)
        if action == "create":
            return _prompt_create_text(manager, _PromptFields(name=name, content=content, category=category, description=description))
        if action == "update":
            return _prompt_update_text(manager, prompt_id, _PromptFields(name=name, content=content, category=category, description=description))
        if action == "delete":
            return _prompt_delete_text(manager, prompt_id)

        return [TextContent(type="text", text="❌ 无效的 action。")]

    except Exception as e:
        logger.error(f"提示词操作失败: {e}")
        return [TextContent(type="text", text=f"❌ 失败: {str(e)}")]
