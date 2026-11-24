"""Observed web Task card shapes with synthetic IDs and no remote mutations."""

import asyncio
from contextlib import nullcontext
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.adapters.mcp_sdk import MCPServer
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.task_approval_browser import _MAC_CHROME, _run_browser
from src.services import web_task_approvals as approvals
from src.services.task_progress import TaskProgressService
from src.tools import web_tasks


_FIXTURE_TITLE = "合成待办"
_FIXTURE_PSID = "synthetic-web-task-approval-account"
_RULE_ENV = "GEMINI_TASK_GOOGLE_TASKS_DELETE_RULES"


def _turn(*, kind="browser", rid="r_fixture", once="synthetic-once", status=5, selected=False,
          title=_FIXTURE_TITLE, detail_title=None):
    if kind == "browser":
        card = [None, "浏览器", "Synthetic permission", None, None, None,
                [["始终允许", [None, None, None, "synthetic-persistent"]]],
                [["允许一次", [None, None, None, once]]], [["不允许", [None, None, None, "synthetic-deny"]]], 1]
    else:
        detail = title if detail_title is None else detail_title
        card = [None, "Google Tasks", "要允许 Gemini **删除任务**吗？", None, None, None,
                [["允许", [None, None, None, "允许"]]], None, [["不允许", [None, None, None, "不允许"]]],
                1, None, [[None, None, f"**{detail}**", True]]]
    if selected:
        card += [None] * (14 - len(card)) + [1]
    details = [None] * 35
    details[34] = [[None, {"154": card}]]
    candidate = [None] * 13
    candidate[0], candidate[8], candidate[12] = "rc_fixture", [2], details
    if kind == "delete":
        candidate[1] = [f"确定要删除“{title}”吗？\n\n[synthetic approval]"]
    return {"chat_id": "c_fixture", "response_id": rid, "candidate": candidate,
            "upstream_status": status, "latest_verified": True}


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", _FIXTURE_PSID)
    monkeypatch.setenv("GEMINI_TASK_WEB_APPROVALS", "true")
    monkeypatch.setenv("GEMINI_TASK_AUTO_APPROVE", "true")
    monkeypatch.delenv(_RULE_ENV, raising=False)
    monkeypatch.setattr(approvals, "fetch_task_metadata_page", AsyncMock(return_value={
        "items": [{"chat_id": "c_fixture", "upstream_status": 5}], "next_page_token": None,
    }))
    client = SimpleNamespace(
        generate_content=AsyncMock(), start_chat=lambda **kw: kw["metadata"],
        task_generation_scope=lambda *args, **kwargs: nullcontext(),
    )
    return approvals.WebTaskApprovalWatcher(), client, OperationStore(tmp_path / "operations.sqlite3")


def test_web_task_browser_approval_is_verified_once_across_watchers_without_ownership(watcher, monkeypatch):
    run, client, store = watcher
    read = AsyncMock(side_effect=[_turn(), _turn(), _turn(rid="r_new", status=3), _turn(), _turn()])
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", read)
    asyncio.run(run.scan(client, store))
    asyncio.run(approvals.WebTaskApprovalWatcher().scan(client, OperationStore(store.path)))
    client.generate_content.assert_awaited_once_with(
        prompt="synthetic-once", model="flash", chat=["c_fixture", "r_fixture", "rc_fixture"],
    )
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state FROM web_task_approval_claims").fetchall() == [("continuation_observed",)]
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone() == (0,)
    assert b"synthetic-once" not in store.path.read_bytes()


@pytest.mark.parametrize("reason", ["delete", "generic_allow", "selected", "changed", "disabled", "account_changed", "owned"])
def test_web_watcher_leaves_delete_stale_or_unauthorized_cards_waiting(watcher, monkeypatch, reason):
    run, client, store = watcher
    if reason == "disabled":
        monkeypatch.delenv("GEMINI_TASK_WEB_APPROVALS")
    if reason == "owned":
        store.create("op_fixture", "gemini_run_task")
        assert store.claim_worker("op_fixture", "worker")
        assert store.checkpoint_media("op_fixture", worker_token="worker", chat_id="c_fixture",
                                      response_id="r_fixture", candidate_id="rc_fixture")

    async def read(*args):
        if reason == "account_changed":
            monkeypatch.setenv("GEMINI_PSID", "synthetic-different-account")
        return _turn(kind="delete" if reason == "delete" else "browser", selected=reason == "selected",
                     once="允许" if reason == "generic_allow" else "synthetic-once")

    fetch = AsyncMock(side_effect=[_turn(), _turn(once="changed-query")] if reason == "changed" else read)
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", fetch)
    asyncio.run(run.scan(client, store))
    client.generate_content.assert_not_awaited()
    if reason in {"disabled", "owned"}:
        fetch.assert_not_awaited()


def test_unverified_browser_send_does_not_replay(watcher, monkeypatch):
    run, client, store = watcher
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", AsyncMock(return_value=_turn()))
    client.generate_content.side_effect = TimeoutError
    asyncio.run(run.scan(client, store))
    asyncio.run(run.scan(client, store))
    assert client.generate_content.await_count == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state FROM web_task_approval_claims").fetchall() == [("dispatch_unverified",)]


def _enable_delete(monkeypatch, *, title=_FIXTURE_TITLE):
    monkeypatch.setenv(_RULE_ENV, json.dumps([{"task_id": "c_fixture", "title": title}]))
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {"__Secure-1PSID": _FIXTURE_PSID})


def test_authorized_delete_uses_the_original_browser_card_once(watcher, monkeypatch):
    run, client, store = watcher
    _enable_delete(monkeypatch)
    read = AsyncMock(side_effect=[_turn(kind="delete"), _turn(kind="delete"), _turn(rid="r_new", status=3),
                                  _turn(kind="delete", rid="r_later_same_title")])
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", read)

    async def browser(url, cookies, script, argument, *, before_execute, timeout):
        assert url == "https://gemini.google.com/app/fixture"
        assert cookies == {"__Secure-1PSID": _FIXTURE_PSID}
        assert "approve.click()" in script
        assert argument == {"taskId": "fixture", "responseId": "r_fixture", "title": _FIXTURE_TITLE}
        assert await before_execute()
        return {"status": "activated"}

    open_browser = AsyncMock(side_effect=browser)
    monkeypatch.setattr(approvals, "run_task_approval_browser", open_browser)
    asyncio.run(run.scan(client, store))
    asyncio.run(approvals.WebTaskApprovalWatcher().scan(client, OperationStore(store.path)))
    client.generate_content.assert_not_awaited()
    assert open_browser.await_count == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state FROM web_task_approval_claims").fetchall() == [("continuation_observed",)]
        assert db.execute("SELECT COUNT(*) FROM web_task_delete_claims").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM operations").fetchone() == (0,)
    assert _FIXTURE_TITLE.encode() not in store.path.read_bytes()


@pytest.mark.skipif(not _MAC_CHROME.is_file(), reason="System Chrome is unavailable")
def test_background_watcher_automatically_clicks_a_native_card_in_chrome(watcher, monkeypatch, tmp_path):
    run, client, store = watcher
    _enable_delete(monkeypatch)
    fixture = tmp_path / "task.html"
    fixture.write_text(f"""<meta charset="utf-8">
        <style>action-card, remy-confirmation-card-v2, gem-banner, gem-button {{ display: block; }}</style>
        <div id="model-response-message-contentr_fixture">
            <p>确定要删除“{_FIXTURE_TITLE}”吗？</p>
            <action-card><remy-confirmation-card-v2><gem-banner>
                <h3>Google Tasks</h3><p>要允许 Gemini <strong>删除任务</strong>吗？</p>
                <div class="button-group">
                    <gem-button><button>不允许</button></gem-button>
                    <gem-button class="primary-button">
                        <button id="approve" data-clicks="0"
                            onclick="this.dataset.clicks = Number(this.dataset.clicks) + 1; this.disabled = true;">
                            允许
                        </button>
                    </gem-button>
                </div>
            </gem-banner></remy-confirmation-card-v2></action-card>
        </div>""", encoding="utf-8")
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", AsyncMock(side_effect=[
        _turn(kind="delete"), _turn(kind="delete"), _turn(rid="r_new", status=3), _turn(kind="delete"),
    ]))
    results = []

    async def browser(url, cookies, script, argument, *, before_execute, timeout):
        # Use the production selector with a synthetic location on an offline local page.
        fixture_script = f"""async (args) => {{
            const location = {{origin: "https://gemini.google.com", pathname: "/app/fixture"}};
            const result = await ({script})(args);
            return {{...result, clicks: Number(document.getElementById("approve").dataset.clicks)}};
        }}"""
        outcome = await _run_browser(str(_MAC_CHROME), fixture.as_uri(), [], fixture_script, argument,
                                     before_execute=before_execute, timeout=timeout)
        results.append(outcome)
        return outcome

    monkeypatch.setattr(approvals, "run_task_approval_browser", browser)
    asyncio.run(run.scan(client, store))
    asyncio.run(approvals.WebTaskApprovalWatcher().scan(client, OperationStore(store.path)))
    assert results == [{"status": "activated", "clicks": 1}]
    client.generate_content.assert_not_awaited()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT state FROM web_task_approval_claims").fetchall() == [("continuation_observed",)]


def test_delete_rule_does_not_open_chrome_with_another_accounts_cookies(watcher, monkeypatch):
    run, client, store = watcher
    _enable_delete(monkeypatch)
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {"__Secure-1PSID": "other-account"})
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", AsyncMock(return_value=_turn(kind="delete")))
    open_browser = AsyncMock()
    monkeypatch.setattr(approvals, "run_task_approval_browser", open_browser)
    asyncio.run(run.scan(client, store))
    open_browser.assert_not_awaited()


@pytest.mark.parametrize("mode", ["disabled", "different_title", "different_details", "changed_turn", "account_changed"])
def test_delete_rule_requires_exact_title_card_and_account(watcher, monkeypatch, mode):
    run, client, store = watcher
    if mode != "disabled":
        _enable_delete(monkeypatch, title="另一个待办" if mode == "different_title" else _FIXTURE_TITLE)
    item = _turn(kind="delete", detail_title="另一个待办" if mode == "different_details" else None)
    final = _turn(kind="delete", rid="r_changed" if mode == "changed_turn" else "r_fixture")
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", AsyncMock(side_effect=[item, final]))

    async def browser(*args, before_execute, **kwargs):
        if mode == "account_changed":
            monkeypatch.setenv("GEMINI_PSID", "different-account")
        assert not await before_execute()
        return {"status": "skipped"}

    open_browser = AsyncMock(side_effect=browser)
    monkeypatch.setattr(approvals, "run_task_approval_browser", open_browser)
    asyncio.run(run.scan(client, store))
    client.generate_content.assert_not_awaited()
    assert open_browser.await_count == int(mode in {"changed_turn", "account_changed"})
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM web_task_delete_claims").fetchone() == (0,)


@pytest.mark.parametrize("failure, expected_count", [("not_matched", 2), ("uncertain", 1)])
def test_browser_retry_requires_positive_evidence_of_no_click(watcher, monkeypatch, failure, expected_count):
    run, client, store = watcher
    _enable_delete(monkeypatch)
    monkeypatch.setattr(approvals, "fetch_task_progress_turn", AsyncMock(return_value=_turn(kind="delete")))

    async def browser(*args, before_execute, **kwargs):
        assert await before_execute()
        if failure == "uncertain":
            raise TimeoutError
        return {"status": "not_matched"}

    open_browser = AsyncMock(side_effect=browser)
    monkeypatch.setattr(approvals, "run_task_approval_browser", open_browser)
    asyncio.run(run.scan(client, store))
    asyncio.run(run.scan(client, store))
    assert open_browser.await_count == expected_count
    with sqlite3.connect(store.path) as db:
        rows = db.execute("SELECT state FROM web_task_approval_claims").fetchall()
    assert rows == ([("dispatch_unverified",)] if failure == "uncertain" else [])


@pytest.mark.parametrize("first", ["manual", "browser"])
def test_owned_delete_card_shares_a_single_claim_with_manual_reply(watcher, first):
    _run, _client, store = watcher
    store.create("op_fixture", "gemini_run_task")
    assert store.claim_worker("op_fixture", "old-worker")
    assert store.checkpoint_media("op_fixture", worker_token="old-worker", chat_id="c_fixture",
                                  response_id="r_fixture", candidate_id="rc_fixture")
    owner = approvals.current_account_key()

    def claim_browser():
        return store.claim_web_task_approval(owner, "c_fixture", "r_fixture", "rc_fixture", title_digest="synthetic-hash")

    def claim_manual():
        return store.begin_task_input("op_fixture", "input_fixture", response_id="r_fixture", candidate_id="rc_fixture",
                                      worker_token="new-worker")

    if first == "manual":
        assert claim_manual() and not claim_browser()
    else:
        assert claim_browser() and not claim_manual()


@pytest.mark.parametrize("enabled", [False, True])
def test_web_watcher_is_started_and_stopped_by_server_lifespan(watcher, monkeypatch, enabled):
    from src import client_wrapper
    from src.services import background

    monkeypatch.setenv("GEMINI_TASK_WEB_APPROVALS", str(enabled).lower())
    started, cancelled = [], []

    async def watch():
        started.append(True)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(approvals, "watch_web_task_approvals", watch)
    monkeypatch.setattr(background, "maintain_background_operations", AsyncMock())
    monkeypatch.setattr(background, "repair_operation_cleanup", lambda *args: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(client_wrapper, "_lifecycle_service", SimpleNamespace(reset_all=AsyncMock()))
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))

    async def serve():
        async with client_wrapper.server_lifespan(None):
            await asyncio.sleep(0)

    asyncio.run(serve())
    assert bool(started) is enabled and bool(cancelled) is enabled


@pytest.mark.parametrize("register", [web_tasks.register_web_task_reads, web_tasks.register_web_task_tools])
def test_task_progress_surfaces_native_delete_approval_even_without_activity(register, monkeypatch):
    latest = AsyncMock(return_value=_turn(kind="delete"))
    service = TaskProgressService(AsyncMock(), AsyncMock(), latest, lambda: "synthetic-owner")
    monkeypatch.setattr(web_tasks, "_progress_service", lambda *args: service)
    mcp = MCPServer("web-approval-progress")
    register(mcp)

    async def check():
        tools = await mcp.list_tools()
        assert [tool.name for tool in tools] == ["gemini_tasks"]
        assert tools[0].annotations.read_only_hint is (register is web_tasks.register_web_task_reads)
        return await mcp.call_tool("gemini_tasks", {"action": "progress", "chat_id": "c_fixture"})

    result = asyncio.run(check())
    content = result.content[0]
    assert "🚩 Delete a Google Tasks item" in content.text
    assert content.meta["domain_result"]["data"]["pending_approval"] == {
        "action": "Delete a Google Tasks item", "kind": "google_tasks_delete",
        "state": "native_review_required", "url": "https://gemini.google.com/app/fixture",
    }
