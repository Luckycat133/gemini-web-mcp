"""Install a verified wheel behind stable user-local MCP commands."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("gemini-web-mcp", "gemini-web-cli")
LEGACY_COMMANDS = (
    "gemini-mcp-function",
    "gemini-mcp-account",
    "gemini-mcp-server",
    "gemini-mcp-assist",
    "gemini-mcp-skill-server",
    "gemini-mcp-onboarding",
)


def run(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(args, text=True, cwd=ROOT, env=env)


def install_runtime(uv: str, wheel: Path, releases: Path, *, offline: bool) -> tuple[Path, str]:
    with wheel.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    runtime = releases / digest[:16]
    verified = runtime / "wheel.sha256"
    if verified.is_file() and verified.read_text().strip() == digest:
        return runtime, digest
    if runtime.exists():
        raise RuntimeError(f"Incomplete runtime exists at {runtime}")

    options = ("--offline",) if offline else ()
    try:
        run(uv, "venv", *options, "--python", sys.executable, str(runtime))
        run(uv, "pip", "install", *options, "--python", str(runtime / "bin/python"), f"{wheel}[all]")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GEMINI_") and key != "PYTHONPATH"}
        env.update(GEMINI_BROWSER_AUTO_LOAD="false", GEMINI_AUTO_REFRESH="false")
        entrypoints = runtime / "bin"
        with tempfile.TemporaryDirectory(prefix="gemini-update-preflight-") as scratch:
            env["GEMINI_COOKIE_PATH"] = str(Path(scratch) / "cookies")
            env["PATH"] = str(entrypoints) + os.pathsep + env.get("PATH", os.defpath)
            result = json.loads(run(str(entrypoints / "python"), "-m", "src.onboarding", "preflight", env=env))
            if result.get("status") != "ok" or result.get("enabled_tools") != 8 or result.get("text_tool") != "ask-gemini":
                raise RuntimeError("The installed gemini-web-mcp server did not pass MCP preflight")
            run(str(entrypoints / "python"), str(ROOT / "scripts/smoke_mcp_protocol.py"), env=env)
        verified.write_text(digest + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(runtime, ignore_errors=True)
        raise
    return runtime, digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Install an already built wheel")
    parser.add_argument("--offline", action="store_true", help="Use uv's local cache")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required")

    home = Path.home()
    shared = home / ".local/share/gemini-web-mcp"
    releases = shared / "releases"
    commands = home / ".local/bin"
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    commands.mkdir(parents=True, exist_ok=True)

    with (shared / "install.lock").open("w") as lock, tempfile.TemporaryDirectory(prefix="gemini-local-wheel-") as temp:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = shared / "current"
        if current.exists() and not current.is_symlink():
            raise SystemExit(f"Expected a runtime symlink at {current}")
        for name in COMMANDS:
            link = commands / name
            target = current / "bin" / name
            if os.path.lexists(link) and (not link.is_symlink() or link.readlink() != target):
                raise SystemExit(f"Conflicting local command exists at {link}")

        if args.wheel is None:
            options = ("--offline",) if args.offline else ()
            run(uv, "build", *options, "--wheel", "--out-dir", temp)
            wheels = list(Path(temp).glob("gemini_mcp_server-*.whl"))
            if len(wheels) != 1:
                raise SystemExit("Expected one Gemini MCP wheel")
            wheel = wheels[0]
        else:
            wheel = args.wheel.resolve(strict=True)
        runtime, digest = install_runtime(uv, wheel, releases, offline=args.offline)
        with tempfile.TemporaryDirectory(prefix="activate-", dir=shared) as stage:
            pointer = Path(stage) / "current"
            pointer.symlink_to(runtime, target_is_directory=True)
            pointer.replace(current)
        for name in COMMANDS:
            link = commands / name
            if not os.path.lexists(link):
                link.symlink_to(current / "bin" / name)
        for legacy in LEGACY_COMMANDS:
            link = commands / legacy
            if os.path.islink(link):
                resolved = str(link.readlink())
                if "gemini-web-mcp" in resolved:
                    link.unlink()
        print(json.dumps({"status": "ok", "wheel_sha256": digest, "commands": str(commands), "runtime": str(runtime)}))


if __name__ == "__main__":
    main()
