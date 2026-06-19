![Codex controls Claude Code](docs/assets/codex-controls-claude-code.png)

<p align="center">
  <a href="./README.md"><img alt="Language English" src="https://img.shields.io/badge/Language-English-2f6feb?style=for-the-badge"></a>
  <a href="./README.ja.md"><img alt="Read in Japanese" src="https://img.shields.io/badge/Read%20in-%E6%97%A5%E6%9C%AC%E8%AA%9E-f97316?style=for-the-badge"></a>
  <img alt="Version v0.0.3" src="https://img.shields.io/badge/Version-v0.0.3-111827?style=for-the-badge">
</p>

# ClaudeCodeGLM Supervisor

ClaudeCodeGLM Supervisor lets Codex delegate bounded implementation and review
work to Claude Code while routing Claude Code to Z.AI GLM-5.2.

Codex stays responsible for planning, task design, risk control, validation,
and final acceptance. Claude Code GLM-5.2 acts as a constrained worker that
edits only the requested files, runs the requested checks, and returns a compact
result for Codex to audit.

## Install

The recommended install path is PyPI through `uv`.

One-off check:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

Persistent CLI install:

```bash
uv tool install claude-glm52-supervisor
claude-glm52 setup --print
```

The GitHub Release installer is available for users who prefer a release-asset
download. It verifies checksums before installing:

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

## Let Codex Set It Up

The fastest path is to ask Codex to do the environment checks and install steps
for you. Copy the prompt in
[`docs/codex-setup-prompt.md`](docs/codex-setup-prompt.md) into a new Codex
session.

The prompt tells Codex to:

- install or upgrade `claude-glm52-supervisor` through `uv`
- verify Python, Bash, Git, Claude Code, CLIProxyAPI, and optional timeout
- create the isolated Claude Code worker config directory
- avoid reading or printing secrets
- run offline and online doctor checks
- run a no-edit smoke test only when the local tools are ready
- leave a short setup report with any remaining manual steps

## Requirements

Required:

- macOS or Linux shell environment
- Python 3.11 or newer
- Bash
- Git
- Claude Code CLI installed and authenticated
- CLIProxyAPI installed and running locally
- A Z.AI account/API key with GLM-5.2 access

Recommended:

- `uv` for install and upgrades
- `rg` for faster repository inspection
- GNU `timeout` for runaway-task guards
- a dedicated Claude Code worker config directory, usually
  `~/.claude-glm52-worker`

Sensitive values are read from local environment or provider config at runtime.
Do not commit API keys, `.env` files, auth tokens, provider configs, or shell
history.

## Setup Overview

1. Install the supervisor CLI:

   ```bash
   uv tool install claude-glm52-supervisor
   claude-glm52 doctor --offline
   ```

2. Install and authenticate Claude Code:

   ```bash
   claude --version
   ```

3. Install and run CLIProxyAPI, then expose a local Anthropic-compatible
   endpoint such as:

   ```text
   http://127.0.0.1:8317
   ```

4. Configure CLIProxyAPI so the Claude Code-visible alias routes to GLM-5.2:

   ```text
   claude-opus-4-6[1m] -> glm-5.2
   ```

5. Use a dedicated worker config directory:

   ```bash
   export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
   mkdir -p "$CLAUDE_GLM52_WORKER_CONFIG_DIR"
   ```

6. Run the setup guide and doctor:

   ```bash
   claude-glm52 setup --print
   claude-glm52 doctor
   ```

## Basic Smoke Test

Run a no-edit review task before delegating real work:

```bash
claude-glm52-delegate \
  --role review \
  --cwd . \
  --timeout 120 \
  --retries 0 \
  --no-usage-log \
  --no-quota-snapshot \
  "Return exactly: ok. Do not edit files."
```

If this fails, check `claude --version`, confirm CLIProxyAPI is running, and
run `claude-glm52 doctor` again.

## Core Commands

Single task:

```bash
claude-glm52-delegate \
  --cwd /path/to/repo \
  --timeout 900 \
  --retries 1 \
  --prompt-file task-packet.md \
  --result-file delegate-result.json
```

Read-only review:

```bash
claude-glm52-delegate \
  --role review \
  --cwd /path/to/repo \
  --timeout 300 \
  --prompt-file review-packet.md \
  --result-file review-result.json
```

Independent batch work:

```bash
claude-glm52-batch \
  --plan-file batch-plan.json \
  --concurrency 2 \
  --result-file batch-result.json
```

Image-aware task:

```bash
claude-glm52-delegate \
  --cwd /path/to/repo \
  --image screenshots/error.png \
  --vision-backend mcp \
  --vision-mode auto \
  --prompt-file task-packet.md \
  --result-file delegate-result.json
```

The GLM-5.2 coding worker is treated as text-only. Image files are analyzed
first through a lightweight Z.AI Vision MCP/OCR preflight. The extracted
evidence text is injected into the task packet, while raw image summaries are
not persisted in result JSON or usage logs.

## Task Packet Shape

Use concise English packets unless the task itself is about Japanese text.

```text
Role: implementation worker
Goal:
Repo/CWD:
Files likely relevant:
Allowed files:
Constraints:
Acceptance criteria:
Validation commands:
Do not:
Return:
```

Important constraints:

- list the files the worker may modify
- forbid broad searches from `/` or `~`
- forbid deleting files, editing secrets, committing, pushing, and changing
  auth/config
- include the exact validation command when possible
- keep final worker output short; large deliverables should be written to files

## Verified Capabilities

| Layer | Route |
| --- | --- |
| Orchestrator | Codex |
| Worker runtime | Claude Code |
| Provider gateway | CLIProxyAPI |
| Upstream model | Z.AI GLM-5.2 |
| Claude Code-visible model | `claude-opus-4-6[1m]` alias |
| Verified context window | 1,000,000 tokens |
| Verified Claude Code output ceiling | 64,000 tokens |
| Vision handling | Z.AI Vision MCP/OCR preflight, then text context injection |

GLM-5.2 can support larger outputs at the model/API layer, but this Claude Code
worker route is currently verified to a 64K Claude Code output ceiling. For a
true 128K single-response output, use a separately verified direct GLM-5.2
route instead of assuming Claude Code will expose it safely.

## More Docs

- [Installation details](docs/install.md)
- [Install channels](docs/distribution-strategy.md)
- [Codex setup prompt](docs/codex-setup-prompt.md)

## License

See [`LICENSE`](LICENSE). The current notice is intentionally conservative and
does not grant open-source reuse or redistribution rights.
