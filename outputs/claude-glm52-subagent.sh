#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  claude-glm52-subagent.sh "coding task prompt"
  claude-glm52-subagent.sh --cwd /path/to/repo "coding task prompt"
  claude-glm52-subagent.sh --full --cwd /path/to/repo "coding task prompt"
  claude-glm52-subagent.sh --role review --cwd /path/to/repo "review prompt"
  claude-glm52-subagent.sh --timeout 1800 --cwd /path/to/repo "long coding task prompt"
  claude-glm52-subagent.sh --safety-ceiling 21600 --max-budget-usd 5.00 --cwd /path/to/repo "guarded task prompt"
  claude-glm52-subagent.sh --max-output-tokens 64000 --cwd /path/to/repo "large-output task prompt"

Runs Claude Code as a GLM-5.2[1m] coding subagent using ~/.claude-glm52-worker.
Output is JSON so Codex or another orchestrator can parse result/modelUsage.
Default mode is --bare to avoid loading hooks and large ambient context.
Use --full when you explicitly want normal Claude Code startup context.
MCP is forced off with --strict-mcp-config and an empty inline config.
Global ~/.claude changes do not affect this worker profile.
Roles:
  implement  Allows edits. Best for Codex/Hermes delegating bounded implementation.
  review     Read-only tool surface. Best for external review of Codex changes.
Timeout:
  By default this runner does not add a short task timeout. That lets long
  Claude Code GLM-5.2 implementation tasks finish. It still applies a generous
  runaway safety ceiling and API budget guard:
    CLAUDE_GLM52_SAFETY_CEILING_SECONDS (default: 21600 = 6h)
    CLAUDE_GLM52_MAX_BUDGET_USD (default: 5.00)
  Pass --timeout SECONDS, or set CLAUDE_GLM52_TIMEOUT_SECONDS, only when an
  orchestrator needs a tighter task-class guard.
  --no-timeout disables only the tighter task timeout; the safety ceiling remains.
Output:
  Claude Code stable currently reports a 64K output ceiling through this
  GLM-5.2 route. Routine packets should still keep compact summaries for token
  efficiency and write large deliverables to files.
USAGE
}

cwd="$(pwd)"
bare=1
role="implement"
timeout_seconds="${CLAUDE_GLM52_TIMEOUT_SECONDS:-0}"
safety_ceiling_seconds="${CLAUDE_GLM52_SAFETY_CEILING_SECONDS:-21600}"
max_budget_usd="${CLAUDE_GLM52_MAX_BUDGET_USD:-5.00}"
max_output_tokens="${CLAUDE_GLM52_MAX_OUTPUT_TOKENS:-0}"
worker_config_dir="${CLAUDE_GLM52_WORKER_CONFIG_DIR:-${HOME}/.claude-glm52-worker}"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

while [[ "${1:-}" == "--full" || "${1:-}" == "--bare" || "${1:-}" == "--role" || "${1:-}" == "--timeout" || "${1:-}" == "--no-timeout" || "${1:-}" == "--safety-ceiling" || "${1:-}" == "--no-safety-ceiling" || "${1:-}" == "--max-budget-usd" || "${1:-}" == "--no-budget-guard" || "${1:-}" == "--max-output-tokens" || "${1:-}" == "--no-max-output-override" ]]; do
  case "$1" in
    --full) bare=0 ;;
    --bare) bare=1 ;;
    --timeout)
      if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
        usage >&2
        exit 2
      fi
      timeout_seconds="$2"
      shift
      ;;
    --no-timeout)
      timeout_seconds=0
      ;;
    --safety-ceiling)
      if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
        usage >&2
        exit 2
      fi
      safety_ceiling_seconds="$2"
      shift
      ;;
    --no-safety-ceiling)
      safety_ceiling_seconds=0
      ;;
    --max-budget-usd)
      if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        usage >&2
        exit 2
      fi
      max_budget_usd="$2"
      shift
      ;;
    --no-budget-guard)
      max_budget_usd=0
      ;;
    --max-output-tokens)
      if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
        usage >&2
        exit 2
      fi
      max_output_tokens="$2"
      shift
      ;;
    --no-max-output-override)
      max_output_tokens=0
      ;;
    --role)
      if [[ $# -lt 2 ]]; then
        usage >&2
        exit 2
      fi
      role="$2"
      shift
      ;;
  esac
  shift
done

if [[ "${1:-}" == "--cwd" ]]; then
  if [[ $# -lt 3 ]]; then
    usage >&2
    exit 2
  fi
  cwd="$2"
  shift 2
fi

if [[ $# -eq 0 ]]; then
  usage >&2
  exit 2
fi

prompt="$*"
bare_args=()
if [[ "$bare" -eq 1 ]]; then
  bare_args+=(--bare)
fi

case "$role" in
  implement)
    implement_allowed_tools="${CLAUDE_GLM52_IMPLEMENT_ALLOWED_TOOLS:-Read,Edit,MultiEdit,Glob,Grep,Bash(git status*),Bash(git diff*),Bash(git log*),Bash(git show*),Bash(python3 -m unittest*),Bash(python3 -m pytest*),Bash(python -m unittest*),Bash(python -m pytest*),Bash(pytest*),Bash(npm test*),Bash(npm run test*),Bash(npm run lint*),Bash(npm run typecheck*)}"
    disallowed_tools="${CLAUDE_GLM52_DISALLOWED_TOOLS:-Write,Bash(find *),Bash(/usr/bin/find *),Bash(grep -R *),Bash(rg / *),Bash(rg ~ *),Bash(ls / *),Bash(ls ~ *),Bash(rm *),Bash(trash *),Bash(mv *),Bash(chmod *),Bash(chown *),Bash(sudo *)}"
    role_args=(
      --permission-mode acceptEdits
      --allowedTools "$implement_allowed_tools"
      --disallowedTools "$disallowed_tools"
    )
    ;;
  review)
    disallowed_tools="${CLAUDE_GLM52_DISALLOWED_TOOLS:-Write,Edit,MultiEdit,Bash(find *),Bash(/usr/bin/find *),Bash(grep -R *),Bash(rg / *),Bash(rg ~ *),Bash(ls / *),Bash(ls ~ *),Bash(rm *),Bash(trash *),Bash(mv *),Bash(chmod *),Bash(chown *),Bash(sudo *)}"
    role_args=(
      --permission-mode default
      --allowedTools "Read,Glob,Grep,Bash(git status*),Bash(git diff*),Bash(git log*),Bash(git show*),Bash(python3 -m unittest*),Bash(python3 -m pytest*),Bash(python -m unittest*),Bash(python -m pytest*),Bash(pytest*),Bash(npm test*),Bash(npm run test*),Bash(npm run lint*),Bash(npm run typecheck*)"
      --disallowedTools "$disallowed_tools"
    )
    ;;
  *)
    echo "Unknown role: $role" >&2
    usage >&2
    exit 2
    ;;
esac

cd "$cwd"
export CLAUDE_CONFIG_DIR="$worker_config_dir"
if [[ "$max_output_tokens" != "0" ]]; then
  export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$max_output_tokens"
fi

worker_instruction="You are Claude Code GLM-5.2 acting as a subordinate coding worker for Codex. Codex owns planning, final audit, and user communication. Work in English unless the task is about Japanese text. Stay inside the current working directory and the requested files. Never search from / or ~. Never delete files or directories. Never edit task packets or tests unless explicitly requested. Do not push, commit, change secrets, alter auth files, or broaden scope. If the requested files are insufficient, stop with BLOCKED instead of searching broadly. Optimize for fast correct execution: read only the named files, edit directly, run the specified validation once, and stop when acceptance criteria are met or a concrete blocker is found. Do not produce long traces, tables, or exhaustive explanations. Return at most 10 concise bullet lines: status, files changed, validation, blocker if any, residual risk."
if [[ "$role" == "review" ]]; then
  worker_instruction="You are Claude Code GLM-5.2 acting as a read-only external reviewer for Codex. Work in English unless the task is about Japanese text. Do not edit files. Stay inside the current working directory and requested files. Never search from / or ~. Ignore delegation artifacts such as delegate-result.json, review-result.json, *_result.json, and task-packet files unless explicitly requested. Prioritize correctness bugs, regressions, security issues, missing tests, and risky assumptions. Run one relevant validation command when it is clearly allowed; if denied, stop retrying that command and state it once. Findings first, with file paths and concrete evidence. If no actionable issues, say so and name residual risk. Keep output under 12 concise bullets; no manual trace tables unless there is a blocker."
fi

budget_args=()
if [[ "$max_budget_usd" != "0" ]]; then
  budget_args=(--max-budget-usd "$max_budget_usd")
fi

claude_cmd=(
  claude \
  -p \
  "${bare_args[@]}" \
  --no-chrome \
  --disable-slash-commands \
  --no-session-persistence \
  --setting-sources user \
  --strict-mcp-config \
  --mcp-config '{"mcpServers":{}}' \
  --model opus \
  --effort max \
  "${budget_args[@]}" \
  --append-system-prompt "$worker_instruction" \
  "${role_args[@]}" \
  --output-format json \
  "$prompt"
)

guard_seconds="$timeout_seconds"
if [[ "$guard_seconds" -eq 0 && "$safety_ceiling_seconds" -gt 0 ]]; then
  guard_seconds="$safety_ceiling_seconds"
fi

if [[ "$guard_seconds" -gt 0 ]]; then
  if command -v timeout >/dev/null 2>&1; then
    exec timeout -k 60 "$guard_seconds" "${claude_cmd[@]}"
  elif [[ -x /opt/homebrew/bin/timeout ]]; then
    exec /opt/homebrew/bin/timeout -k 60 "$guard_seconds" "${claude_cmd[@]}"
  else
    echo "runtime guard requested but no timeout binary was found" >&2
    exit 127
  fi
fi

exec "${claude_cmd[@]}"
