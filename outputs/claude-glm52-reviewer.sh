#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec "$script_dir/claude-glm52-subagent.sh" --help
fi
exec "$script_dir/claude-glm52-subagent.sh" --role review "$@"
