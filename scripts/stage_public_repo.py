#!/usr/bin/env python3
"""Stage a clean public repository locally without pushing to GitHub."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import build_public_snapshot


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path, *, timeout: int = 120) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)


def output(cmd: list[str], cwd: Path, *, timeout: int = 120) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout.strip()


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"missing required command: {name}")


def project_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def validate_version(version: str, root: Path) -> None:
    if not version.startswith("v") or len(version.split(".")) != 3:
        raise SystemExit("--version must look like vX.Y.Z")
    expected = project_version(root)
    actual = version[1:]
    if actual != expected:
        raise SystemExit(f"--version {version} does not match pyproject version {expected}")


def init_git_repo(out_dir: Path, version: str) -> None:
    ensure_command("git")
    run(["git", "init", "-b", "main"], out_dir)
    run(["git", "add", "-A"], out_dir)
    run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=ClaudeCodeGLM Release Bot",
            "-c",
            "user.email=release-bot@example.invalid",
            "commit",
            "-m",
            f"chore: public snapshot {version}",
        ],
        out_dir,
    )
    run(["git", "-c", "core.hooksPath=/dev/null", "tag", version], out_dir)


def validate_snapshot_repo(out_dir: Path) -> None:
    run([sys.executable, "scripts/public_audit.py", "--root", str(out_dir), "--all-files"], out_dir)
    py_files = [
        *sorted(str(path) for path in (out_dir / "src" / "claude_glm52_supervisor").glob("*.py")),
        *sorted(str(path) for path in (out_dir / "outputs").glob("*.py")),
        str(out_dir / "scripts" / "public_audit.py"),
        str(out_dir / "scripts" / "build_public_snapshot.py"),
        str(out_dir / "scripts" / "stage_public_repo.py"),
    ]
    run([sys.executable, "-m", "py_compile", *py_files], out_dir)
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], out_dir, timeout=180)


def build_release_assets(out_dir: Path, assets_dir: Path, version: str) -> None:
    script = out_dir / "packaging" / "release" / "build-release-assets.sh"
    run(["bash", str(script), "--version", version, "--out-dir", str(assets_dir)], out_dir, timeout=120)


def build_package(out_dir: Path, build_dir: Path) -> None:
    ensure_command("uv")
    run(["uv", "build", "--out-dir", str(build_dir)], out_dir, timeout=180)


def stage(args: argparse.Namespace) -> dict:
    version = args.version
    validate_version(version, ROOT)
    out_dir = Path(args.out_dir).expanduser().resolve()
    assets_dir = Path(args.assets_dir).expanduser().resolve() if args.assets_dir else out_dir.parent / f"{out_dir.name}-release-assets"
    wheel_dir = Path(args.wheel_dir).expanduser().resolve() if args.wheel_dir else out_dir.parent / f"{out_dir.name}-python-dist"

    if args.dry_run:
        return {
            "version": version,
            "out_dir": str(out_dir),
            "assets_dir": str(assets_dir),
            "wheel_dir": str(wheel_dir),
            "replace": bool(args.replace),
            "run_tests": not args.skip_tests,
            "build_package": not args.skip_package_build,
            "build_release_assets": not args.skip_release_assets,
        }

    build_args = ["--out-dir", str(out_dir)]
    if args.replace:
        build_args.append("--replace")
    build_public_snapshot.main(build_args)

    init_git_repo(out_dir, version)
    if not args.skip_tests:
        validate_snapshot_repo(out_dir)
    if not args.skip_package_build:
        build_package(out_dir, wheel_dir)
    if not args.skip_release_assets:
        build_release_assets(out_dir, assets_dir, version)

    status = output(["git", "status", "--short"], out_dir)
    tagged = output(["git", "tag", "--list", version], out_dir)
    commit = output(["git", "rev-parse", "--short", "HEAD"], out_dir)
    return {
        "version": version,
        "out_dir": str(out_dir),
        "assets_dir": str(assets_dir),
        "wheel_dir": str(wheel_dir),
        "commit": commit,
        "tag": tagged,
        "git_status_short": status,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Local public repo directory to create.")
    parser.add_argument("--version", required=True, help="Version tag, for example v0.1.0.")
    parser.add_argument("--assets-dir", help="Directory for GitHub Release assets.")
    parser.add_argument("--wheel-dir", help="Directory for Python wheel/sdist.")
    parser.add_argument("--replace", action="store_true", help="Replace out-dir if it already exists.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip tests in the staged public repo.")
    parser.add_argument("--skip-package-build", action="store_true", help="Skip uv build in the staged public repo.")
    parser.add_argument("--skip-release-assets", action="store_true", help="Skip release asset generation.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = stage(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
