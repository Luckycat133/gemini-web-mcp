"""Build Python distributions and the standalone Codex skill zip."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

if __package__:
    from .release_metadata import (
        PROJECT_ROOT,
        ReleaseMetadataError,
        load_release_metadata,
        release_tag_from_environment,
        require_release_artifacts,
        require_repository_version_consistency,
        validate_release_tag,
    )
else:
    from release_metadata import (  # type: ignore[import-not-found,no-redef]
        PROJECT_ROOT,
        ReleaseMetadataError,
        load_release_metadata,
        release_tag_from_environment,
        require_release_artifacts,
        require_repository_version_consistency,
        validate_release_tag,
    )


ROOT = PROJECT_ROOT
SKILL_SOURCE = ROOT / ".agents" / "skills" / "gemini-web-mcp"
SKILL_NAME = "gemini-web-mcp"


def require_skill_files() -> None:
    required = [SKILL_SOURCE / "SKILL.md", SKILL_SOURCE / "agents" / "openai.yaml"]
    missing = [path for path in required if not path.is_file()]
    if missing:
        formatted = ", ".join(str(path.relative_to(ROOT)) for path in missing)
        raise SystemExit(f"Missing required skill file(s): {formatted}")


def build_python_distributions(outdir: Path) -> None:
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(outdir)], cwd=ROOT, check=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "gemini_mcp_server.egg-info", ignore_errors=True)


def build_skill_zip(outdir: Path, version: str) -> Path:
    require_skill_files()
    zip_path = outdir / f"{SKILL_NAME}-skill-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SKILL_SOURCE.rglob("*")):
            if path.is_file():
                relative = path.relative_to(SKILL_SOURCE)
                archive.write(path, f"{SKILL_NAME}/{relative.as_posix()}")

    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="dist", help="Directory for wheel, sdist, and skill zip")
    parser.add_argument("--skip-python", action="store_true", help="Only build the standalone skill zip")
    parser.add_argument("--tag", help="Release tag to validate (defaults to the tag-build environment, if any)")
    args = parser.parse_args()

    outdir = (ROOT / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        metadata = load_release_metadata(ROOT)
        require_repository_version_consistency(ROOT, metadata)
        tag = args.tag or release_tag_from_environment(os.environ)
        if tag:
            validate_release_tag(tag, metadata)
    except ReleaseMetadataError as exc:
        raise SystemExit(str(exc)) from exc

    if not args.skip_python:
        build_python_distributions(outdir)
    skill_zip = build_skill_zip(outdir, metadata.version)

    try:
        require_release_artifacts(outdir, metadata, require_python=not args.skip_python)
    except ReleaseMetadataError as exc:
        raise SystemExit(str(exc)) from exc

    artifacts = sorted(path.name for path in outdir.iterdir() if path.is_file())
    print(f"Built release artifacts in {outdir}:")
    for artifact in artifacts:
        print(f"- {artifact}")
    print(f"Standalone skill zip: {skill_zip}")


if __name__ == "__main__":
    main()
