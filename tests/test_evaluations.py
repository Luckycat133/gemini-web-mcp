import xml.etree.ElementTree as ET
from pathlib import Path


EVALUATION_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "gemini_web_mcp_contract.xml"


def _qa_pairs():
    tree = ET.parse(EVALUATION_PATH)
    root = tree.getroot()
    return root.findall("qa_pair")


def test_gemini_web_mcp_contract_evaluation_shape():
    pairs = _qa_pairs()

    assert len(pairs) == 78
    for pair in pairs:
        question = pair.findtext("question")
        answer = pair.findtext("answer")
        assert question and question.strip()
        assert answer and answer.strip()
        assert "\n" not in answer.strip()


def test_research_resume_evaluation_requires_single_claim_and_original_policy():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    answer = answers["Can reusing a research operation handle resend the original query or confirm the same saved plan twice?"]
    assert "not resent" in answer and "atomically claimed and confirmed once" in answer
    assert "original model, thinking level and retention policy" in answer


def test_search_evidence_evaluation_distinguishes_retrieval_from_support():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    answer_only = answers["Does a successful source-free Gemini search answer prove grounded web retrieval?"]
    attribution = answers["When may a candidate source attribution establish grounded search evidence?"]

    assert "unverified" in answer_only and "retrieved_only" in answer_only
    assert "offsets exactly match" in attribution
    assert "selected candidate frame and final answer" in attribution
    assert "Unicode code points or UTF-16 units" in attribution
    assert "otherwise it remains retrieved_only" in attribution


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

    assert account_tools["gemini_manage_chat"]["read_only"] is False
    assert account_tools["gemini_manage_chat"]["destructive"] is False
    assert account_tools["gemini_manage_chat"]["availability"] == ["gemini-mcp-account"]
    assert account_tools["gemini_preferences"]["read_only"] is False
    assert account_tools["gemini_preferences"]["availability"] == ["gemini-mcp-account"]
    assert pairs["gemini_preferences"]
    for name in ("gemini_skills", "gemini_manage_notebook"):
        assert account_tools[name]["read_only"] is False
        assert account_tools[name]["destructive"] is True
        assert account_tools[name]["availability"] == ["gemini-mcp-account"]
        assert pairs[name]

    assert "gemini_get_tool_manifest" in tools
    assert pairs["gemini_get_tool_manifest"]
    assert tools["gemini_doctor"]["availability"] == ["always"]
    assert tools["gemini_doctor"]["read_only"] is True
    assert pairs["gemini_doctor"]

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
    assert account_tools["gemini_move_chat_to_notebook"]["read_only"] is False
    assert account_tools["gemini_move_chat_to_notebook"]["destructive"] is False
    assert account_tools["gemini_move_chat_to_notebook"]["privacy"] == "moves_private_chat_metadata"
    assert pairs["gemini_move_chat_to_notebook"]

    assert tools["gemini_manage_prompts"]["availability"] == ["prompts"]
    assert pairs["prompts"]

    assert "scheduled_action_create_and_cleanup" in workflow_names
    assert pairs["scheduled_action_create_and_cleanup"]
    assert tools["gemini_get_operation_status"]["read_only"] is True
    assert tools["gemini_get_operation_result"]["privacy"] == "reads_private_report_and_cleans_unretained_chat"
    assert tools["gemini_get_operation_result"]["destructive"] is True
    assert pairs["gemini_get_operation_result"]


def test_background_operation_evaluation_requires_same_handle_on_all_surfaces():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    start = answers["How should an agent start and recover a long media or Deep Research task by default?"]
    surfaces = answers["Which compact and assist actions recover an existing operation without starting it again?"]
    assert "background" in start and "same handle" in start and "background=false" in start
    assert "session_id" in surfaces and "operation_id" in surfaces and "no query" in surfaces


def test_business_surface_evaluations_distinguish_function_account_and_owned_cleanup():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    surfaces = answers["Which business entrypoints serve ordinary Gemini functionality and explicit account management?"]
    cleanup = answers["What is the permitted scope of gemini_cleanup on the account surface?"]
    assert "gemini-mcp-function" in surfaces and "gemini-mcp-account" in surfaces
    assert "MCP-owned" in cleanup and "titles" in cleanup


def test_natural_app_and_search_evaluations_do_not_invent_enable_switches_or_task_support():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    ask = answers["Which default tool handles ordinary questions, current-web questions and natural-language requests to connected apps?"]
    assert "gemini_ask" in ask and "Flash by default" in ask and "do not add" in ask
    assert "compatibility assist tool" in ask
    task = answers["Does a connected-app menu or a completed text answer prove that an external app action or Gemini Task completed?"]
    assert "do not prove execution" in task and "scheduled account actions are not Gemini Task mode" in task


def test_model_policy_evaluations_require_runtime_enforcement_and_community_context():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    choice = answers["Which model should an agent normally use for search and multimodal tasks, and when is Argon appropriate?"]
    gate = answers["May a caller choose Flash-Lite merely for speed or after an advanced-thinking, media, authentication or network failure?"]
    thinking = answers["What should the runtime do with a requested thinking level when quota fallback selects Flash-Lite?"]
    assert "Default to Flash" in choice and "writing and creative-work" in choice
    assert "task-specific anecdotes" in choice and "does not always require" in choice
    assert "fresh same-account basic Flash text-quota" in gate and "60 seconds" in gate
    assert "never replay" in gate and "explicit Pro or Argon" in gate
    assert "thinking_selectable=false" in thinking and "effective_thinking_level=null" in thinking
    assert "shared client boundary" in thinking


def test_chat_management_evaluations_require_exact_identity_and_readback():
    answers = {pair.findtext("question"): pair.findtext("answer") for pair in _qa_pairs()}
    assert answers["Which account tool renames, pins or unpins one exact Gemini chat without reading conversation text?"] == "gemini_manage_chat"
    mutation = answers["Does a successful rename or pin RPC response prove that the chat metadata changed?"]
    assert "fresh metadata read-back" in mutation and "changed=null" in mutation
    assert "unchanged account identity" in mutation and "not be automatically replayed" in mutation
    history = answers["How does the account surface reduce history-management context without losing target identity and pagination?"]
    assert "compact JSON" in history and "pagination" in history and "bounded explicit text reads" in history


def test_task_schedule_and_browser_evaluation_safety_boundaries():
    answers = {pair.findtext('question'): pair.findtext('answer') for pair in _qa_pairs()}
    schedule = answers['How should Task schedules be managed and verified?']
    browser = answers['What is required before explicit Task browser input?']
    evidence = answers['Does a browser screenshot or input establish exclusive pause and a successful website action?']
    assert 'separate from Chat scheduled actions' in schedule and 'independent read-back' in schedule
    assert 'frame hash' in browser and 'unique action ID' in browser and 'never replay' in browser.lower()
    assert 'do not prove an exclusive pause or website effects' in evidence
