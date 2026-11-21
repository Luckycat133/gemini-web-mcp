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
                                     limit: int = 20, offset: int = 0, url: str | None = None,
                                     file_path: str | None = None) -> list[TextContent]:
        """Manage an exact native Notebook and its sources with fresh metadata verification.

        Existing notebooks are discovered with gemini_notebooks(source="gemini").
        get/list_sources paginate local results (limit 1..100). For add_source, supply
        a title and exactly one of plaintext in text, a web URL in url, or a local
        UTF-8 .txt file up to 1 MiB in file_path. Use list_sources to check the same
        source_id until source state 2 confirms indexing. The URL or upload reference
        is verified separately from the source contents. Notebook deletion also removes
        its conversations and requires explicit intent. Each write is dispatched once.
        """
        result = await _service().run(action, notebook_id=notebook_id, source_id=source_id, title=title,
                                      text=text, url=url, file_path=file_path, limit=limit, offset=offset)
        if not result.ok:
            message = domain_failure_text(result)
        else:
            message = {
                "source_processing": "Notebook source added. Indexing is in progress.",
                "source_url_ready_verified": "Notebook URL source indexed and URL verified.",
                "source_url_unobserved": "Notebook source indexed. Original URL remains unverified.",
                "source_file_ready_verified": "Notebook file source indexed and upload reference verified.",
                "source_file_reference_unobserved": "Notebook file source indexed. Upload reference remains unverified.",
            }.get(result.meta.verification_status, "Notebook metadata verified.")
        return domain_text(result, message, use_result_data=True)
