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


def test_preflight_calls_real_offline_text_tool_over_stdio(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "must-not-be-read")
    command = Path(sys.executable).parent / "gemini-mcp-server"

    payload = asyncio.run(run_preflight(server_command=str(command)))

    assert payload["status"] == "ok"
    assert payload["mode"] == "offline"
    assert payload["credentials_accessed"] is False
    assert payload["text_tool"] == "gemini_get_tool_manifest"
    assert payload["profile"] == "model"
    assert payload["enabled_tools"] > 0


def test_offline_environment_strips_credentials_and_forces_safe_profile(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "private")
    monkeypatch.setenv("GEMINI_PSIDTS", "private")
    monkeypatch.setenv("GEMINI_PSIDCC", "private")
    monkeypatch.setenv("GEMINI_TOOLS", "all")

    environment = _server_environment(profile="model", allow_live_account=False)

    assert all(name not in environment for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"))
    assert environment["GEMINI_TOOLS"] == "model"
    assert environment["GEMINI_AUTO_REFRESH"] == "false"


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
