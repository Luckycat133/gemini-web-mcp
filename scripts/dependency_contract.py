"""Audit runtime imports against direct and optional project dependencies."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

REQUIRED_IMPORTS = {
    "gemini_webapi": "gemini-webapi",
    "mcp": "mcp",
    "mcp_types": "mcp-types",
    "orjson": "orjson",
    "pydantic": "pydantic",
}


@dataclass(frozen=True, slots=True)
class OptionalImport:
    distribution: str
    extras: frozenset[str]


OPTIONAL_IMPORTS = {
    "browser_cookie3": OptionalImport("browser-cookie3", frozenset({"browser", "all"})),
    "PIL": OptionalImport("pillow", frozenset({"image", "all"})),
}

EXPECTED_REQUIREMENTS = {
    "gemini-webapi": "gemini-webapi>=2.0.0,<3",
    "mcp": "mcp>=2.0.0,<3",
    "mcp-types": "mcp-types>=2.0.0,<3",
    "orjson": "orjson>=3.11.7,<4",
    "pydantic": "pydantic>=2.0.0,<3",
}

EXPECTED_OPTIONAL_REQUIREMENTS = {
    ("browser", "browser-cookie3"): "browser-cookie3>=0.19.0,<1",
    ("image", "pillow"): "pillow>=10.0.0,<13",
    ("all", "browser-cookie3"): "browser-cookie3>=0.19.0,<1",
    ("all", "pillow"): "pillow>=10.0.0,<13",
}


def normalize_distribution_name(name: str) -> str:
    """Normalize a distribution name using the PEP 503 comparison form."""

    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """Extract and normalize the distribution portion of a requirement string."""

    match = _REQUIREMENT_NAME.match(requirement.strip())
    if match is None:
        raise ValueError(f"Invalid requirement: {requirement!r}")
    return normalize_distribution_name(match.group())


def discover_runtime_imports(source_root: Path) -> set[str]:
    """Return non-stdlib top-level imports used anywhere in runtime source."""

    imports: set[str] = set()
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.partition(".")[0])

    local_packages = {source_root.name}
    return imports - set(sys.stdlib_module_names) - local_packages


def dependency_contract_errors(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Return actionable dependency/import mismatches for the checkout."""

    with (project_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    dependencies = {
        requirement_name(requirement): requirement for requirement in project.get("dependencies", [])
    }
    optional_dependencies = {
        extra: {requirement_name(requirement): requirement for requirement in requirements}
        for extra, requirements in project.get("optional-dependencies", {}).items()
    }
    imports = discover_runtime_imports(project_root / "src")
    errors: list[str] = []

    known_imports = set(REQUIRED_IMPORTS) | set(OPTIONAL_IMPORTS)
    for module in sorted(imports - known_imports):
        errors.append(f"src imports undeclared third-party module {module!r}")

    for module, distribution in REQUIRED_IMPORTS.items():
        normalized = normalize_distribution_name(distribution)
        if module in imports and normalized not in dependencies:
            errors.append(f"src import {module!r} requires direct dependency {distribution!r}")

    for distribution, expected in EXPECTED_REQUIREMENTS.items():
        observed = dependencies.get(distribution)
        if observed != expected:
            errors.append(f"dependency {distribution!r} must be {expected!r}, found {observed!r}")

    for module, contract in OPTIONAL_IMPORTS.items():
        if module not in imports:
            continue
        normalized = normalize_distribution_name(contract.distribution)
        for extra in sorted(contract.extras):
            if normalized not in optional_dependencies.get(extra, {}):
                errors.append(
                    f"optional src import {module!r} requires {contract.distribution!r} in extra {extra!r}"
                )

    for (extra, distribution), expected in EXPECTED_OPTIONAL_REQUIREMENTS.items():
        observed = optional_dependencies.get(extra, {}).get(distribution)
        if observed != expected:
            errors.append(
                f"optional dependency {extra!r}/{distribution!r} must be {expected!r}, found {observed!r}"
            )

    return errors


def require_dependency_contract(project_root: Path = PROJECT_ROOT) -> None:
    """Raise one diagnostic error if runtime imports rely on transitive packages."""

    errors = dependency_contract_errors(project_root)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(f"Dependency contract check failed:\n{details}")
