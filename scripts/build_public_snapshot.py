#!/usr/bin/env python3
"""Build a clean public snapshot directory from the private development tree."""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TOP_LEVEL_FILES = (
    ".gitignore",
    "LICENSE",
    "README.md",
    "README.ja.md",
    "SECURITY.md",
    "pyproject.toml",
)

PUBLIC_DIRS = (
    ".github/workflows",
    "docs",
    "outputs",
    "packaging/homebrew-tap",
    "packaging/install",
    "packaging/release",
    "scripts",
    "src",
    "tests",
)

EXCLUDE_PATTERNS = (
    ".git/**",
    ".codex/**",
    ".autoreview-*",
    ".env*",
    "CONTINUITY.md",
    "HANDOFF.md",
    "logs/**",
    "artifacts/**",
    "work/**",
    "build/**",
    "dist/**",
    "**/*.egg-info/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.DS_Store",
    "tests/test_supervisor_duel_vision.py",
)


def is_excluded(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in EXCLUDE_PATTERNS)


def iter_public_files() -> list[Path]:
    files: list[Path] = []
    for rel in TOP_LEVEL_FILES:
        path = ROOT / rel
        if path.is_file() and not is_excluded(rel):
            files.append(path)

    for rel_dir in PUBLIC_DIRS:
        root_dir = ROOT / rel_dir
        if not root_dir.is_dir():
            continue
        for path in root_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if not is_excluded(rel):
                files.append(path)

    return sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix())


def safe_replace_dir(path: Path) -> None:
    resolved = path.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), ROOT.resolve()}
    if resolved in forbidden or ROOT.resolve() in (resolved, *resolved.parents):
        raise SystemExit(f"refusing to replace unsafe output directory: {resolved}")
    shutil.rmtree(resolved)


def copy_public_files(out_dir: Path, files: list[Path]) -> list[str]:
    copied: list[str] = []
    for source in files:
        rel = source.relative_to(ROOT)
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        copied.append(rel.as_posix())
    return copied


def chmod_public_scripts(out_dir: Path) -> None:
    for rel in (
        "packaging/install/claude-glm52-installer.sh",
        "packaging/release/build-release-assets.sh",
    ):
        path = out_dir / rel
        if path.exists():
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def validate_snapshot(out_dir: Path) -> None:
    forbidden = [path for path in out_dir.rglob("*") if is_excluded(path.relative_to(out_dir).as_posix())]
    if forbidden:
        display = "\n".join(f"- {path.relative_to(out_dir).as_posix()}" for path in forbidden[:20])
        raise SystemExit(f"snapshot contains excluded files:\n{display}")

    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "public_audit.py"), "--root", str(out_dir), "--all-files"],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if audit.returncode != 0:
        sys.stdout.write(audit.stdout)
        sys.stderr.write(audit.stderr)
        raise SystemExit(audit.returncode)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Directory to create as the public snapshot.")
    parser.add_argument("--replace", action="store_true", help="Replace out-dir if it already exists.")
    parser.add_argument("--dry-run", action="store_true", help="Print the file list without writing.")
    parser.add_argument("--manifest-json", help="Optional path to write a JSON manifest.")
    parser.add_argument("--no-audit", action="store_true", help="Skip public audit after copying.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir).expanduser()
    files = iter_public_files()
    rels = [path.relative_to(ROOT).as_posix() for path in files]

    manifest = {
        "source_root": str(ROOT),
        "out_dir": str(out_dir),
        "file_count": len(rels),
        "files": rels,
    }

    if args.manifest_json:
        manifest_path = Path(args.manifest_json).expanduser()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if out_dir.exists():
        if not args.replace:
            raise SystemExit(f"output directory exists; pass --replace: {out_dir}")
        safe_replace_dir(out_dir)

    out_dir.mkdir(parents=True)
    copied = copy_public_files(out_dir, files)
    chmod_public_scripts(out_dir)
    if not args.no_audit:
        validate_snapshot(out_dir)
    print(f"Public snapshot ready: {out_dir} ({len(copied)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
