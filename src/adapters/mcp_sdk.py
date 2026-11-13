"""Project-owned boundary around the MCP Python SDK v2 surface.

Protocol models moved to the standalone ``mcp-types`` distribution in SDK v2.
Keeping those imports here makes the dependency boundary explicit and prevents
domain services from depending on server or wire-protocol implementation details.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

from mcp.server import MCPServer as SDKMCPServer
from mcp_types import Icon, ImageContent, TextContent, Tool, ToolAnnotations


_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


def _sdk_content_definitions() -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for model in (TextContent, ImageContent):
        schema = model.model_json_schema(by_alias=True, mode="serialization")
        definitions.update(schema.pop("$defs", {}))
        definitions[model.__name__] = schema
    return definitions


_SDK_CONTENT_DEFINITIONS = _sdk_content_definitions()


def _compact_content_output(tool: Tool) -> Tool:
    """Trim generated labels from the SDK's standard content-block wrapper."""
    schema = tool.output_schema
    if not schema or set(schema) - {"type", "properties", "required", "$defs", "title"}:
        return tool
    properties = schema.get("properties")
    definitions = schema.get("$defs")
    if (schema.get("type") != "object" or schema.get("required") != ["result"]
            or not isinstance(properties, dict) or set(properties) != {"result"}
            or not isinstance(definitions, dict) or not definitions):
        return tool
    result = properties["result"]
    if (not isinstance(result, dict) or result.get("type") != "array"
            or set(result) - {"type", "items", "title"}
            or any(name not in _SDK_CONTENT_DEFINITIONS or definition != _SDK_CONTENT_DEFINITIONS[name]
                   for name, definition in definitions.items())):
        return tool
    items = result.get("items")
    if not isinstance(items, dict):
        return tool
    refs = [{"$ref": f"#/$defs/{name}"} for name in ("TextContent", "ImageContent") if name in definitions]
    item_refs = items["anyOf"] if set(items) == {"anyOf"} else [items]
    if not refs or item_refs not in (refs, refs[::-1]):
        return tool

    compact = deepcopy(schema)
    if compact.get("title") == f"{tool.name}Output":
        compact.pop("title")
    result_schema = compact["properties"]["result"]
    if result_schema.get("title") == "Result":
        result_schema.pop("title")
    for definition in compact["$defs"].values():
        definition.pop("title", None)
        definition.pop("description", None)
        for field in definition.get("properties", {}).values():
            if isinstance(field, dict):
                field.pop("title", None)
    return tool.model_copy(update={"output_schema": compact})


class MCPServer(SDKMCPServer):
    """Keep content-block tools structured across supported MCP SDK v2 minors.

    MCP SDK 2.1 changed automatic schema detection so functions returning MCP
    content blocks are unstructured by default. This project intentionally
    publishes and validates schemas for those tools, so preserve that contract
    at the project-owned SDK boundary while still allowing explicit opt-out.
    """

    async def list_tools(self) -> list[Tool]:
        return [_compact_content_output(tool) for tool in await super().list_tools()]

    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = True,
    ) -> Callable[[_CallableT], _CallableT]:
        return super().tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )


__all__ = [
    "MCPServer",
    "TextContent",
    "ImageContent",
    "ToolAnnotations",
]
