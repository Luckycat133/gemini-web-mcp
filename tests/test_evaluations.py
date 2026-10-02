import xml.etree.ElementTree as ET
from pathlib import Path


EVALUATION_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "gemini_web_mcp_contract.xml"


def _qa_pairs():
    tree = ET.parse(EVALUATION_PATH)
    root = tree.getroot()
    return root.findall("qa_pair")


def test_gemini_web_mcp_contract_evaluation_shape():
    pairs = _qa_pairs()

    assert len(pairs) == 42
    for pair in pairs:
        question = pair.findtext("question")
        answer = pair.findtext("answer")
        assert question and question.strip()
        assert answer and answer.strip()
        assert "\n" not in answer.strip()


def test_gemini_web_mcp_contract_answers_match_static_manifest():
    from src.tools.manage import _tool_manifest_payload, _web_capabilities_payload

    pairs = {pair.findtext("answer"): pair.findtext("question") for pair in _qa_pairs()}
    manifest = _tool_manifest_payload("all")
    history = _tool_manifest_payload("history")
    account = _tool_manifest_payload("account")
    capabilities = _web_capabilities_payload()

    tools = {tool["name"]: tool for tool in manifest["tools"]}
    history_tools = {tool["name"]: tool for tool in history["tools"]}
    account_tools = {tool["name"]: tool for tool in account["tools"]}

    assert "gemini_get_tool_manifest" in tools
    assert pairs["gemini_get_tool_manifest"]
    assert tools["gemini_doctor"]["availability"] == ["always"]
    assert tools["gemini_doctor"]["read_only"] is True
    assert pairs["gemini_doctor"]
    assert tools["gemini_reset"]["destructive"] is True
    assert tools["gemini_reset"]["read_only"] is False
    assert tools["gemini_reset"]["privacy"] == "deletes_non_retained_remote_chats"
    assert pairs["destructiveHint=true and openWorldHint=true"]

    assert history_tools["gemini_delete_chat"]["destructive"] is True
    assert pairs["gemini_delete_chat"]
    assert pairs["verification.status=verified_absent and data.deleted=true"]
    assert history_tools["gemini_cleanup_test_artifacts"]["destructive"] is True

    assert history_tools["gemini_export_chat"]["privacy"] == "reads_private_chat_text"
    assert pairs["reads_private_chat_text"]
    assert history_tools["gemini_history"]["read_only"] is True
    assert history_tools["gemini_history"]["pagination"] is True
    assert pairs["gemini_history"]
    assert history_tools["gemini_scan_chat_history_sources"]["read_only"] is True
    assert history_tools["gemini_scan_chat_history_sources"]["pagination"] is True
    assert pairs["gemini_scan_chat_history_sources"]

    workflow_names = {workflow["name"] for workflow in manifest["workflows"]}
    assert "chat_history_find_and_export" in workflow_names
    assert pairs["chat_history_find_and_export"]
    assert "test_artifact_cleanup" in workflow_names
    assert pairs["test_artifact_cleanup"]
    profile_names = {profile["name"] for profile in manifest["profiles"]}
    assert "history" in profile_names
    assert pairs["history"]

    pro_model = next(model for model in capabilities["models"] if model["display_name"] == "3.1 Pro")
    assert pro_model["alias"] == "pro"
    assert pairs["pro"]

    mode_probe = next(probe for probe in capabilities["feature_probes"] if probe["name"] == "tool_mode_status")
    assert mode_probe["rpcid"] == "MyzX6c"
    assert pairs["MyzX6c"]

    assert account_tools["gemini_list_scheduled_actions"]["pagination"] is True
    assert pairs["gemini_list_scheduled_actions"]
    assert account_tools["gemini_get_scheduled_action"]["read_only"] is True
    assert account_tools["gemini_get_scheduled_action"]["privacy"] == "reads_private_scheduled_action_details"
    assert pairs["gemini_get_scheduled_action"]
    assert account_tools["gemini_create_scheduled_action"]["read_only"] is False
    assert account_tools["gemini_create_scheduled_action"]["destructive"] is False
    assert account_tools["gemini_delete_scheduled_action"]["destructive"] is True

    assert account_tools["gemini_list_library_capabilities"]["pagination"] is True
    assert pairs["gemini_list_library_capabilities"]
    assert account_tools["gemini_account_inventory"]["read_only"] is True
    assert account_tools["gemini_notebooks"]["read_only"] is True
    notebooks = _tool_manifest_payload("notebooks")
    assert "gemini_notebooks" in {tool["name"] for tool in notebooks["tools"]}
    assert pairs["gemini_notebooks"]
    assert account_tools["gemini_move_chat_to_notebook"]["read_only"] is False
    assert account_tools["gemini_move_chat_to_notebook"]["destructive"] is False
    assert account_tools["gemini_move_chat_to_notebook"]["privacy"] == "moves_private_chat_metadata"
    assert pairs["gemini_move_chat_to_notebook"]

    assert tools["gemini_manage_prompts"]["availability"] == ["prompts"]
    assert pairs["prompts"]

    assert "scheduled_action_create_and_cleanup" in workflow_names
    assert pairs["scheduled_action_create_and_cleanup"]
    assert "media_creation_and_cleanup" in workflow_names
    assert pairs["media_creation_and_cleanup"]
    media_workflow = next(
        workflow for workflow in manifest["workflows"]
        if workflow["name"] == "media_creation_and_cleanup"
    )
    assert "source_chat_id" in media_workflow["notes"]
    assert "a missing ID does not prove no chat was created" in media_workflow["notes"]
    assert "never scan unrelated history or automatically restart" in media_workflow["notes"]


def test_focused_evaluation_answers_match_installed_catalog_and_operation_contract():
    import asyncio
    import tomllib

    from src.infrastructure.state_store import RETENTION_SECONDS
    from src.surfaces import account, create

    pairs = {pair.findtext("answer"): pair.findtext("question") for pair in _qa_pairs()}
    project = tomllib.loads((EVALUATION_PATH.parents[1] / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["gemini-mcp-create"] == "src.surfaces.create:main"
    assert pairs["gemini-mcp-create"]

    async def catalogs():
        return ({item.name: item for item in await create.mcp.list_tools()},
                {item.name: item for item in await account.mcp.list_tools()})
    creation_tools, account_tools = asyncio.run(catalogs())
    assert len(creation_tools) == len(account_tools) == 7
    assert creation_tools["gemini_get_operation_result"].input_schema["required"] == ["operation_id"]
    assert pairs["gemini_get_operation_result"]
    for name in ("gemini_generate_music", "gemini_generate_video"):
        assert "idempotency_key" in creation_tools[name].input_schema["properties"]
    assert pairs["idempotency_key"]
    assert RETENTION_SECONDS == 7 * 24 * 60 * 60 and pairs["seven days"]
    assert pairs["accepted"]
    assert account_tools["gemini_cleanup"].annotations.destructive_hint is True
    assert pairs["gemini_cleanup"]
