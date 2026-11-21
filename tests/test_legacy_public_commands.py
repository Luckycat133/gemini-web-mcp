"""Real stdio regression against the two released main entrypoints (ec12c1a5)."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters, stdio_client

_EXPECTED = json.loads(
    (Path(__file__).parent / "fixtures" / "released_ec12c1a5_account_create_contract.json").read_text(encoding="utf-8")
)


def _fingerprint(tool):
    wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    stable = {key: wire[key] for key in ("inputSchema", "outputSchema", "annotations") if key in wire}
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("command", sorted(_EXPECTED))
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_installed_legacy_commands_preserve_wire_and_safe_calls(tmp_path, command, mode):
    async def round_trip():
        # Explicit environment; no account, browser profile, key or user ledger.
        env = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TMPDIR", "TEMP", "TMP")
            if name in os.environ
        }
        env.update({
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "GEMINI_AUTO_REFRESH": "false",
            "GEMINI_BROWSER_AUTO_LOAD": "false",
            "GEMINI_PSID": "",
            "GEMINI_PSIDTS": "",
            "GEMINI_PSIDCC": "",
            "GEMINI_API_KEY": "",
            "GEMINI_STATE_DB_PATH": str(tmp_path / "released.sqlite3"),
        })
        # Stdio transports may inherit the parent HOME or PYTHONPATH defaults;
        # the explicit values keep resources isolated.
        env["PYTHONPATH"] = ""
        bin_dir = Path(sys.executable).parent
        suffix = ".exe" if os.name == "nt" else ""
        params = StdioServerParameters(command=str(bin_dir / (command + suffix)), env=env, cwd=tmp_path)
        async with asyncio.timeout(30):
            async with Client(stdio_client(params), mode=mode, cache=None) as client:
                listing = (await client.list_tools()).tools
                assert {t.name: _fingerprint(t) for t in listing} == _EXPECTED[command]
                if command == "gemini-mcp-account":
                    response = await client.call_tool("gemini_prompts", {"request": {"action": "list", "limit": 2}})
                    assert response.structured_content["ok"] is True
                else:
                    response = await client.call_tool("gemini_get_operation_status", {"operation_id": "op_synthetic"})
                    assert response.structured_content["ok"] is False
                    assert response.structured_content["error"]["code"] == "AUTH_REQUIRED"
                assert not response.is_error
    asyncio.run(round_trip())


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="receipt path requires private POSIX files")
def test_actual_released_store_is_read_only_to_the_next_function(tmp_path, monkeypatch):
    from src.infrastructure.operation_store import OperationStore
    from src.services.operations import OperationService
    from src_legacy.domain import OperationState
    from src_legacy.infrastructure.state_store import OperationRecord, StateStore

    path = tmp_path / "released.sqlite3"
    handle = "op_" + "e" * 32
    same_account = "test-only-no-gemini-credential"
    monkeypatch.setenv("GEMINI_STATE_DB_PATH", str(path))
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "candidate.sqlite3"))
    monkeypatch.setenv("GEMINI_PSID", same_account)
    legacy = StateStore(path, clock=lambda: 100)
    scope = legacy.scope_for(same_account)
    assert scope is not None
    legacy.operations.create(OperationRecord(handle, scope, "video", OperationState.ACCEPTED, 100, 100, 1000))
    key_path = path.with_suffix(".sqlite3.key")
    original = (path.read_bytes(), key_path.read_bytes())

    def forbidden(*args, **kwargs):
        pytest.fail("Receipt must not initialize Gemini or mutate the retained upstream source")

    svc = OperationService(OperationStore(clock=lambda: 110), forbidden, forbidden, forbidden)
    receipt = asyncio.run(svc.get_status(handle))
    assert receipt.ok and receipt.meta.verification_status == "released_media_receipt_only"
    assert receipt.data.operation_id == handle and not receipt.data.continuation_possible
    monkeypatch.setenv("GEMINI_PSID", "foreign-synthetic-account")
    assert asyncio.run(svc.get_result(handle)).meta.verification_status == "operation_not_found"
    assert (path.read_bytes(), key_path.read_bytes()) == original
