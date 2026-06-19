# Installation

ClaudeCodeGLM Supervisor is distributed as the PyPI package
`claude-glm52-supervisor`. The package installs the `claude-glm52`,
`claude-glm52-delegate`, `claude-glm52-batch`, `claude-glm52-subagent`, and
`claude-glm52-reviewer` commands.

## Recommended Install

Install with `uv`:

```bash
uv tool install claude-glm52-supervisor
claude-glm52 doctor --offline
claude-glm52 setup --print
```

Run without a persistent install:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

Upgrade:

```bash
uv tool upgrade claude-glm52-supervisor
```

Uninstall:

```bash
uv tool uninstall claude-glm52-supervisor
```

## Release Installer

The GitHub Release installer is useful when you want a direct release-asset
download. It downloads the release archive, verifies `checksums.txt`, installs
to a user prefix, and runs `doctor --offline`.

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

Dry run:

```bash
bash claude-glm52-installer.sh --dry-run
```

## Codex-Assisted Setup

For the lowest-friction setup, open a new Codex session and paste the prompt in
[`codex-setup-prompt.md`](codex-setup-prompt.md). It asks Codex to install the
package, verify local tools, avoid secrets, run doctor checks, and leave a short
setup report.

## Required Runtime Tools

| Tool | Purpose | Check |
| --- | --- | --- |
| Python 3.11+ | package runtime | `python3 --version` |
| Bash | shell wrappers | `bash --version` |
| Git | repository inspection | `git --version` |
| Claude Code | worker runtime | `claude --version` |
| CLIProxyAPI | local gateway to GLM-5.2 | `cliproxyapi --help` |
| Z.AI GLM-5.2 access | upstream model | configured locally, never committed |

Recommended:

| Tool | Purpose | Check |
| --- | --- | --- |
| `uv` | install and upgrade | `uv --version` |
| `rg` | faster search | `rg --version` |
| GNU `timeout` | runaway-task guard | `timeout --version` |

## Worker Config Directory

Use a dedicated Claude Code worker config directory so this route stays
separate from a normal Claude Code profile:

```bash
export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
mkdir -p "$CLAUDE_GLM52_WORKER_CONFIG_DIR"
```

Add the `export` line to a shell profile only after confirming that this is the
desired default for future terminal sessions.

## CLIProxyAPI Routing

Run CLIProxyAPI locally and expose an Anthropic-compatible endpoint such as:

```text
http://127.0.0.1:8317
```

Configure the Claude Code-visible model alias to route to GLM-5.2:

```text
claude-opus-4-6[1m] -> glm-5.2
```

Keep API keys and provider credentials in local environment or provider config.
Do not paste secret values into task packets, logs, GitHub issues, or commits.

## Verification

Start with offline checks:

```bash
claude-glm52 --version
claude-glm52 paths
claude-glm52 doctor --offline
```

Then run online checks after Claude Code and CLIProxyAPI are ready:

```bash
claude-glm52 doctor
```

Finally, run a no-edit smoke test:

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

## Troubleshooting

- `uvx` cannot find `claude-glm52-supervisor`
  - Confirm network access to PyPI and check the package name spelling.
- `claude-glm52 doctor --offline` returns `FAIL`
  - Reinstall or upgrade the package, then run the command again.
- `claude-glm52 doctor` warns about `tool:claude`
  - Install and authenticate Claude Code.
- `claude-glm52 doctor` warns about `tool:cliproxyapi`
  - Install and start CLIProxyAPI.
- `claude-glm52 doctor` warns about `tool:timeout`
  - Install GNU coreutils if a hard runtime ceiling is required.
- The no-edit smoke test fails
  - Confirm Claude Code is authenticated, CLIProxyAPI is running, and the model
    alias routes to GLM-5.2.
