"""Zero-context-tax companion CLI for Gemini Web account and workspace management."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout, suppress
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from mcp import Client
from mcp_types import CallToolResult

from . import __version__
from .client_wrapper import init_startup_infrastructure, reset_client_async
from .onboarding import domain_result_from_call
from .services.doctor import doctor_payload
from .surfaces.account import build_server


def load_mcp_env_defaults(config_path: Path | None = None) -> None:
    """Populate unset GEMINI_* defaults from Antigravity/Claude mcp_config.json."""
    if os.environ.get("GEMINI_WEB_CLI_NO_CONFIG") == "1":
        return
    path = config_path or (Path.home() / ".gemini/config/mcp_config.json")
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return
    for server_key in ("gemini-web-mcp", "gemini-function", "gemini-account"):
        entry = servers.get(server_key)
        env_map = entry.get("env") if isinstance(entry, dict) else None
        if not isinstance(env_map, dict):
            continue
        for key, value in env_map.items():
            if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
                os.environ.setdefault(key, str(value).lower() if isinstance(value, bool) else str(value))
        break


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false boolean, got: {value!r}")


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("Expected a JSON object.")
    return parsed


_CLI_SESSION_TTL_SECONDS = 900.0


def _session_cache_path() -> Path:
    base = Path(os.environ.get("GEMINI_COOKIE_PATH") or (Path.home() / ".local/state/gemini-web-mcp/cookies")).expanduser()
    return base / ".cli_browser_session.json"


def _restore_cli_browser_session() -> dict[str, Any] | None:
    if os.environ.get("GEMINI_WEB_CLI_NO_CONFIG") == "1" or os.environ.get("GEMINI_PSID"):
        return None
    cache_path = _session_cache_path()
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    verified_at = payload.get("verified_at")
    cookies = payload.get("cookies")
    source = payload.get("source")
    if (
        not isinstance(verified_at, (int, float))
        or time.time() - float(verified_at) >= _CLI_SESSION_TTL_SECONDS
        or not isinstance(cookies, dict)
        or not isinstance(source, str)
    ):
        return None
    psid = cookies.get("__Secure-1PSID")
    if not isinstance(psid, str) or not psid:
        return None
    psidts = cookies.get("__Secure-1PSIDTS")
    os.environ["GEMINI_PSID"] = psid
    if isinstance(psidts, str) and psidts:
        os.environ["GEMINI_PSIDTS"] = psidts
    return {"psid": psid, "psidts": psidts if isinstance(psidts, str) else "", "source": source, "cookies": cookies}


def _apply_restored_cookie_manager(restored: dict[str, Any] | None) -> None:
    if not restored:
        return
    try:
        from .cookie_manager import get_cookie_manager
        get_cookie_manager().update_cookie(
            restored["psid"],
            restored["psidts"],
            source=restored["source"],
            extra_cookies=restored["cookies"],
            notify=False,
        )
    except Exception:
        pass


def _save_cli_browser_session() -> None:
    if os.environ.get("GEMINI_WEB_CLI_NO_CONFIG") == "1":
        return
    try:
        from .cookie_manager import get_cookie_manager
        cookie_data = get_cookie_manager().get_cookie()
    except Exception:
        return
    if not cookie_data or not getattr(cookie_data, "psid", None):
        return
    source = str(getattr(cookie_data, "source", "") or "")
    if not source.startswith("browser_"):
        return
    cookies = dict(getattr(cookie_data, "extra_cookies", None) or {})
    cookies.setdefault("__Secure-1PSID", cookie_data.psid)
    if getattr(cookie_data, "psidts", None):
        cookies.setdefault("__Secure-1PSIDTS", cookie_data.psidts)
    cache_path = _session_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"verified_at": time.time(), "source": source, "cookies": cookies},
            ensure_ascii=False,
        )
        fd = os.open(cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError:
        pass


def _extract_domain_payload(result: CallToolResult) -> tuple[bool, Any, list[str], Any]:
    texts = [
        text
        for item in result.content
        if getattr(item, "type", None) == "text" and isinstance(text := getattr(item, "text", None), str)
    ]
    structured: dict[str, Any] | None = None
    try:
        parsed = domain_result_from_call(result)
        if isinstance(parsed, dict):
            structured = parsed
    except Exception:
        structured = None
    raw_sc = getattr(result, "structured_content", None)
    if structured is None and isinstance(raw_sc, dict):
        structured = raw_sc.get("domain_result") if isinstance(raw_sc.get("domain_result"), dict) else raw_sc
    if structured is None:
        for text in reversed(texts):
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and ("ok" in candidate or "items" in candidate or "error" in candidate):
                structured = candidate
                break
    is_err = bool(getattr(result, "is_error", False))
    if structured is not None:
        ok = bool(structured.get("ok", not is_err))
        if is_err:
            ok = False
        error = structured.get("error")
        if set(structured) == {"result"} and isinstance(structured.get("result"), list):
            if texts:
                try:
                    parsed_inner = json.loads(texts[-1])
                except json.JSONDecodeError:
                    parsed_inner = None
                data: Any = parsed_inner if isinstance(parsed_inner, dict) else "\n\n".join(texts)
            else:
                data = None
        else:
            data = structured.get("data", structured if "ok" not in structured else None)
        return ok, data, texts, error
    return not is_err, None, texts, ({"message": "\n".join(texts)} if is_err else None)


async def _call_account_tool(tool_name: str, arguments: dict[str, Any], *, command_label: str) -> dict[str, Any]:
    restored = _restore_cli_browser_session()
    init_startup_infrastructure()
    _apply_restored_cookie_manager(restored)
    server = build_server(full=True)
    clean_args = {key: value for key, value in arguments.items() if value is not None}
    try:
        async with Client(server, mode="auto", cache=None) as client:
            result = await client.call_tool(tool_name, clean_args)
    finally:
        ok, data, texts, error = _extract_domain_payload(result) if "result" in locals() else (False, None, [], None)
        if ok:
            _save_cli_browser_session()
        with suppress(Exception):
            await reset_client_async()
    return {
        "ok": ok,
        "command": command_label,
        "tool": tool_name,
        "data": data,
        "text": "\n\n".join(texts) if texts else None,
        "error": error,
    }


def _dispatch_spec(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    cmd = args.command
    if cmd == "models":
        return "gemini_account_inventory", {
            "surface": "models", "locale": args.locale, "response_format": "json",
        }
    if cmd == "quotas":
        return "gemini_account_inventory", {
            "surface": "usage", "usage_scope": args.scope, "locale": args.locale, "response_format": "json",
        }
    if cmd == "inventory":
        return "gemini_account_inventory", {
            "surface": args.surface,
            "feature_surface": args.feature_surface,
            "limit": args.limit,
            "offset": args.offset,
            "locale": args.locale,
            "response_format": "json",
        }
    if cmd == "public-links":
        return "gemini_account_inventory", {
            "surface": "links", "limit": args.limit, "offset": args.offset, "response_format": "json",
        }
    if cmd == "library":
        return "gemini_account_inventory", {
            "surface": "library", "limit": args.limit, "offset": args.offset, "response_format": "json",
        }
    if cmd == "notebooks":
        sub = args.subcommand
        if sub in {"list", "chats"}:
            return "gemini_notebooks", {
                "action": sub,
                "source": args.source,
                "query": args.query,
                "notebook_id": getattr(args, "notebook_id", "") or "",
                "notebook_title": getattr(args, "notebook_title", "") or "",
                "limit": args.limit,
                "offset": args.offset,
                "locale": args.locale,
                "response_format": "json",
            }
        if sub == "move-chat":
            return "gemini_move_chat_to_notebook", {
                "chat_id": args.chat_id,
                "notebook_id": getattr(args, "notebook_id", "") or "",
                "notebook_title": getattr(args, "notebook_title", "") or "",
                "locale": args.locale,
                "response_format": "json",
            }
        action_map = {"sources": "list_sources", "add-source": "add_source", "delete-source": "delete_source"}
        return "gemini_manage_notebook", {
            "action": action_map.get(sub, sub),
            "notebook_id": getattr(args, "notebook_id", None),
            "source_id": getattr(args, "source_id", None),
            "title": getattr(args, "title", None),
            "text": getattr(args, "text", None),
            "url": getattr(args, "url", None),
            "file_path": getattr(args, "file_path", None),
            "limit": getattr(args, "limit", 20),
            "offset": getattr(args, "offset", 0),
        }
    if cmd == "gems":
        return "gemini_manage_gems", {
            "action": args.subcommand,
            "gem_id": getattr(args, "gem_id", None),
            "name": getattr(args, "name", None),
            "description": getattr(args, "description", None),
            "instructions": getattr(args, "instructions", None),
        }
    if cmd == "skills":
        return "gemini_skills", {
            "action": args.subcommand,
            "skill_id": getattr(args, "skill_id", None),
            "include_instructions": getattr(args, "include_instructions", False),
            "limit": getattr(args, "limit", 20),
            "offset": getattr(args, "offset", 0),
            "max_chars": getattr(args, "max_chars", 4000),
            "title": getattr(args, "title", None),
            "description": getattr(args, "description", None),
            "instructions": getattr(args, "instructions", None),
            "enabled": getattr(args, "enabled", None),
        }
    if cmd == "scheduled":
        sub = args.subcommand
        if sub == "list":
            return "gemini_list_scheduled_actions", {
                "scope": args.scope, "limit": args.limit, "offset": args.offset, "response_format": "json",
            }
        if sub == "get":
            return "gemini_get_scheduled_action", {
                "action_id": args.action_id, "max_chars_per_field": args.max_chars_per_field, "response_format": "json",
            }
        if sub == "create":
            return "gemini_create_scheduled_action", {
                "title": args.title,
                "instructions": args.instructions,
                "hour": args.hour,
                "timezone_name": args.timezone,
                "locale": args.locale,
                "response_format": "json",
            }
        if sub == "delete":
            return "gemini_delete_scheduled_action", {"action_id": args.action_id, "response_format": "json"}
    if cmd == "tasks":
        sub = args.subcommand
        if sub == "browser":
            return "gemini_task_browser", {
                "chat_id": args.chat_id,
                "response_id": args.response_id,
                "candidate_id": args.candidate_id,
                "browser_input": args.browser_input,
                "action_id": args.action_id,
                "expected_frame_sha256": args.expected_frame_sha256,
                "known_frame_sha256": args.known_frame_sha256,
                "include_image": args.include_image,
            }
        sub_action_map = {
            "schedules": "list_schedules",
            "get-schedule": "get_schedule",
            "create-schedule": "create_schedule",
            "update-schedule": "update_schedule",
            "pause-schedule": "pause_schedule",
            "resume-schedule": "resume_schedule",
            "delete-schedule": "delete_schedule",
        }
        return "gemini_tasks", {
            "action": sub_action_map.get(sub, sub),
            "chat_id": getattr(args, "chat_id", None),
            "limit": getattr(args, "limit", 20),
            "page_token": getattr(args, "page_token", None),
            "max_chars": getattr(args, "max_chars", 800),
            "schedule_id": getattr(args, "schedule_id", None),
            "schedule": getattr(args, "schedule", None),
            "offset": getattr(args, "offset", 0),
            "response_id": getattr(args, "response_id", None),
            "candidate_id": getattr(args, "candidate_id", None),
            "title": getattr(args, "title", None),
        }
    if cmd == "history":
        return "gemini_history", {
            "action": args.subcommand,
            "chat_id": getattr(args, "chat_id", ""),
            "query": getattr(args, "query", ""),
            "limit": getattr(args, "limit", 10),
            "offset": getattr(args, "offset", 0),
            "scan_turns": getattr(args, "scan_turns", False),
            "turns_per_chat": getattr(args, "turns_per_chat", 20),
            "max_chars_per_turn": getattr(args, "max_chars_per_turn", 800),
            "include_notebook_chats": not getattr(args, "exclude_notebook_chats", False),
            "include_remy_goals": not getattr(args, "exclude_remy_goals", False),
            "response_format": "json",
        }
    if cmd == "chats":
        if args.subcommand == "delete":
            return "gemini_delete_chat", {"chat_id": args.chat_id}
        return "gemini_manage_chat", {
            "action": args.subcommand,
            "chat_id": args.chat_id,
            "title": getattr(args, "title", None),
            "expected_title": getattr(args, "expected_title", None),
        }
    if cmd == "preferences":
        action_map = {
            "get-browser": "get_browser",
            "set-browser": "set_browser",
            "list-permissions": "list_permissions",
            "set-permission": "set_permission",
        }
        return "gemini_preferences", {
            "action": action_map[args.subcommand],
            "browser": getattr(args, "browser", None),
            "permission": getattr(args, "permission", None),
            "permission_id": getattr(args, "permission_id", None),
            "expected_revision": getattr(args, "expected_revision", None),
            "limit": getattr(args, "limit", 20),
            "offset": getattr(args, "offset", 0),
        }
    if cmd == "cleanup":
        return "gemini_cleanup", {
            "action": args.subcommand,
            "chat_id": getattr(args, "chat_id", None),
            "limit": getattr(args, "limit", 50),
            "offset": getattr(args, "offset", 0),
            "include_completed": getattr(args, "include_completed", False),
        }
    raise ValueError(f"Unsupported command: {cmd}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gemini-web-cli",
        description="Gemini Web account, workspace, notebook, gem, skill, schedule, and cleanup CLI (JSON output).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--compact", action="store_true", help="Emit single-line compact JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Inspect cookie, browser profile, and runtime readiness.")
    p_doc.add_argument("--browser", default="chrome")
    p_doc.add_argument("--validate-browser", action="store_true")

    p_models = sub.add_parser("models", help="List discovered Gemini Web models.")
    p_models.add_argument("--locale", default="zh-CN")

    p_quotas = sub.add_parser("quotas", help="Inspect account usage and model quota status.")
    p_quotas.add_argument("--scope", choices=["all", "quota", "model_state"], default="all")
    p_quotas.add_argument("--locale", default="zh-CN")

    p_inv = sub.add_parser("inventory", help="Read account inventory surface.")
    p_inv.add_argument(
        "--surface",
        choices=["capabilities", "status", "features", "links", "usage", "library", "notebooks", "notebook_chats", "scheduled", "modes", "models"],
        default="capabilities",
    )
    p_inv.add_argument("--feature-surface", default="all")
    p_inv.add_argument("--limit", type=int, default=20)
    p_inv.add_argument("--offset", type=int, default=0)
    p_inv.add_argument("--locale", default="zh-CN")

    p_links = sub.add_parser("public-links", help="List shared Gemini public links.")
    p_links.add_argument("--limit", type=int, default=20)
    p_links.add_argument("--offset", type=int, default=0)

    p_lib = sub.add_parser("library", help="List generated/saved library artifacts index.")
    p_lib.add_argument("--limit", type=int, default=20)
    p_lib.add_argument("--offset", type=int, default=0)

    # Notebooks
    p_nb = sub.add_parser("notebooks", help="List and manage NotebookLM notebooks and Gemini projects.")
    nb_sub = p_nb.add_subparsers(dest="subcommand", required=True)
    p_nb_list = nb_sub.add_parser("list")
    p_nb_list.add_argument("--source", choices=["notebooklm", "gemini"], default="notebooklm")
    p_nb_list.add_argument("--query", default="")
    p_nb_list.add_argument("--limit", type=int, default=20)
    p_nb_list.add_argument("--offset", type=int, default=0)
    p_nb_list.add_argument("--locale", default="zh-CN")

    p_nb_chats = nb_sub.add_parser("chats")
    p_nb_chats.add_argument("--notebook-id", default="")
    p_nb_chats.add_argument("--notebook-title", default="")
    p_nb_chats.add_argument("--source", choices=["notebooklm", "gemini"], default="notebooklm")
    p_nb_chats.add_argument("--limit", type=int, default=20)
    p_nb_chats.add_argument("--offset", type=int, default=0)
    p_nb_chats.add_argument("--locale", default="zh-CN")

    p_nb_create = nb_sub.add_parser("create")
    p_nb_create.add_argument("--title", required=True)

    p_nb_get = nb_sub.add_parser("get")
    p_nb_get.add_argument("--notebook-id", required=True)

    p_nb_sources = nb_sub.add_parser("sources")
    p_nb_sources.add_argument("--notebook-id", required=True)
    p_nb_sources.add_argument("--limit", type=int, default=20)
    p_nb_sources.add_argument("--offset", type=int, default=0)

    p_nb_add = nb_sub.add_parser("add-source")
    p_nb_add.add_argument("--notebook-id", required=True)
    p_nb_add.add_argument("--title")
    p_nb_add.add_argument("--text")
    p_nb_add.add_argument("--url")
    p_nb_add.add_argument("--file-path")

    p_nb_delsrc = nb_sub.add_parser("delete-source")
    p_nb_delsrc.add_argument("--notebook-id", required=True)
    p_nb_delsrc.add_argument("--source-id", required=True)

    p_nb_del = nb_sub.add_parser("delete")
    p_nb_del.add_argument("--notebook-id", required=True)

    p_nb_move = nb_sub.add_parser("move-chat")
    p_nb_move.add_argument("--chat-id", required=True)
    p_nb_move.add_argument("--notebook-id", default="")
    p_nb_move.add_argument("--notebook-title", default="")
    p_nb_move.add_argument("--locale", default="zh-CN")

    # Gems
    p_gems = sub.add_parser("gems", help="List and manage custom Gems.")
    gems_sub = p_gems.add_subparsers(dest="subcommand", required=True)
    gems_sub.add_parser("list")
    p_gc = gems_sub.add_parser("create")
    p_gc.add_argument("--name", required=True)
    p_gc.add_argument("--instructions", required=True)
    p_gc.add_argument("--description")
    p_gu = gems_sub.add_parser("update")
    p_gu.add_argument("--gem-id", required=True)
    p_gu.add_argument("--name")
    p_gu.add_argument("--instructions")
    p_gu.add_argument("--description")
    p_gd = gems_sub.add_parser("delete")
    p_gd.add_argument("--gem-id", required=True)

    # Skills
    p_skills = sub.add_parser("skills", help="List and manage Gemini Skills.")
    sk_sub = p_skills.add_subparsers(dest="subcommand", required=True)
    p_sk_list = sk_sub.add_parser("list")
    p_sk_list.add_argument("--include-instructions", action="store_true")
    p_sk_list.add_argument("--limit", type=int, default=20)
    p_sk_list.add_argument("--offset", type=int, default=0)
    p_sk_list.add_argument("--max-chars", type=int, default=4000)

    p_sk_get = sk_sub.add_parser("get")
    p_sk_get.add_argument("--skill-id", required=True)
    p_sk_get.add_argument("--include-instructions", action="store_true")
    p_sk_get.add_argument("--max-chars", type=int, default=4000)

    p_sk_c = sk_sub.add_parser("create")
    p_sk_c.add_argument("--title", required=True)
    p_sk_c.add_argument("--instructions", required=True)
    p_sk_c.add_argument("--description")

    p_sk_u = sk_sub.add_parser("update")
    p_sk_u.add_argument("--skill-id", required=True)
    p_sk_u.add_argument("--title")
    p_sk_u.add_argument("--instructions")
    p_sk_u.add_argument("--description")

    p_sk_e = sk_sub.add_parser("enable")
    p_sk_e.add_argument("--skill-id", required=True)
    p_sk_e.add_argument("--enabled", type=_parse_bool, required=True)

    p_sk_d = sk_sub.add_parser("delete")
    p_sk_d.add_argument("--skill-id", required=True)

    # Scheduled chat actions
    p_sched = sub.add_parser("scheduled", help="Manage scheduled Chat actions.")
    sc_sub = p_sched.add_subparsers(dest="subcommand", required=True)
    p_sc_l = sc_sub.add_parser("list")
    p_sc_l.add_argument("--scope", choices=["all", "active", "inactive"], default="all")
    p_sc_l.add_argument("--limit", type=int, default=20)
    p_sc_l.add_argument("--offset", type=int, default=0)

    p_sc_g = sc_sub.add_parser("get")
    p_sc_g.add_argument("--action-id", required=True)
    p_sc_g.add_argument("--max-chars-per-field", type=int, default=500)

    p_sc_c = sc_sub.add_parser("create")
    p_sc_c.add_argument("--title", required=True)
    p_sc_c.add_argument("--instructions", required=True)
    p_sc_c.add_argument("--hour", type=int, default=9)
    p_sc_c.add_argument("--timezone", default="Asia/Shanghai")
    p_sc_c.add_argument("--locale", default="zh-CN")

    p_sc_d = sc_sub.add_parser("delete")
    p_sc_d.add_argument("--action-id", required=True)

    # Tasks
    p_tasks = sub.add_parser("tasks", help="Manage Gemini Tasks, schedules, and browser approvals.")
    tk_sub = p_tasks.add_subparsers(dest="subcommand", required=True)
    p_tk_l = tk_sub.add_parser("list")
    p_tk_l.add_argument("--limit", type=int, default=20)
    p_tk_l.add_argument("--page-token")
    p_tk_l.add_argument("--max-chars", type=int, default=800)

    for act in ("get", "progress"):
        p_tka = tk_sub.add_parser(act)
        p_tka.add_argument("--chat-id", required=True)
        p_tka.add_argument("--response-id")
        p_tka.add_argument("--candidate-id")
        p_tka.add_argument("--max-chars", type=int, default=800)

    p_tk_ren = tk_sub.add_parser("rename")
    p_tk_ren.add_argument("--chat-id", required=True)
    p_tk_ren.add_argument("--title", required=True)

    for act in ("pin", "unpin", "delete"):
        p_tkm = tk_sub.add_parser(act)
        p_tkm.add_argument("--chat-id", required=True)

    p_tk_sl = tk_sub.add_parser("schedules")
    p_tk_sl.add_argument("--limit", type=int, default=20)
    p_tk_sl.add_argument("--offset", type=int, default=0)

    for act in ("get-schedule", "pause-schedule", "resume-schedule", "delete-schedule"):
        p_tks = tk_sub.add_parser(act)
        p_tks.add_argument("--schedule-id", required=True)

    p_tk_sc = tk_sub.add_parser("create-schedule")
    p_tk_sc.add_argument("--schedule", type=_parse_json_object, required=True)

    p_tk_su = tk_sub.add_parser("update-schedule")
    p_tk_su.add_argument("--schedule-id", required=True)
    p_tk_su.add_argument("--schedule", type=_parse_json_object, required=True)

    p_tk_br = tk_sub.add_parser("browser")
    p_tk_br.add_argument("--chat-id", required=True)
    p_tk_br.add_argument("--response-id")
    p_tk_br.add_argument("--candidate-id")
    p_tk_br.add_argument("--action-id")
    p_tk_br.add_argument("--expected-frame-sha256")
    p_tk_br.add_argument("--known-frame-sha256")
    p_tk_br.add_argument("--browser-input", type=_parse_json_object)
    p_tk_br.add_argument("--include-image", action="store_true")

    # History
    p_hist = sub.add_parser("history", help="List, search, read, and export conversation history.")
    hi_sub = p_hist.add_subparsers(dest="subcommand", required=True)
    for act in ("list", "scan", "search"):
        p_ha = hi_sub.add_parser(act)
        p_ha.add_argument("--query", default="", required=(act == "search"))
        p_ha.add_argument("--limit", type=int, default=10)
        p_ha.add_argument("--offset", type=int, default=0)
        p_ha.add_argument("--scan-turns", action="store_true")
        p_ha.add_argument("--turns-per-chat", type=int, default=20)
        p_ha.add_argument("--max-chars-per-turn", type=int, default=800)
        p_ha.add_argument("--exclude-notebook-chats", action="store_true")
        p_ha.add_argument("--exclude-remy-goals", action="store_true")

    for act in ("read", "export"):
        p_hr = hi_sub.add_parser(act)
        p_hr.add_argument("--chat-id", required=True)
        p_hr.add_argument("--turns-per-chat", type=int, default=20)
        p_hr.add_argument("--max-chars-per-turn", type=int, default=800)

    # Chats
    p_chats = sub.add_parser("chats", help="Rename, pin, unpin, or delete conversations.")
    ch_sub = p_chats.add_subparsers(dest="subcommand", required=True)
    p_chr = ch_sub.add_parser("rename")
    p_chr.add_argument("--chat-id", required=True)
    p_chr.add_argument("--title", required=True)
    p_chr.add_argument("--expected-title")
    for act in ("pin", "unpin", "delete"):
        p_cha = ch_sub.add_parser(act)
        p_cha.add_argument("--chat-id", required=True)

    # Preferences
    p_prefs = sub.add_parser("preferences", help="Manage Task browser and permission preferences.")
    pr_sub = p_prefs.add_subparsers(dest="subcommand", required=True)
    pr_sub.add_parser("get-browser")
    p_psb = pr_sub.add_parser("set-browser")
    p_psb.add_argument("--browser", choices=["automatic", "remote_only"], required=True)
    p_plp = pr_sub.add_parser("list-permissions")
    p_plp.add_argument("--limit", type=int, default=20)
    p_plp.add_argument("--offset", type=int, default=0)
    p_psp = pr_sub.add_parser("set-permission")
    p_psp.add_argument("--permission-id", required=True)
    p_psp.add_argument("--permission", choices=["always_ask", "always_allow"], required=True)
    p_psp.add_argument("--expected-revision")

    # Cleanup
    p_clean = sub.add_parser("cleanup", help="Inspect, retry, or recheck MCP-owned remote cleanup.")
    cl_sub = p_clean.add_subparsers(dest="subcommand", required=True)
    for act in ("list", "retry", "recheck"):
        p_ca = cl_sub.add_parser(act)
        p_ca.add_argument("--chat-id")
        p_ca.add_argument("--limit", type=int, default=50)
        p_ca.add_argument("--offset", type=int, default=0)
        p_ca.add_argument("--include-completed", action="store_true")

    return parser


def run_cli(argv: list[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(argv)
    load_mcp_env_defaults()
    label = f"{args.command} {args.subcommand}" if getattr(args, "subcommand", None) else args.command
    with redirect_stdout(sys.stderr):
        if args.command == "doctor":
            payload = doctor_payload(browser=args.browser, validate_browser=args.validate_browser)
            return {
                "ok": payload.get("overall_status") != "error",
                "command": label,
                "tool": "gemini_doctor",
                "data": payload,
                "text": None,
                "error": None,
            }
        tool_name, tool_args = _dispatch_spec(args)
        return asyncio.run(_call_account_tool(tool_name, tool_args, command_label=label))


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    compact = bool(args.compact)
    load_mcp_env_defaults()
    label = f"{args.command} {args.subcommand}" if getattr(args, "subcommand", None) else args.command
    try:
        with redirect_stdout(sys.stderr):
            if args.command == "doctor":
                payload = doctor_payload(browser=args.browser, validate_browser=args.validate_browser)
                envelope: dict[str, Any] = {
                    "ok": payload.get("overall_status") != "error",
                    "command": label,
                    "tool": "gemini_doctor",
                    "data": payload,
                    "text": None,
                    "error": None,
                }
            else:
                tool_name, tool_args = _dispatch_spec(args)
                envelope = asyncio.run(_call_account_tool(tool_name, tool_args, command_label=label))
    except Exception as exc:
        envelope = {
            "ok": False,
            "command": label,
            "tool": None,
            "data": None,
            "text": None,
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
    indent = None if compact else 2
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, indent=indent) + "\n")
    sys.stdout.flush()
    raise SystemExit(0 if envelope.get("ok") else 1)


if __name__ == "__main__":
    main()
