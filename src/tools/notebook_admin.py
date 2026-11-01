"""Explicit native Notebook and source administration."""
from typing import Literal

from ..adapters import domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..infrastructure.notebook_admin import request
from ..services.notebook_admin import NotebookAdminDependencies, NotebookAdminService
from ..services.notebooks import fetch_native_notebooks
from .annotations import DESTRUCTIVE_REMOTE


def _service() -> NotebookAdminService:
    return NotebookAdminService(NotebookAdminDependencies(get_gemini_client, initialize_client, request, fetch_native_notebooks))


def register_notebook_admin_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    async def gemini_manage_notebook(action: Literal["create", "get", "list_sources", "add_source", "delete_source", "delete"],
                                     notebook_id: str | None = None, source_id: str | None = None,
                                     title: str | None = None, text: str | None = None,
                                     limit: int = 20, offset: int = 0) -> list[TextContent]:
        """Manage an exact native Notebook and its sources with fresh metadata verification.

        Existing notebooks are discovered with gemini_notebooks(source="gemini").
        get/list_sources use local pagination (limit 1..100). add_source accepts plaintext only. Metadata read-back does not prove text
        was indexed or semantically available. Delete removes the Notebook AND its
        conversations: use only with explicit intent. Unknown writes are not replayed.
        """
        result = await _service().run(action, notebook_id=notebook_id, source_id=source_id, title=title, text=text,
                                      limit=limit, offset=offset)
        return domain_text(result, "Notebook metadata verified." if result.ok else domain_failure_text(result),
                           use_result_data=True)
