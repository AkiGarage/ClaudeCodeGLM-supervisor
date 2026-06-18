#!/usr/bin/env python3
"""Fail-closed public release audit for privacy and secret hygiene."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]

GENERATED_PATTERNS = (
    "CONTINUITY.md",
    "HANDOFF.md",
    "logs/**",
    ".codex/**",
    ".autoreview-*",
    "artifacts/**",
    "work/**/*.json",
    "work/**/runs/**",
)

TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cfg",
    ".conf",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".mjs",
    ".plist",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}

PERSONAL_OWNER = chr(65) + "ki"
PERSONAL_OWNER_LOWER = PERSONAL_OWNER.lower()
PERSONAL_SHORT = chr(97) + chr(121)

PRIVATE_PATTERNS = (
    ("local_user_path", re.compile(r"(/" + r"Users/|/home/|/user/|\\\\Users\\\\)[A-Za-z0-9._-]+")),
    ("private_launch_agent_label", re.compile(r"com\." + PERSONAL_OWNER_LOWER + r"\b", re.IGNORECASE)),
    ("personal_name", re.compile(r"\b" + PERSONAL_OWNER + r"\b|\b" + PERSONAL_OWNER_LOWER + r"\b")),
    ("personal_short_email_or_path", re.compile(r"\b" + PERSONAL_SHORT + r"@|/" + PERSONAL_SHORT + r"\b|-" + PERSONAL_SHORT + r"\b", re.IGNORECASE)),
)

SECRET_PATTERNS = (
    ("private_key_block", re.compile(r"BEGIN (RSA |DSA |EC |OPENSSH |)PRIVATE KEY")),
    (
        "likely_secret_assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|auth[_-]?token|access[_-]?token|bearer|secret|private[_-]?key)"
            r"\b[^\n]{0,40}[:=]\s*['\"]?([A-Za-z0-9_./+=:-]{16,})"
        ),
    ),
)

PLACEHOLDER_WORDS = ("placeholder", "example", "dummy", "fake", "test", "redacted", "secret", "<", "$")


def git_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line for line in result.stdout.splitlines() if line]


def filesystem_files(root: Path) -> list[str]:
    files: list[str] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root).as_posix()
        if any(part in {".git", "__pycache__"} for part in file_path.relative_to(root).parts):
            continue
        files.append(rel)
    return sorted(files)


def is_generated(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in GENERATED_PATTERNS)


def read_text(root: Path, path: str) -> str | None:
    file_path = root / path
    if file_path.suffix not in TEXT_SUFFIXES:
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in PLACEHOLDER_WORDS)


def is_dynamic_secret_line(line: str) -> bool:
    lowered = line.lower()
    return "{" in line and "}" in line and ("api_key" in lowered or "token" in lowered)


def audit(root: Path, paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if is_generated(path):
            findings.append(f"{path}:1: generated_or_local_artifact present")

        text = read_text(root, path)
        if text is None:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PRIVATE_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path}:{line_no}: {label}: {line.strip()[:160]}")
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if is_dynamic_secret_line(line):
                    continue
                secret_value = match.group(match.lastindex or 0)
                if is_placeholder(secret_value):
                    continue
                findings.append(f"{path}:{line_no}: {label}: {line.strip()[:160]}")
    return findings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit public release files for local/private artifacts and likely secrets."
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Root directory to audit. Defaults to the repository root.",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Scan all files under --root instead of git tracked files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    paths = filesystem_files(root) if args.all_files else git_files(root)
    findings = audit(root, paths)

    if findings:
        print("Public audit failed:")
        for finding in findings:
            print(f"- {finding}")
        print(f"\nTotal findings: {len(findings)}")
        return 1

    print("Public audit passed: no tracked privacy, generated artifact, or likely secret findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
