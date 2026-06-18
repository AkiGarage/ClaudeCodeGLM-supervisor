from __future__ import annotations

import os
import subprocess
import sys

from ._runtime import resource_path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    script = resource_path("claude-glm52-subagent.sh")
    if os.name == "posix":
        os.execv("/bin/bash", ["/bin/bash", str(script), *args])
    return subprocess.call(["bash", str(script), *args])


if __name__ == "__main__":
    raise SystemExit(main())
