"""Public installation and onboarding contract tests."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest
from mcp_types import CallToolResult, TextContent

from src.domain import ArtifactKind, ArtifactResultData, ArtifactState
from src.onboarding import (
    OnboardingError,
    _build_parser,
    _server_environment,
    domain_result_from_call,
    run_preflight,
    verify_local_image_artifacts,
)
from src.services import artifact_from_local_path, artifact_result


def test_preflight_calls_real_offline_text_tool_over_stdio(monkeypatch, tmp_path):
    import src.onboarding as onboarding

    monkeypatch.setenv("GEMINI_PSID", "must-not-be-read")
    user_operations = tmp_path / "user-operations.sqlite3"
    user_cleanup = tmp_path / "user-cleanup.sqlite3"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(user_operations))
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(user_cleanup))
    seen = []
    parameters = onboarding.StdioServerParameters
    def capture(**kwargs):
        seen.append(kwargs["env"])
        return parameters(**kwargs)
    monkeypatch.setattr(onboarding, "StdioServerParameters", capture)
    command = Path(sys.executable).parent / "gemini-mcp-function"

    payload = asyncio.run(run_preflight(server_command=str(command)))

    assert payload["status"] == "ok"
    assert payload["mode"] == "offline"
    assert payload["credentials_accessed"] is False
    assert payload["text_tool"] == "gemini_ask"
    assert payload["profile"] == "function"
    assert payload["enabled_tools"] == 17
    assert seen[0]["GEMINI_OPERATION_DB_PATH"] != str(user_operations)
    assert seen[0]["GEMINI_CLEANUP_DB_PATH"] != str(user_cleanup)
    assert not user_operations.exists() and not user_cleanup.exists()


def test_offline_environment_strips_credentials_and_forces_safe_profile(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "private")
    monkeypatch.setenv("GEMINI_PSIDTS", "private")
    monkeypatch.setenv("GEMINI_PSIDCC", "private")
    monkeypatch.setenv("GEMINI_TOOLS", "all")

    environment = _server_environment(profile="model", allow_live_account=False)

    assert all(name not in environment for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"))
    assert environment["GEMINI_TOOLS"] == "model"
    assert environment["GEMINI_AUTO_REFRESH"] == "false"
    assert environment["GEMINI_BROWSER_AUTO_LOAD"] == "false"


def test_domain_result_extraction_supports_wire_structured_content():
    payload = {"ok": True, "data": {"state": "local"}, "error": None, "warnings": [], "meta": {}}
    result = CallToolResult(
        content=[TextContent(type="text", text="done")],
        structuredContent={"result": [{"type": "text", "text": "done", "_meta": {"domain_result": payload}}]},
    )

    assert domain_result_from_call(result) == payload


def test_image_example_requires_verified_file_mime_size_and_dimensions(tmp_path):
    path = tmp_path / "verified.png"
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABAQMAAADO7O3JAAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAAGUExURf8AAP///0EdNBEAAAABYktHRAH/Ai3eAAAAB3RJTUUH6ggDByA1n7aAbwAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII="
        )
    )
    artifact = artifact_from_local_path(
        ArtifactKind.IMAGE,
        str(path),
        requested_backend="flash",
        request_model="gemini-3-flash",
        effective_backend="Nano Banana 2",
        observed_backend="observed-image-generator",
    )
    domain = artifact_result(
        ArtifactResultData(
            state=ArtifactState.LOCAL,
            artifacts=(artifact,),
            requested_model="flash",
            request_model="gemini-3-flash",
            effective_backend="Nano Banana 2",
            observed_backend="observed-image-generator",
            media_type="image",
        )
    ).to_dict()

    verified = verify_local_image_artifacts(domain, output_dir=tmp_path)

    assert verified == [
        {
            "id": artifact.id,
            "local_path": str(path.resolve()),
            "mime_type": "image/png",
            "size_bytes": path.stat().st_size,
            "width": 2,
            "height": 1,
            "verification": "verified",
        }
    ]
    assert domain["data"]["effective_backend"] == "Nano Banana 2"
    assert domain["data"]["observed_backend"] == "observed-image-generator"
    domain["data"]["artifacts"][0]["mime_type"] = "image/jpeg"
    with pytest.raises(OnboardingError, match="content does not match"):
        verify_local_image_artifacts(domain, output_dir=tmp_path)


def test_image_example_rejects_remote_only_or_unverified_artifacts(tmp_path):
    remote_only = {
        "ok": True,
        "data": {
            "state": "remote",
            "artifacts": [
                {
                    "kind": "image",
                    "state": "remote",
                    "uri": "https://cdn.example.test/image.png",
                    "verification": {"status": "unverified"},
                }
            ],
        },
    }

    with pytest.raises(OnboardingError, match="remote URI or response text alone is not enough"):
        verify_local_image_artifacts(remote_only, output_dir=tmp_path)


def test_live_examples_require_explicit_account_gate():
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["chat", "--prompt", "hello"])
    with pytest.raises(SystemExit):
        parser.parse_args(["image", "--prompt", "hello", "--output-dir", "artifacts"])


def test_image_onboarding_polls_same_background_handle_and_verifies_file(monkeypatch, tmp_path):
    from src import onboarding

    image = tmp_path / "result.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABAQMAAADO7O3JAAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAAGUExURf8AAP///0EdNBEAAAABYktHRAH/Ai3eAAAAB3RJTUUH6ggDByA1n7aAbwAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII="
    ))
    artifact = artifact_from_local_path(ArtifactKind.IMAGE, str(image), requested_backend="flash")
    artifact_data = artifact_result(ArtifactResultData(state=ArtifactState.LOCAL, artifacts=(artifact,))).to_dict()["data"]
    calls = []

    async def fake_call(name, arguments, **_kwargs):
        calls.append((name, dict(arguments)))
        if name == "gemini_generate_image":
            data = {"state": "accepted", "operation_id": "op_" + "a" * 32}
        elif len(calls) == 2:
            data = {"state": "running", "operation_id": arguments["operation_id"]}
        else:
            data = {**artifact_data, "state": "completed", "operation_id": arguments["operation_id"]}
        return CallToolResult(content=[TextContent(type="text", text="ok", _meta={"domain_result": {"ok": True, "data": data}})]), "v", "server"

    async def no_wait(_seconds):
        pass

    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    @asynccontextmanager
    async def fake_client(**_kwargs):
        async def call(name, arguments):
            result, _, _ = await fake_call(name, arguments)
            return result
        yield SimpleNamespace(call_tool=call, protocol_version="v", server_info=SimpleNamespace(version="server"))

    monkeypatch.setattr(onboarding, "_installed_client", fake_client)
    monkeypatch.setattr(onboarding.secrets, "token_hex", lambda _n: "a" * 32)
    monkeypatch.setattr(onboarding.asyncio, "sleep", no_wait)
    result = asyncio.run(onboarding.run_image("prompt", output_dir=tmp_path, model="flash", filename=None))
    assert result["artifacts"][0]["local_path"] == str(image.resolve())
    assert [name for name, _ in calls] == ["gemini_generate_image", "gemini_get_operation_result", "gemini_get_operation_result"]
    assert calls[1][1] == calls[2][1] == {"operation_id": result["operation_id"]}


def test_image_onboarding_never_resubmits_failed_background_operation(monkeypatch, tmp_path):
    from src import onboarding

    calls = []
    operation_id = "op_" + "b" * 32

    async def fake_call(name, arguments, **_kwargs):
        calls.append(name)
        data = {"operation_id": operation_id, "state": "accepted" if len(calls) == 1 else "failed"}
        return CallToolResult(content=[TextContent(type="text", text="ok", _meta={"domain_result": {"ok": True, "data": data}})]), "v", "server"

    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    @asynccontextmanager
    async def fake_client(**_kwargs):
        async def call(name, arguments):
            result, _, _ = await fake_call(name, arguments)
            return result
        yield SimpleNamespace(call_tool=call, protocol_version="v", server_info=SimpleNamespace(version="server"))

    monkeypatch.setattr(onboarding, "_installed_client", fake_client)
    monkeypatch.setattr(onboarding.secrets, "token_hex", lambda _n: "b" * 32)
    with pytest.raises(OnboardingError, match=operation_id):
        asyncio.run(onboarding.run_image("prompt", output_dir=tmp_path, model="flash", filename=None))
    assert calls == ["gemini_generate_image", "gemini_get_operation_result"]
