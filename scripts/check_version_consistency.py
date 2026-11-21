"""Validate every non-historical release version consumer."""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag to compare with pyproject.toml (for example v0.2.1)")
    parser.add_argument("--artifacts-dir", type=Path, help="Also validate built wheel/sdist/skill asset names")
    parser.add_argument(
        "--skip-python-assets",
        action="store_true",
        help="With --artifacts-dir, require only the standalone skill zip",
    )
    args = parser.parse_args()

    try:
        metadata = load_release_metadata(PROJECT_ROOT)
        require_repository_version_consistency(PROJECT_ROOT, metadata)
        tag = args.tag or release_tag_from_environment()
        if tag:
            validate_release_tag(tag, metadata)
        if args.artifacts_dir is not None:
            outdir = args.artifacts_dir
            if not outdir.is_absolute():
                outdir = PROJECT_ROOT / outdir
            require_release_artifacts(outdir, metadata, require_python=not args.skip_python_assets)
    except ReleaseMetadataError as exc:
        raise SystemExit(str(exc)) from exc

    suffix = f", tag {tag}" if tag else ""
    print(f"Version consistency OK: {metadata.project_name} {metadata.version}{suffix}")


if __name__ == "__main__":
    main()
