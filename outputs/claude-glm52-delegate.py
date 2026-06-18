#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

from claude_glm52_supervisor.delegate import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
