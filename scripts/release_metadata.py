"""Single-source release metadata and consistency checks.

``pyproject.toml`` is the only persisted source for the project version.  This
module derives runtime-adjacent release names from it and validates both tagged
release references and the evergreen source-install path used for onboarding.
"""

from __future__ import annotations

import os
import re
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ASSET_BASENAME = "gemini-web-mcp-skill"
CANONICAL_GIT_SOURCE = "git+https://github.com/Luckycat133/gemini-web-mcp@main"

_STABLE_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_PRODUCT_VERSION_PATTERNS = (
    re.compile(
        r"Gemini(?: Web)? MCP(?: Server)?[^\n`]*?\(?v(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)\)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:主要功能|智能错误处理|工具组配置)\s*\(v(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
    ),
    re.compile(r"\bv(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*(?:新增|支持)"),
)
_RUNTIME_RELEASE_LITERAL = re.compile(
    r"(?:\b版本\s*:\s*|\(v)(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?)"
)
_WHEEL_URL = re.compile(
    r"https://github\.com/Luckycat133/gemini-web-mcp/releases/download/"
    r"v(?P<tag_version>[^/\s]+)/gemini_mcp_server-(?P<wheel_version>[^/\s]+)-py3-none-any\.whl"
)
_PUBLIC_SOURCE_INSTALL_PATHS = (
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("docs/quickstart.md"),
    Path("docs/launch-kit.md"),
    Path("docs/client-examples.md"),
    Path("examples/clients/codex.config.toml"),
    Path("examples/clients/claude-desktop.json"),
    Path("examples/clients/claude-code.mcp.json"),
    Path("examples/clients/vscode.mcp.json"),
)
_RUNTIME_VERSION_FILES = (
    Path("src/__init__.py"),
    Path("src/server.py"),
    Path("src/skill_server.py"),
)


class ReleaseMetadataError(ValueError):
    """Release metadata or an artifact disagrees with the project version."""


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    """Version-derived names used by packaging and release automation."""

    project_name: str
    version: str
    distribution_basename: str
    tag: str
    wheel_filename: str
    sdist_filename: str
    skill_filename: str


def load_release_metadata(project_root: Path = PROJECT_ROOT) -> ReleaseMetadata:
    """Read the authoritative project name and version from ``pyproject.toml``."""

    with (project_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    name = str(project["name"])
    version = str(project["version"])
    if _STABLE_SEMVER.fullmatch(version) is None:
        raise ReleaseMetadataError(f"Project version must be stable SemVer (X.Y.Z), got {version!r}")

    distribution_basename = re.sub(r"[-_.]+", "_", name)
    return ReleaseMetadata(
        project_name=name,
        version=version,
        distribution_basename=distribution_basename,
        tag=f"v{version}",
        wheel_filename=f"{distribution_basename}-{version}-py3-none-any.whl",
        sdist_filename=f"{distribution_basename}-{version}.tar.gz",
        skill_filename=f"{SKILL_ASSET_BASENAME}-{version}.zip",
    )


def release_tag_from_environment(environment: Mapping[str, str] | None = None) -> str | None:
    """Return a tag only when the environment represents a tag build."""

    values = os.environ if environment is None else environment
    ref = values.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/"):
        return ref.removeprefix("refs/tags/")
    if values.get("GITHUB_REF_TYPE") == "tag":
        return values.get("GITHUB_REF_NAME") or None
    return None


def validate_release_tag(tag: str, metadata: ReleaseMetadata) -> None:
    """Reject a release tag that is not derived from the project version."""

    if tag != metadata.tag:
        raise ReleaseMetadataError(
            f"Release tag {tag!r} does not match pyproject.toml version {metadata.version!r}; "
            f"expected {metadata.tag!r}"
        )


def find_product_version_errors(path: Path, text: str, expected_version: str) -> list[str]:
    """Find stale product-version language while ignoring dependency/schema versions."""

    errors: list[str] = []
    for pattern in _PRODUCT_VERSION_PATTERNS:
        for match in pattern.finditer(text):
            observed = match.group("version")
            if observed != expected_version:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{path.as_posix()}:{line}: product version v{observed} != v{expected_version}")
    return errors


def repository_version_errors(
    project_root: Path = PROJECT_ROOT,
    metadata: ReleaseMetadata | None = None,
) -> list[str]:
    """Return all known repository version drift without mutating the checkout."""

    release = metadata or load_release_metadata(project_root)
    errors: list[str] = []

    markdown_paths = [project_root / "README.md", project_root / "README.zh-CN.md"]
    markdown_paths.extend(sorted((project_root / "docs").rglob("*.md")))
    for absolute_path in markdown_paths:
        relative_path = absolute_path.relative_to(project_root)
        if relative_path == Path("docs/changelog.md") or not absolute_path.is_file():
            continue
        text = absolute_path.read_text(encoding="utf-8")
        errors.extend(find_product_version_errors(relative_path, text, release.version))

    for relative_path in _RUNTIME_VERSION_FILES:
        absolute_path = project_root / relative_path
        if not absolute_path.is_file():
            errors.append(f"{relative_path.as_posix()}: required runtime version consumer is missing")
            continue
        text = absolute_path.read_text(encoding="utf-8")
        for match in _RUNTIME_RELEASE_LITERAL.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{relative_path.as_posix()}:{line}: runtime release literal v{match.group('version')} is forbidden"
            )

    init_path = project_root / "src/__init__.py"
    if init_path.is_file():
        init_source = init_path.read_text(encoding="utf-8")
        if "from importlib.metadata import PackageNotFoundError, version" not in init_source:
            errors.append("src/__init__.py: runtime version must come from importlib.metadata")
        if re.search(r"__version__\s*=\s*['\"]v?[0-9]+\.[0-9]+", init_source):
            errors.append("src/__init__.py: __version__ must not be a persisted release literal")

    for relative_path in (Path("src/server.py"), Path("src/skill_server.py")):
        absolute_path = project_root / relative_path
        if not absolute_path.is_file():
            continue
        source = absolute_path.read_text(encoding="utf-8")
        if "{__version__}" not in source:
            errors.append(f"{relative_path.as_posix()}: banner must interpolate package __version__")

    version_reference_paths = [project_root / "README.md", project_root / "README.zh-CN.md"]
    version_reference_paths.extend(sorted((project_root / "docs").rglob("*.md")))
    version_reference_paths.extend(sorted((project_root / "examples" / "clients").glob("*")))
    for absolute_path in version_reference_paths:
        if not absolute_path.is_file():
            continue
        relative_path = absolute_path.relative_to(project_root)
        if relative_path == Path("docs/changelog.md"):
            continue
        text = absolute_path.read_text(encoding="utf-8")
        for match in _WHEEL_URL.finditer(text):
            tag_version = match.group("tag_version")
            wheel_version = match.group("wheel_version")
            if tag_version != release.version or wheel_version != release.version:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{relative_path.as_posix()}:{line}: wheel URL versions "
                    f"tag={tag_version!r}, wheel={wheel_version!r}, expected={release.version!r}"
                )

    for relative_path in _PUBLIC_SOURCE_INSTALL_PATHS:
        absolute_path = project_root / relative_path
        if not absolute_path.is_file():
            errors.append(f"{relative_path.as_posix()}: required public onboarding surface is missing")
            continue
        text = absolute_path.read_text(encoding="utf-8")
        if CANONICAL_GIT_SOURCE not in text:
            errors.append(
                f"{relative_path.as_posix()}: canonical source install {CANONICAL_GIT_SOURCE!r} is missing"
            )

    return errors


def require_repository_version_consistency(
    project_root: Path = PROJECT_ROOT,
    metadata: ReleaseMetadata | None = None,
) -> None:
    """Raise one actionable error when repository version consumers drift."""

    errors = repository_version_errors(project_root, metadata)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ReleaseMetadataError(f"Version consistency check failed:\n{details}")


def _wheel_metadata_errors(wheel_path: Path, metadata: ReleaseMetadata) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                return [f"{wheel_path.name}: expected one .dist-info/METADATA, found {len(metadata_files)}"]
            message = Parser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        return [f"{wheel_path.name}: cannot read wheel metadata: {exc}"]

    if message.get("Name") != metadata.project_name:
        errors.append(
            f"{wheel_path.name}: wheel Name={message.get('Name')!r}, expected {metadata.project_name!r}"
        )
    if message.get("Version") != metadata.version:
        errors.append(
            f"{wheel_path.name}: wheel Version={message.get('Version')!r}, expected {metadata.version!r}"
        )
    return errors


def release_artifact_errors(
    outdir: Path,
    metadata: ReleaseMetadata,
    *,
    require_python: bool = True,
) -> list[str]:
    """Validate release asset names and the built wheel's internal metadata."""

    expected_all = {
        metadata.wheel_filename,
        metadata.sdist_filename,
        metadata.skill_filename,
    }
    required = {metadata.skill_filename}
    if require_python:
        required.update({metadata.wheel_filename, metadata.sdist_filename})

    files = {path.name: path for path in outdir.iterdir() if path.is_file()} if outdir.is_dir() else {}
    errors = [f"{outdir}: missing release artifact {name}" for name in sorted(required - files.keys())]

    known_asset = re.compile(r"(?:gemini_mcp_server-.+\.(?:whl|tar\.gz)|gemini-web-mcp-skill-.+\.zip)")
    stale_assets = sorted(name for name in files if known_asset.fullmatch(name) and name not in expected_all)
    errors.extend(f"{outdir}: stale or mismatched release artifact {name}" for name in stale_assets)

    wheel_path = files.get(metadata.wheel_filename)
    if wheel_path is not None:
        errors.extend(_wheel_metadata_errors(wheel_path, metadata))
    return errors


def require_release_artifacts(
    outdir: Path,
    metadata: ReleaseMetadata,
    *,
    require_python: bool = True,
) -> None:
    """Raise when release filenames or wheel metadata disagree with pyproject."""

    errors = release_artifact_errors(outdir, metadata, require_python=require_python)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ReleaseMetadataError(f"Release artifact check failed:\n{details}")


def expected_release_artifacts(metadata: ReleaseMetadata) -> Sequence[str]:
    """Return deterministic release asset names for display and tests."""

    return (metadata.wheel_filename, metadata.sdist_filename, metadata.skill_filename)
