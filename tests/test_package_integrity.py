from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.dependency_contract import (
    dependency_contract_errors,
    discover_runtime_imports,
)
from scripts.smoke_installed_wheel import EXPECTED_ENTRY_POINTS
from src import skill_server
from src.resources import default_prompts_resource, read_default_prompts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_runtime_imports_have_direct_or_extra_dependency_intent():
    assert dependency_contract_errors(PROJECT_ROOT) == []
    assert discover_runtime_imports(PROJECT_ROOT / "src") == {
        "PIL",
        "browser_cookie3",
        "gemini_webapi",
        "mcp",
        "mcp_types",
        "orjson",
    }


def test_dependency_contract_detects_new_transitive_import(tmp_path):
    shutil.copy(PROJECT_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "unexpected.py").write_text("import requests\n", encoding="utf-8")

    assert "src imports undeclared third-party module 'requests'" in dependency_contract_errors(tmp_path)


def test_default_prompts_are_package_internal_and_readable():
    resource = default_prompts_resource()
    payload = json.loads(read_default_prompts())

    assert resource.is_file()
    assert len(payload["prompts"]) >= 8
    assert not (PROJECT_ROOT / "prompts_default.json").exists()


def test_compact_server_initializes_defaults_from_package_data(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    prompts_file = config_dir / "prompts.json"
    monkeypatch.setattr(skill_server, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", prompts_file)

    skill_server._init_default_prompts()

    assert json.loads(prompts_file.read_text(encoding="utf-8")) == json.loads(read_default_prompts())


def test_package_data_and_all_console_entrypoints_are_explicit():
    metadata = _pyproject()
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert metadata["project"]["scripts"] == EXPECTED_ENTRY_POINTS
    assert "data/*.json" in metadata["tool"]["setuptools"]["package-data"]["src"]
    assert "recursive-include src/data *.json" in manifest
    assert "recursive-include scripts *.py" in manifest
    assert "recursive-include compatibility *.json" in manifest
    assert "recursive-include examples *.json *.toml" in manifest
    assert (PROJECT_ROOT / "src" / "onboarding.py").is_file()


def test_dependency_checker_cli_passes():
    completed = subprocess.run(
        [sys.executable, "scripts/check_dependency_contract.py"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "every runtime third-party import is direct or extra-gated" in completed.stdout
