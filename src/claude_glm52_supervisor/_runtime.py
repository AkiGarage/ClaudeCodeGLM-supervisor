from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def find_source_root() -> Path | None:
    for candidate in (PACKAGE_ROOT, *PACKAGE_ROOT.parents):
        outputs = candidate / "outputs"
        if (
            outputs.is_dir()
            and (outputs / "claude-glm52-delegate.py").is_file()
            and (outputs / "claude-glm52-batch.py").is_file()
        ):
            return candidate
    return None


def runtime_root() -> Path:
    return find_source_root() or PACKAGE_ROOT


def default_usage_log() -> Path:
    source_root = find_source_root()
    if source_root is not None:
        return source_root / "logs" / "glm52-usage.jsonl"
    state_home = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return state_home / "claude-glm52" / "glm52-usage.jsonl"


def resource_path(name: str) -> Path:
    source_root = find_source_root()
    if source_root is not None:
        candidate = source_root / "outputs" / name
        if candidate.is_file():
            return candidate
    return Path(str(files("claude_glm52_supervisor").joinpath("resources", name)))


def default_delegate_path() -> Path | None:
    source_root = find_source_root()
    if source_root is None:
        return None
    candidate = source_root / "outputs" / "claude-glm52-delegate.py"
    return candidate if candidate.is_file() else None
