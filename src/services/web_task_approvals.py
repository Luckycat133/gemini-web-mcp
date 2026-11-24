"""Observe native approval cards on website Tasks without taking cleanup ownership."""

import asyncio
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import logging
import os
import re
from typing import Any
from urllib.parse import quote

from ..domain.task_progress import TaskPendingApproval
from ..domain.web_tasks import TASK_STATUS_INPUT_REQUIRED
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.operation_store import OperationStore
from ..infrastructure.task_confirmation import (
    native_confirmation_card,
    one_time_browser_option,
    parse_confirmation_replies,
)
from ..infrastructure.task_approval_browser import run_task_approval_browser
from ..infrastructure.task_setup import parse_app_setup_card
from .deadlines import await_before_deadline
from .model_policy import model_policy_scope
from .task_inputs import task_auto_approve_enabled
from .web_context_transport import fetch_task_metadata_page, fetch_task_progress_turn

logger = logging.getLogger(__name__)
_TASK_CHAT_ID = re.compile(r"c_[A-Za-z0-9_-]{1,128}")
_DELETE_RULES_ENV = "GEMINI_TASK_GOOGLE_TASKS_DELETE_RULES"


def web_task_approvals_enabled() -> bool:
    return (task_auto_approve_enabled()
            and os.environ.get("GEMINI_TASK_WEB_APPROVALS", "").strip().lower() == "true")


@dataclass(frozen=True)
class PendingWebTaskCard:
    chat_id: str
    response_id: str
    candidate_id: str
    confirmation: dict[str, Any]
    summary: TaskPendingApproval
    delete_title: str | None = None


def _delete_rules() -> frozenset[tuple[str, str]]:
    raw = os.environ.get(_DELETE_RULES_ENV, "")
    if not raw or len(raw) > 65_536:
        return frozenset()
    try:
        rules = json.loads(raw)
    except (ValueError, RecursionError):
        return frozenset()
    if not isinstance(rules, list) or len(rules) > 100:
        return frozenset()
    configured: set[tuple[str, str]] = set()
    for item in rules:
        if not isinstance(item, dict) or set(item) != {"task_id", "title"}:
            return frozenset()
        chat_id, title = item["task_id"], item["title"]
        if (not isinstance(chat_id, str) or _TASK_CHAT_ID.fullmatch(chat_id) is None
                or not isinstance(title, str) or not 1 <= len(title) <= 300 or title.strip() != title
                or any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in title)):
            return frozenset()
        configured.add((chat_id, title))
    return frozenset(configured)


def _delete_title(native: list[Any], candidate: list[Any], confirmation: dict[str, Any]) -> str | None:
    """Require the observed single-item Google Tasks card and its matching reply text."""
    if len(native) <= 11 or native[2] != "要允许 Gemini **删除任务**吗？":
        return None
    card = confirmation["input_card"]
    options = confirmation["input_options"]
    if (card.get("unsupported") or card.get("requires_option_id") is not False
            or [(option["label"], option["prompt"]) for option in options] != [("允许", "允许"), ("不允许", "不允许")]
            or [reply["action_path"] for reply in card["replies"]] != [[6, 0], [8, 0]]):
        return None
    details = native[11]
    if not isinstance(details, list) or len(details) != 1:
        return None
    item = details[0]
    if (not isinstance(item, list) or len(item) != 4 or item[:2] != [None, None] or item[3] is not True
            or not isinstance(item[2], str) or not item[2].startswith("**") or not item[2].endswith("**")):
        return None
    title = item[2][2:-2]
    text = candidate[1]
    if not title or not isinstance(text, list) or not text or not isinstance(text[0], str):
        return None
    first_line, _, _ = text[0].strip().partition("\n")
    return title if first_line.strip() == f"确定要删除“{title}”吗？" else None


def _browser_option(confirmation: dict[str, Any]) -> dict[str, str] | None:
    option = one_time_browser_option(confirmation)
    if (option is not None and confirmation["input_card"]["requires_option_id"] is False
            and option["prompt"].strip().casefold() in {"allow", "允许", "允許"}):
        # Bare approvals are also used by app mutations in the same channel.
        return None
    return option


def pending_web_task_card(payload: Any) -> PendingWebTaskCard | None:
    if (not isinstance(payload, dict) or payload.get("latest_verified") is not True
            or type(payload.get("upstream_status")) is not int
            or payload["upstream_status"] != TASK_STATUS_INPUT_REQUIRED):
        return None
    chat_id, rid, candidate = (payload.get(key) for key in ("chat_id", "response_id", "candidate"))
    if (not isinstance(chat_id, str) or not _TASK_CHAT_ID.fullmatch(chat_id)
            or not isinstance(rid, str) or not rid.startswith("r_")
            or not isinstance(candidate, list) or len(candidate) < 13 or not isinstance(candidate[0], str)
            or not candidate[0].startswith("rc_")):
        return None
    state = candidate[8]
    if not isinstance(state, list) or not state or type(state[0]) is not int or state[0] != 2:
        return None
    if parse_app_setup_card(candidate)["setup_card_present"]:
        return None
    native = native_confirmation_card(candidate)
    if native is None or (len(native) > 14 and native[14] not in (None, "", [])):
        return None
    confirmation = parse_confirmation_replies(native, chat_id=chat_id, response_id=rid, candidate_id=candidate[0])
    url = "https://gemini.google.com/app/" + quote(chat_id.removeprefix("c_"), safe="")
    delete_title = None
    if _browser_option(confirmation) is not None:
        notice = TaskPendingApproval("browser", "Allow browser use for this step", "browser_grant_available", url)
    elif (len(native) > 2 and native[1] == "Google Tasks" and isinstance(native[2], str)
          and re.search(r"删除任务|刪除工作|\bdelete tasks?\b", native[2], flags=re.IGNORECASE)):
        delete_title = _delete_title(native, candidate, confirmation)
        configured = delete_title is not None and (chat_id, delete_title) in _delete_rules()
        state = "browser_approval_configured" if configured and web_task_approvals_enabled() else "native_review_required"
        notice = TaskPendingApproval("google_tasks_delete", "Delete a Google Tasks item", state, url)
    else:
        notice = TaskPendingApproval("confirmation", "Review the native approval card", "native_review_required", url)
    return PendingWebTaskCard(chat_id, rid, candidate[0], confirmation, notice, delete_title)


async def _approve_web_task_browser(client: Any, store: OperationStore, owner: str, card: PendingWebTaskCard) -> None:
    from ..thinking_client import mutation_scope

    chat_id = card.chat_id
    option = _browser_option(card.confirmation)
    if option is None or current_account_key() != owner:
        return
    identity = (chat_id, card.response_id, card.candidate_id)
    # Double-check the latest card before claiming. Older turns and changed
    # button queries are still observable history, but are not executable.
    latest = pending_web_task_card(await fetch_task_progress_turn(client, chat_id))
    if latest != card or not web_task_approvals_enabled() or current_account_key() != owner:
        return
    if not store.claim_web_task_approval(owner, *identity):
        return
    reply = next(item for item in card.confirmation["input_card"]["replies"]
                 if item["input_option_id"] == option["input_option_id"])
    verified = False
    try:
        with (client.task_generation_scope(chat_id, action_choice_id=reply["action_choice_id"]),
              mutation_scope(owner=owner), model_policy_scope("task", allow_fallback=False)):
            if current_account_key() != owner:
                return
            await await_before_deadline(client.generate_content(
                prompt=option["prompt"], model="flash", chat=client.start_chat(metadata=list(identity)),
            ), timeout=60)
        observed = await fetch_task_progress_turn(client, chat_id)
        verified = (current_account_key() == owner and isinstance(observed, dict)
                    and observed.get("chat_id") == chat_id and observed.get("latest_verified") is True
                    and isinstance(observed.get("response_id"), str)
                    and observed.get("response_id") != card.response_id)
    finally:
        # A timeout, cancellation or crash never causes an automatic resend.
        store.finish_web_task_approval(owner, *identity, continuation_verified=verified)


async def _approve_google_tasks_delete(client: Any, store: OperationStore, owner: str, card: PendingWebTaskCard) -> None:
    from ..client_manager import get_extra_cookies

    title = card.delete_title
    if title is None or (card.chat_id, title) not in _delete_rules() or current_account_key() != owner:
        return
    identity = (card.chat_id, card.response_id, card.candidate_id)
    digest = hashlib.sha256(title.encode()).hexdigest()
    if store.has_web_task_delete_claim(owner, card.chat_id, digest):
        return
    cookies = get_extra_cookies()
    browser_psid = cookies.get("__Secure-1PSID")
    if not browser_psid or browser_psid != os.environ.get("GEMINI_PSID"):
        return
    claimed = False
    verified = False

    async def before_execute() -> bool:
        nonlocal claimed
        latest = pending_web_task_card(await fetch_task_progress_turn(client, card.chat_id))
        if (latest != card or current_account_key() != owner or not web_task_approvals_enabled()
                or os.environ.get("GEMINI_PSID") != browser_psid
                or (card.chat_id, title) not in _delete_rules()):
            return False
        claimed = store.claim_web_task_approval(owner, *identity, title_digest=digest)
        return claimed

    try:
        browser_script = files("src").joinpath("data", "task_delete_approval.js").read_text(encoding="utf-8")
        outcome = await run_task_approval_browser(
            card.summary.url, cookies, browser_script,
            {"taskId": card.chat_id.removeprefix("c_"), "responseId": card.response_id, "title": title},
            before_execute=before_execute, timeout=60,
        )
        status = outcome.get("status")
        if claimed and status in ("not_matched", "skipped"):
            store.release_unmatched_web_task_delete(owner, *identity, title_digest=digest)
            claimed = False
            return
        if not claimed or status != "activated":
            return
        # Only Task continuation is observed here. The connected app owns the deletion result.
        for delay in (0, 1, 3):
            if delay:
                await asyncio.sleep(delay)
            if current_account_key() != owner:
                return
            latest_turn = await fetch_task_progress_turn(client, card.chat_id)
            if (current_account_key() == owner and isinstance(latest_turn, dict) and latest_turn.get("chat_id") == card.chat_id
                    and latest_turn.get("latest_verified") is True and isinstance(latest_turn.get("response_id"), str)
                    and latest_turn["response_id"] != card.response_id):
                verified = True
                break
    finally:
        if claimed:
            store.finish_web_task_approval(owner, *identity, continuation_verified=verified)


class WebTaskApprovalWatcher:
    """Bounded Task discovery, with one page per maintenance pass."""

    def __init__(self) -> None:
        self.owner: str | None = None
        self.page_token: str | None = None

    async def scan(self, client: Any, store: OperationStore) -> None:
        owner = current_account_key()
        if not owner or not web_task_approvals_enabled():
            return
        if owner != self.owner:
            self.owner, self.page_token = owner, None
        page = await fetch_task_metadata_page(client, 20, self.page_token)
        if current_account_key() != owner:
            return
        token = page["next_page_token"]
        self.page_token = token if token != self.page_token else None
        configured_chats = {chat_id for chat_id, _ in _delete_rules()}
        for task in page["items"]:
            if not web_task_approvals_enabled() or current_account_key() != owner:
                break
            chat_id = task["chat_id"]
            if task.get("upstream_status") != TASK_STATUS_INPUT_REQUIRED:
                continue
            owned = store.has_owned_task_chat(owner, chat_id)
            if owned and chat_id not in configured_chats:
                continue
            try:
                card = pending_web_task_card(await fetch_task_progress_turn(client, chat_id))
                if card is None or card.chat_id != chat_id or current_account_key() != owner:
                    continue
                if card.summary.kind == "google_tasks_delete" and chat_id in configured_chats:
                    await _approve_google_tasks_delete(client, store, owner, card)
                elif not owned:
                    await _approve_web_task_browser(client, store, owner, card)
            except Exception as error:
                logger.warning("Web Task approval remains unverified error_type=%s", type(error).__name__)


async def watch_web_task_approvals() -> None:
    from ..client_wrapper import get_gemini_client, initialize_client

    watcher = WebTaskApprovalWatcher()
    while web_task_approvals_enabled():
        try:
            await await_before_deadline(initialize_client(), timeout=60)
            await watcher.scan(get_gemini_client(), OperationStore())
        except Exception as error:
            logger.warning("Web Task approval scan unavailable error_type=%s", type(error).__name__)
        await asyncio.sleep(15)
