#!/usr/bin/env python3
"""ClaudeCodeGLM Supervisor umbrella CLI.

Package-friendly entry point that exposes safe, side-effect free introspection
commands (--version, paths, doctor, setup --print) for the install. It never
mutates global config, never writes secrets, and never depends on Claude Code,
CLIProxyAPI, npm, or pip at runtime. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

from . import __version__
from ._runtime import PACKAGE_ROOT, find_source_root


VERSION = __version__

SCRIPT_PATH = Path(__file__).resolve()


def _repo_root() -> Path:
    return find_source_root() or PACKAGE_ROOT


REPO_ROOT = _repo_root()


SOURCE_RUNTIME_ARTIFACTS = {
    "outputs/claude-glm52.py": "Umbrella CLI (this file)",
    "outputs/claude-glm52-delegate.py": "Single-task delegation wrapper",
    "outputs/claude-glm52-batch.py": "Parallel bounded batch wrapper",
    "outputs/claude-glm52-subagent.sh": "Raw Claude Code worker runner",
    "outputs/claude-glm52-reviewer.sh": "Read-only review shortcut (role=review)",
    "outputs/glm52_usage.py": "Provider usage/quota normalization helpers",
    "outputs/glm52_usage_snapshots.py": "Before/after usage snapshot accounting",
    "outputs/glm52_vision.py": "Vision MCP/OCR preflight helpers",
}

PACKAGE_RUNTIME_ARTIFACTS = {
    "cli.py": "Umbrella CLI module",
    "delegate.py": "Single-task delegation module",
    "batch.py": "Parallel bounded batch module",
    "resources/claude-glm52-subagent.sh": "Raw Claude Code worker runner",
    "resources/claude-glm52-reviewer.sh": "Read-only review shortcut",
    "glm52_usage.py": "Provider usage/quota normalization helpers",
    "glm52_usage_snapshots.py": "Before/after usage snapshot accounting",
    "glm52_vision.py": "Vision MCP/OCR preflight helpers",
}

# Environment variables that are safe to NAME in doctor output. Values are never
# printed.
SAFE_ENV_NAMES = (
    "CLAUDE_GLM52_WORKER_CONFIG_DIR",
    "CLAUDE_GLM52_TIMEOUT_SECONDS",
    "CLAUDE_GLM52_SAFETY_CEILING_SECONDS",
    "CLAUDE_GLM52_MAX_BUDGET_USD",
    "CLAUDE_GLM52_MAX_OUTPUT_TOKENS",
)


def _print_version() -> int:
    print(f"claude-glm52 {VERSION}")
    return 0


def _cmd_paths() -> dict:
    paths = {
        "repo_root": str(REPO_ROOT),
        "package": str(PACKAGE_ROOT),
    }
    if find_source_root() is not None:
        paths.update(
            {
                "outputs": str(REPO_ROOT / "outputs"),
                "docs": str(REPO_ROOT / "docs"),
                "tests": str(REPO_ROOT / "tests"),
                "scripts": str(REPO_ROOT / "scripts"),
                "packaging": str(REPO_ROOT / "packaging"),
            }
        )
    for rel in _runtime_artifacts():
        paths[rel] = str(REPO_ROOT / rel)
    # Surface the optional Homebrew tap skeleton in source checkouts. The
    # files are not present in normal PyPI/uvx installs.
    tap_formula = REPO_ROOT / "packaging" / "homebrew-tap" / "Formula" / "claude-glm52.rb"
    if tap_formula.is_file():
        paths["packaging/homebrew-tap/Formula/claude-glm52.rb"] = str(tap_formula)
    return paths


def _print_paths() -> int:
    payload = _cmd_paths()
    width = max(len(k) for k in payload)
    for key in sorted(payload):
        print(f"{key.ljust(width)}  {payload[key]}")
    return 0


def _have_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _have_file(rel: str) -> bool:
    return (REPO_ROOT / rel).is_file()


def _runtime_artifacts() -> dict[str, str]:
    return SOURCE_RUNTIME_ARTIFACTS if find_source_root() is not None else PACKAGE_RUNTIME_ARTIFACTS


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name))


def doctor(offline: bool = False, verbose: bool = False) -> tuple:
    """Run safe doctor checks.

    Offline mode (default for tests and the Homebrew formula test) only checks
    local install artifacts. It never reaches Claude Code, CLIProxyAPI, the
    network, or secret-bearing config.
    """
    lines = []

    def record(status: str, name: str, detail: str = "") -> None:
        suffix = f"  {detail}" if detail else ""
        lines.append(f"[{status}] {name}{suffix}")

    failures = 0
    warnings = 0

    for rel in _runtime_artifacts():
        if _have_file(rel):
            record("PASS", f"file:{rel}")
        else:
            record("FAIL", f"file:{rel}", "missing")
            failures += 1

    # Package managers and generated entry points run this CLI via a concrete
    # interpreter, so a running `sys.executable` is sufficient proof of a
    # working Python runtime even when the convenience `python3` command is
    # absent from PATH.
    current_python = sys.executable or ""
    if current_python:
        record("PASS", "runtime:python", current_python)
    else:
        record("FAIL", "runtime:python", "no Python interpreter available")
        failures += 1

    if _have_executable("python3"):
        record("PASS", "tool:python3", shutil.which("python3") or "")
    else:
        record(
            "WARN",
            "tool:python3",
            "not on PATH; runtime Python above is sufficient for this CLI",
        )
        warnings += 1

    if _have_executable("bash"):
        record("PASS", "tool:bash", shutil.which("bash") or "")
    else:
        record("FAIL", "tool:bash", "not found on PATH")
        failures += 1

    if _have_executable("git"):
        record("PASS", "tool:git", "available")
    else:
        record("WARN", "tool:git", "not on PATH; Codex audit helper unavailable")
        warnings += 1

    if _have_executable("timeout"):
        record("PASS", "tool:timeout", "available for runaway-task guard")
    else:
        record("WARN", "tool:timeout", "install `brew install coreutils` for the safety ceiling")
        warnings += 1

    if offline:
        record("WARN", "mode:offline", "skipped claude-code/cliproxyapi/secret checks")
        warnings += 1
        lines.append("")
        lines.append(f"offline doctor: {failures} fail / {warnings} warn")
        return lines, (1 if failures else 0)

    if _have_executable("claude"):
        record("PASS", "tool:claude", "claude code CLI found")
    else:
        record("WARN", "tool:claude", "claude code CLI not on PATH")
        warnings += 1

    if _have_executable("cliproxyapi"):
        record("PASS", "tool:cliproxyapi", "CLIProxyAPI binary found")
    else:
        record("WARN", "tool:cliproxyapi", "install/run CLIProxyAPI before delegating real work")
        warnings += 1

    for name in SAFE_ENV_NAMES:
        if _env_present(name):
            record("PASS", f"env:{name}", "set (value not shown)")
        else:
            record("WARN", f"env:{name}", "not set; using wrapper default")
            warnings += 1

    lines.append("")
    lines.append(f"doctor: {failures} fail / {warnings} warn")
    return lines, (1 if failures else 0)


def _run_doctor(offline: bool, verbose: bool) -> int:
    lines, code = doctor(offline=offline, verbose=verbose)
    for line in lines:
        print(line)
    return code


SETUP_GUIDE = """\
Manual setup guide for ClaudeCodeGLM Supervisor

This CLI is designed for PyPI/uvx, a checksum-verified release installer, or a
clean source checkout. Installers and entry points never write secrets, never
edit Claude Code global config, and never start CLIProxyAPI. Run
`claude-glm52 doctor` after install to see what is still missing on this
machine.

1. Python and bash
   - Verify: `python3 --version` (3.11+) and `bash --version`.
   - Runtime wrappers use the Python standard library.

2. Claude Code CLI
   - Recommended: `brew install --cask claude-code` (or official Native Install).
   - Verify: `claude --version`.

3. CLIProxyAPI
   - Install from the project's official release.
   - Run it locally (default verified endpoint: http://127.0.0.1:8317).
   - Map the Claude Code-visible alias to GLM-5.2, e.g.
     claude-opus-4-6[1m] -> glm-5.2

4. Z.AI API key
   - Create a GLM-5.2-capable key in your Z.AI account.
   - Load the key from your shell/keychain/local provider config.
   - NEVER commit the key value, .env, or provider configs to this repo.

5. Worker profile
   - Keep Claude Code worker config separate from your daily profile:
     export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
     mkdir -p "$CLAUDE_GLM52_WORKER_CONFIG_DIR"

6. Smoke test (read-only, lightweight)
   - After package/install (uses the installed wrapper, works without a source
     checkout):
       claude-glm52-delegate --role review --cwd . \
         --timeout 120 --retries 0 --no-usage-log --no-quota-snapshot \
         "Return exactly: ok. Do not edit files."
   - From a source checkout only (no install):
       python3 outputs/claude-glm52-delegate.py --role review --cwd . \
         --timeout 120 --retries 0 --no-usage-log --no-quota-snapshot \
         "Return exactly: ok. Do not edit files."

7. Verify install health
   - claude-glm52 --version
   - claude-glm52 paths
   - claude-glm52 doctor
   - claude-glm52 doctor --offline   # CI / package smoke-test mode

Notes
   - claude-glm52 setup --print never mutates the system. It only prints this
     guide. There is intentionally no `setup` mutation command in this CLI.
   - If doctor reports FAIL for a required local file, reinstall the package or
     refresh the source checkout. WARN items can be deferred until real
     delegation.
"""


def _print_setup() -> int:
    print(SETUP_GUIDE, end="")
    return 0


def _setup_no_print(ns: argparse.Namespace) -> int:
    print(
        "claude-glm52 setup does not mutate the system. "
        "Use `claude-glm52 setup --print` for the manual guide, "
        "or see docs/install.md.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-glm52",
        description=(
            "ClaudeCodeGLM Supervisor umbrella CLI. Safe, read-only install "
            "introspection. Does not mutate config or expose secrets."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the claude-glm52 CLI version and exit.",
    )
    sub = parser.add_subparsers(dest="command")

    p_paths = sub.add_parser("paths", help="Print repository and wrapper paths.")
    p_paths.set_defaults(func=lambda ns: _print_paths())

    p_doc = sub.add_parser("doctor", help="Check install health without touching secrets.")
    p_doc.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Only check local install artifacts. Skip Claude Code, "
            "CLIProxyAPI, network, and secret-adjacent checks. Use this in "
            "CI, the Homebrew formula test, and any air-gapped environment."
        ),
    )
    p_doc.add_argument(
        "--verbose",
        action="store_true",
        help="Reserved for future verbose output. Currently a no-op.",
    )
    p_doc.set_defaults(func=lambda ns: _run_doctor(ns.offline, ns.verbose))

    p_setup = sub.add_parser("setup", help="Safe setup guidance. No mutation.")
    p_setup.add_argument(
        "--print",
        dest="print_guide",
        action="store_true",
        help="Print the manual setup guide. Never writes files or secrets.",
    )
    p_setup.set_defaults(
        func=lambda ns: _print_setup() if ns.print_guide else _setup_no_print(ns)
    )
    return parser


def main(argv: Iterable | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.version:
        return _print_version()

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
