# ClaudeCodeGLM Supervisor Homebrew Tap

This directory is the Homebrew tap source for **ClaudeCodeGLM Supervisor**.
It lets `brew install` place the repo's Python and shell wrappers on `PATH`
without npm, pip, pnpm, or uvx.

Public installs fetch from the clean public source snapshot repository
`AkiGarage/claude-glm52`, not from the private development repository. This
keeps private git history, local workspaces, and generated evaluation artifacts
out of the public archive. The formula uses `license :cannot_represent`
because the current `LICENSE` is a conservative rights-reserved notice, not a
standard SPDX expression.

The tap is intentionally minimal:

- `Formula/claude-glm52.rb` installs repo files under Homebrew's `libexec`
  and creates executable `bin/` wrappers for the umbrella CLI plus the four
  primary worker entry points.
- Each Python wrapper execs Homebrew's `python@3.11`; each shell wrapper
  execs `/bin/bash`. The source tarball does not need to preserve the
  executable bit on `.py` / `.sh` files.
- The formula never writes secrets, never edits Claude Code global config,
  and never starts CLIProxyAPI.
- `brew test claude-glm52` only runs offline help/version/doctor commands.

## Layout

```text
packaging/homebrew-tap/
  Formula/
    claude-glm52.rb      # formula
  README.md              # this file
```

Wrappers installed by the formula:

| Binary | Backing file | Runtime |
| --- | --- | --- |
| `claude-glm52` | `outputs/claude-glm52.py` | `python@3.11` |
| `claude-glm52-delegate` | `outputs/claude-glm52-delegate.py` | `python@3.11` |
| `claude-glm52-batch` | `outputs/claude-glm52-batch.py` | `python@3.11` |
| `claude-glm52-subagent` | `outputs/claude-glm52-subagent.sh` | `/bin/bash` |
| `claude-glm52-reviewer` | `outputs/claude-glm52-reviewer.sh` | `/bin/bash` |

## Local and pre-release checks

The formula defines a `head` stanza pointing at the **remote `main`** branch
of the clean public source repository, so a true `--HEAD` build reflects that
public remote `main` head (post-push), not this private development tree or the
uncommitted local tree. Current Homebrew rejects direct formula paths, so a
true `--HEAD` install must target a tap.

For quick checks of an uninstalled source checkout, run the wrappers directly
from the repository root:

```bash
python3 outputs/claude-glm52.py --version
python3 outputs/claude-glm52.py doctor --offline
python3 outputs/claude-glm52-delegate.py --help
```

For a true pre-release Homebrew E2E of local, unpushed code, build a source
tarball from the checkout, copy the formula into a temporary tap, replace the
formula's `url` with that `file://` tarball and its real `sha256`, then run
`brew install --build-from-source <tap>/claude-glm52` and `brew test`. Do not use `--HEAD` for local tarball E2E; `--HEAD` is for remote `main`.

```bash
brew tap AkiGarage/homebrew-tap https://github.com/AkiGarage/homebrew-tap
brew install claude-glm52
brew test claude-glm52
```

## Release checklist

For each release, the flow must:

1. If the license changes to MIT, Apache-2.0, or another standard SPDX
   expression, update the formula's `license` field from `:cannot_represent`
   to the matching expression.
2. Export a clean public source snapshot without private history or local work
   artifacts, then tag it, e.g. `v0.0.3`.
3. Publish a GitHub Release for that public source tag with an auto-generated
   source tarball.
4. Download the tarball and compute its `sha256`.
5. Overwrite `url`, `version`, and `sha256` in
   `packaging/homebrew-tap/Formula/claude-glm52.rb`.
6. Publish (or update) the tap repository, then run `ruby -c Formula/claude-glm52.rb`
   and, where available, validate against the tap-qualified target. Direct
   formula paths are rejected by current Homebrew, so use the tap/name form:

   ```bash
   brew tap AkiGarage/homebrew-tap
   brew audit --strict --formula AkiGarage/homebrew-tap/claude-glm52
   brew install --build-from-source AkiGarage/homebrew-tap/claude-glm52
   brew test claude-glm52
   ```
7. Commit the updated formula and push the tap.

## What the formula deliberately does NOT do

- It does not install Claude Code (use `brew install --cask claude-code`).
- It does not install or start CLIProxyAPI.
- It does not write Z.AI API keys, `.env`, auth tokens, or provider configs.
- It does not mutate `~/.claude` or `~/.claude-glm52-worker`.
- It does not run `claude` or `cliproxyapi` at install or test time.

Use `claude-glm52 doctor` after install to see which optional runtime pieces
are still missing on your machine.
