import xml.etree.ElementTree as ET
from pathlib import Path


EVALUATION_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "gemini_web_mcp_contract.xml"


def _qa_pairs():
    tree = ET.parse(EVALUATION_PATH)
    root = tree.getroot()
    return root.findall("qa_pair")


def test_gemini_web_mcp_contract_evaluation_shape():
    pairs = _qa_pairs()

    assert len(pairs) == 11
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

    assert history_tools["gemini_delete_chat"]["destructive"] is True
    assert pairs["gemini_delete_chat"]

    assert history_tools["gemini_export_chat"]["privacy"] == "reads_private_chat_text"
    assert pairs["reads_private_chat_text"]

    workflow_names = {workflow["name"] for workflow in manifest["workflows"]}
    assert "chat_history_find_and_export" in workflow_names
    assert pairs["chat_history_find_and_export"]

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

    assert tools["gemini_manage_prompts"]["availability"] == ["prompts"]
    assert pairs["prompts"]

    assert "scheduled_action_create_and_cleanup" in workflow_names
    assert pairs["scheduled_action_create_and_cleanup"]
