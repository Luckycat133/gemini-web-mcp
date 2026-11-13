"""
预设提示词库 MCP 工具
支持提示词的 CRUD 操作和分类管理
"""

import json
import logging
import os
import tempfile
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Literal, Optional

from ..adapters.mcp_sdk import MCPServer, TextContent

from .annotations import DESTRUCTIVE_LOCAL

logger = logging.getLogger(__name__)

DEFAULT_PROMPTS_FILE = "prompts.json"


class PromptManager:
    def __init__(self, file_path: str = DEFAULT_PROMPTS_FILE):
        self.file_path = file_path
        self.prompts: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._load_prompts()

    def _load_prompts(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get('prompts'), dict):
                raise ValueError("提示词库格式无效")
            self.prompts = data['prompts']
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.error(f"加载提示词失败: {e}")
            raise ValueError("无法加载提示词库；原文件已保留，请先修复后重试。") from e

    def _save_prompts(self):
        temp_path = ""
        try:
            with self._lock:
                directory = os.path.dirname(os.path.abspath(self.file_path))
                prefix = f".{os.path.basename(self.file_path)}."
                with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', dir=directory, prefix=prefix, suffix='.tmp', delete=False,
                ) as f:
                    temp_path = f.name
                    json.dump({
                        'version': '1.0',
                        'updated_at': datetime.now().isoformat(),
                        'prompts': self.prompts
                    }, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.file_path)
        except Exception as e:
            logger.error(f"保存提示词失败: {e}")
            raise
        finally:
            if temp_path:
                with suppress(OSError):
                    os.unlink(temp_path)

    def create_prompt(
        self,
        name: str,
        content: str,
        category: str = "通用",
        description: str = ""
    ) -> str:
        with self._lock:
            prompt_id = str(uuid.uuid4())
            original = self.prompts
            self.prompts = {
                **original,
                prompt_id: {
                    'id': prompt_id,
                    'name': name,
                    'content': content,
                    'category': category,
                    'description': description,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                },
            }
            try:
                self._save_prompts()
            except Exception:
                self.prompts = original
                raise
            return prompt_id

    def get_prompt(self, prompt_id: str) -> Optional[dict]:
        return self.prompts.get(prompt_id)

    def list_prompts(self, category: Optional[str] = None) -> List[dict]:
        prompts = list(self.prompts.values())
        if category:
            prompts = [p for p in prompts if p['category'] == category]
        return sorted(prompts, key=lambda x: x['created_at'], reverse=True)

    def list_categories(self) -> List[str]:
        categories = set(p['category'] for p in self.prompts.values())
        return sorted(categories)

    def update_prompt(
        self,
        prompt_id: str,
        name: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        with self._lock:
            if prompt_id not in self.prompts:
                return False

            original = self.prompts
            prompt = dict(original[prompt_id])
            # 使用 `is not None` 检查，允许显式设置空字符串等 falsy 值
            if name is not None:
                prompt['name'] = name
            if content is not None:
                prompt['content'] = content
            if category is not None:
                prompt['category'] = category
            if description is not None:
                prompt['description'] = description
            prompt['updated_at'] = datetime.now().isoformat()
            self.prompts = {**original, prompt_id: prompt}
            try:
                self._save_prompts()
            except Exception:
                self.prompts = original
                raise
            return True

    def delete_prompt(self, prompt_id: str) -> bool:
        with self._lock:
            if prompt_id not in self.prompts:
                return False
            original = self.prompts
            self.prompts = {key: value for key, value in original.items() if key != prompt_id}
            try:
                self._save_prompts()
            except Exception:
                self.prompts = original
                raise
            return True


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
    categories = manager.list_categories()
    if not categories:
        return [TextContent(type="text", text="暂无分类。")]
    
    category_list = ["## 🏷️ 提示词分类"]
    for i, cat in enumerate(categories, 1):
        count = len([p for p in manager.prompts.values() if p['category'] == cat])
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
