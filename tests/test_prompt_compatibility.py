"""Offline coverage for the released local Prompt facade and shared JSON store."""

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from mcp import Client, StdioServerParameters, stdio_client

from src.services.prompt_requests import _render
from src.services.prompts import PromptLibrary
from src.tools.prompts import PromptManager


def test_manifest_enables_typed_prompt_on_the_explicit_profile(monkeypatch):
    from src.services.manifest import tool_manifest_payload

    monkeypatch.setenv("GEMINI_TOOLS", "prompts")
    catalog = {tool["name"]: tool for tool in tool_manifest_payload("all")["tools"]}
    assert catalog["gemini_prompts"]["current_enabled"] is True
    assert catalog["gemini_prompts"]["availability"] == ["prompts"]


def test_two_flat_managers_keep_distinct_ids_after_interleaved_writes(tmp_path):
    path = str(tmp_path / "prompts.json")
    first, second = PromptManager(path), PromptManager(path)
    left = first.create_prompt("First", "Synthetic left")
    right = second.create_prompt("Second", "Synthetic right")
    assert first.update_prompt(right, description="changed by first")
    assert second.get_prompt(right)["description"] == "changed by first"
    assert set(PromptManager(path).prompts) == {left, right}
    assert first.delete_prompt(left)
    assert set(second.prompts) == {right}


def test_stable_sidecar_serializes_concurrent_writers(tmp_path):
    path = tmp_path / "prompts.json"
    count = 12
    ready = Barrier(count, timeout=10)

    def write(i):
        store = PromptLibrary(path)
        ready.wait()
        return store.create(f"Prompt {i}", "Synthetic")

    with ThreadPoolExecutor(max_workers=count) as pool:
        ids = list(pool.map(write, range(count)))
    assert len(set(ids)) == count
    assert set(PromptLibrary(path).current()) == set(ids)


def test_existing_manager_does_not_overwrite_newly_malformed_storage(tmp_path):
    path = tmp_path / "prompts.json"
    manager = PromptManager(str(path))
    manager.create_prompt("First", "Synthetic")
    corrupt = b'{"prompts": {"x": {"id": "not-x"}}}'
    path.write_bytes(corrupt)
    with pytest.raises(ValueError, match="原文件已保留"):
        manager.create_prompt("Second", "Must not write")
    assert path.read_bytes() == corrupt


@pytest.mark.parametrize("template", ["{name:{width}}", "{name:10000}", "{name[0]}"])
def test_render_rejects_dynamic_traversal_and_oversized_formatting(template):
    with pytest.raises(ValueError):
        _render(template, {"name": "Synthetic", "width": "4"})


def test_released_prompt_actions_over_real_stdio_with_shared_flat_tool(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env.update({
        "PYTHONPATH": str(root), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path),
        "GEMINI_TOOLS": "prompts", "GEMINI_BROWSER_AUTO_LOAD": "false", "GEMINI_AUTO_REFRESH": "false",
    })
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "src.server"], env=env, cwd=tmp_path)

    async def run():
        async with Client(stdio_client(parameters), cache=None) as a, Client(stdio_client(parameters), cache=None) as b:
            tools = {tool.name: tool for tool in (await a.list_tools()).tools}
            typed = tools["gemini_prompts"]
            assert typed.annotations.destructive_hint and not typed.annotations.open_world_hint
            assert typed.meta["actionSemantics"] == {
                "read": ["list", "categories", "get", "render"], "mutation": ["create", "update", "delete"],
                "scope": "local",
            }
            assert typed.input_schema["required"] == ["request"]
            assert set(typed.input_schema["properties"]["request"]["discriminator"]["mapping"]) == {
                "list", "categories", "get", "render", "create", "update", "delete",
            }
            assert "meta" in typed.output_schema["properties"]

            async def call(client, action, **kwargs):
                result = await client.call_tool("gemini_prompts", {"request": {"action": action, **kwargs}})
                assert not result.is_error and result.result_type == "complete"
                assert result.structured_content == result.content[0].meta["domain_result"]
                assert result.structured_content["meta"]["details"]["facade"] == "prompts"
                return result.structured_content

            assert (await call(a, "list"))["data"]["total_count"] == 0
            empty = await b.call_tool("gemini_manage_prompts", {"action": "list"})
            assert "暂无提示词" in empty.content[0].text

            created = await call(a, "create", name="Typed", content="Hello {who:>7} {{literal}}")
            item = created["data"]["item"]
            pid = item["id"]
            assert item["category"] == "general"
            assert created["meta"]["verification_status"] == "verified_created"
            flat = await b.call_tool("gemini_manage_prompts", {"action": "create", "name": "Flat", "content": "Other"})
            assert "创建成功" in flat.content[0].text

            listing = (await call(a, "list", limit=1))["data"]
            assert listing["total_count"] == 2 and listing["next_offset"] == 1
            assert listing["items"][0]["category"] == "通用"
            tail = (await call(a, "list", limit=1, offset=1))["data"]
            assert tail["items"][0]["id"] == pid and tail["next_offset"] is None
            assert (await call(b, "categories"))["data"]["categories"] == ["general", "通用"]
            assert (await call(b, "list", category="general"))["data"]["items"] == [item]
            assert (await call(b, "get", prompt_id=pid))["data"]["item"] == item
            rendered = await call(b, "render", prompt_id=pid, variables={"who": "World"})
            assert rendered["data"]["text"] == "Hello   World {literal}"
            assert not (await call(a, "render", prompt_id=pid))["ok"]

            updated = await call(a, "update", prompt_id=pid, content="", description="")
            assert updated["data"]["item"]["content"] == ""
            assert updated["data"]["item"]["name"] == "Typed"
            assert (await call(a, "delete", prompt_id=pid))["data"] == {"prompt_id": pid, "deleted": True}
            assert not (await call(b, "get", prompt_id=pid))["ok"]
            assert (await call(b, "list"))["data"]["total_count"] == 1

    asyncio.run(run())
