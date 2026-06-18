![Codex controls Claude Code](docs/assets/codex-controls-claude-code.png)

<p align="center">
  <a href="./README.md"><img alt="Language English" src="https://img.shields.io/badge/Language-English-2f6feb?style=for-the-badge"></a>
  <a href="./README.ja.md"><img alt="Read in Japanese" src="https://img.shields.io/badge/Read%20in-%E6%97%A5%E6%9C%AC%E8%AA%9E-f97316?style=for-the-badge"></a>
  <img alt="Version v0.0.2" src="https://img.shields.io/badge/Version-v0.0.2-111827?style=for-the-badge">
</p>

# ClaudeCodeGLM Supervisor

ClaudeCodeGLM Supervisor lets Codex delegate bounded implementation and review work to Claude Code while routing Claude Code to Z.AI GLM-5.2.

Codex stays responsible for planning, task design, risk control, validation, and final acceptance. Claude Code GLM-5.2 acts as a constrained worker that edits only the requested files, runs the requested checks, and returns a compact machine-readable result for Codex to audit.

This repository is useful when you want the lower-cost GLM execution path for longer coding work without giving up Codex as the operator and final reviewer.

## Current Status

| Layer | Current route |
| --- | --- |
| Orchestrator | Codex |
| Worker runtime | Claude Code |
| Provider gateway | CLIProxyAPI |
| Upstream model | Z.AI GLM-5.2 |
| Claude Code-visible model | `claude-opus-4-6[1m]` alias |
| Verified context window | 1,000,000 tokens |
| Verified Claude Code output ceiling | 64,000 tokens |
| Vision handling | Separate Z.AI Vision MCP/OCR preflight, then text context injection |

GLM-5.2 can support larger outputs at the model/API layer, but this Claude Code worker route is currently verified to a 64K Claude Code output ceiling. For true 128K single-response output, use a separately verified direct GLM-5.2 route instead of assuming Claude Code will expose it safely.

## How It Is Meant To Be Used

The intended workflow is:

1. Codex reads the repo, creates the plan, and decides whether delegation is worthwhile.
2. Codex writes a precise task packet with allowed files, constraints, acceptance criteria, and validation commands.
3. ClaudeCodeGLM Supervisor sends that packet to Claude Code GLM-5.2.
4. Claude Code edits or reviews within the requested scope.
5. Codex inspects the wrapper JSON, checks the diff, reruns validation, and either accepts, fixes, or sends a narrower retry.

Short trigger phrases are fine. In day-to-day use, requests such as `implement with CCG`, `use CCG for implementation`, or `delegate implementation to ClaudeCodeGLM` should mean:

- Codex plans first.
- CCG does bounded implementation.
- Codex audits and reports the final result.

Do not treat CCG as a blind autopilot. Planning, product judgment, high-risk decisions, and final acceptance stay with Codex.

## Why CLIProxyAPI Is Used

This route uses CLIProxyAPI between Claude Code and Z.AI because it provides the practical glue that makes the worker route stable:

- It exposes Claude Code-compatible model names while routing upstream to GLM-5.2.
- It lets Claude Code see a model metadata shape that currently gives the verified 1M context and 64K output ceiling.
- It supports local routing, aliases, retries, and multi-key/provider setups in one place.
- It keeps Claude Code configuration cleaner than repeatedly patching ad hoc endpoint settings.

Thanks to the CLIProxyAPI author and maintainers. This project depends on that gateway layer for the current recommended setup.

Can this work without CLIProxyAPI? Not as the recommended route. A direct Claude Code to Z.AI Anthropic-compatible endpoint may be possible in some environments, but this repository's verified worker path relies on CLIProxyAPI aliases and metadata behavior. Without CLIProxyAPI you should expect extra setup work and reduced/untested guarantees around model aliases, output ceiling, retries, quota capture, and provider routing.

## Install

The primary install path is the published PyPI package. Custom Homebrew taps
are not part of the normal user path.

For a one-off check without a persistent install:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

For persistent CLI commands on `PATH`:

```bash
uv tool install claude-glm52-supervisor

claude-glm52 setup --print
```

If you prefer an inspectable release-asset flow, use the checksum-verifying
GitHub Release installer:

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/checksums.txt
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-supervisor-0.0.2.tar.gz
shasum -a 256 -c checksums.txt
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

For source checkout development, run the compatibility wrappers directly:

```bash
python3 outputs/claude-glm52.py --version
python3 outputs/claude-glm52.py doctor --offline
python3 outputs/claude-glm52-delegate.py --help
```

Full rationale and release-channel tradeoffs live in
[`docs/install.md`](docs/install.md) and
[`docs/distribution-strategy.md`](docs/distribution-strategy.md). The
Homebrew tap skeleton remains in [`packaging/homebrew-tap/`](packaging/homebrew-tap/)
for maintainer validation only.

## Requirements

Required:

- macOS or Linux shell environment.
- Python 3.11 or newer.
- Bash.
- Claude Code CLI installed and authenticated.
- CLIProxyAPI installed and running locally.
- A Z.AI account/API key with GLM-5.2 access.
- `npx` available if you use the default Vision MCP backend.

Recommended:

- `timeout` from GNU coreutils or Homebrew coreutils for process guard enforcement.
- `git`, `rg`, and a normal test runner for the target repo.
- A dedicated Claude Code worker config directory, defaulting to `~/.claude-glm52-worker`.

Sensitive values are read from environment/config at runtime. Do not commit API keys, `.env` files, auth tokens, provider configs, or local LaunchAgent files.

## Setup Overview

1. Install and configure CLIProxyAPI for a local Anthropic-compatible endpoint such as:

   ```text
   http://127.0.0.1:8317
   ```

2. Configure CLIProxyAPI so Claude Code-visible aliases such as `claude-opus-4-6[1m]` route upstream to `glm-5.2`.

3. Configure a lean Claude Code worker profile, usually:

   ```bash
   export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
   ```

4. Install the supervisor package, or call the source-checkout compatibility
   wrappers while developing this repository:

   ```bash
   uv tool install claude-glm52-supervisor
   claude-glm52-delegate --help
   claude-glm52-batch --help
   ```

   Source checkout form:

   ```bash
   python3 outputs/claude-glm52-delegate.py --help
   python3 outputs/claude-glm52-batch.py --help
   ```

5. Run a lightweight smoke test before longer delegation:

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

The GLM-5.2 coding worker is treated as text-only. Image files are analyzed first through a lightweight Z.AI Vision MCP/OCR preflight. The extracted evidence text is injected into the task packet, while raw image summaries are not persisted in result JSON or usage logs.

## Task Packet Shape

Use concise English packets unless the task itself is about Japanese text.

```text
Role: implementation worker
Goal:
Repo/CWD:
Files likely relevant:
Constraints:
Acceptance criteria:
Validation commands:
Do not:
Return:
```

Important constraints:

- List the files the worker may modify.
- Forbid broad searches from `/` or `~`.
- Forbid deleting files, editing secrets, committing, pushing, and changing auth/config.
- Include the exact validation command when possible.
- Keep final worker output short; large deliverables should be written to files.

## Usage And Quota Accounting

Delegate results include:

- `usageSummary`: Claude Code model token and cost totals.
- `usage_snapshots.before` and `usage_snapshots.after`: before/after provider usage snapshots.
- `usage_accounting.tokens_*`: ZCode-compatible token fields.
- `usage_accounting.quota_percent_*`: quota percent fields when a delta-safe source exists.

Quota percentage is deliberately conservative. If the provider only returns a percentage without usable usage/remaining counts, the result is marked `unavailable` with a reason instead of pretending the task used `0%`.

## Token Savings Evidence

In one eight-task benchmark covering a website, mini-game, backend reconciliation, policy routing, and four vision/OCR tasks, all three routes passed validation with average quality 10/10:

| Route | Reported tokens | Wall time | Strong passes |
| --- | ---: | ---: | ---: |
| ClaudeCodeGLM | 1,000,148 | 1984.2s | 8/8 |
| ZCode | 1,037,882 | 2236.4s | 8/8 |
| Codex self | 5,020,951 | 1877.8s | 8/8 |

This does not mean work is free. It shifts the execution burden from Codex/GPT tokens to GLM tokens. The result does suggest that, for longer scoped tasks, detailed delegation can preserve quality while substantially reducing Codex-side token use. Treat this as benchmark evidence, not a universal guarantee.

## Key Files

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | PyPI/uv package metadata and console scripts |
| `src/claude_glm52_supervisor/` | Importable package implementation |
| `outputs/claude-glm52-delegate.py` | Source-checkout compatibility shim |
| `outputs/claude-glm52-batch.py` | Source-checkout compatibility shim |
| `outputs/claude-glm52-subagent.sh` | Raw Claude Code worker runner |
| `packaging/install/claude-glm52-installer.sh` | Checksum-verifying GitHub Release installer |
| `.github/workflows/release.yml` | Trusted Publishing and draft release asset workflow |
| `scripts/build_public_snapshot.py` | Clean public snapshot builder for repo handoff |
| `scripts/stage_public_repo.py` | Local public repo commit/tag/release-asset staging |
| `tests/` | Unit tests for install CLI, usage, vision, and process cleanup helpers |

## Validation

```bash
bash -n outputs/claude-glm52-subagent.sh
bash -n packaging/install/claude-glm52-installer.sh packaging/release/build-release-assets.sh
python3 -m py_compile src/claude_glm52_supervisor/*.py outputs/*.py
python3 -m unittest discover -s tests -v
uv build --out-dir /tmp/claude-glm52-dist
```

## Security Notes

- Never commit `.env`, API keys, auth tokens, private keys, local provider configs, or shell history.
- Do not log prompt text when it may contain secrets.
- Treat worker output as evidence, not truth.
- Keep Codex as final auditor.
- Keep image/OCR context sanitized and do not persist raw extracted text unless explicitly needed.

## License

See [`LICENSE`](LICENSE). The current notice is intentionally conservative and
does not grant open-source reuse or redistribution rights. If a standard public
license is selected later, this section and package metadata should be updated
together.
