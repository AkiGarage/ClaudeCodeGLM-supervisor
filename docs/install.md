# Installation

ClaudeCodeGLM Supervisor is moving away from custom Homebrew taps as a normal
user install path. The primary public distribution is now the PyPI package for
`uvx` and `uv tool install`, with a checksum-verified GitHub Release installer
as an inspectable fallback.

> **Status:** version `0.0.2` is published on PyPI as
> `claude-glm52-supervisor`, the `v0.0.2` GitHub Release is public, and the
> release assets have been verified through `releases/latest/download`. The
> Homebrew tap remains a maintainer/legacy validation route, not the default
> user path.
> See [`distribution-strategy.md`](distribution-strategy.md).

## Recommended channels

| Channel | Status | Default? | Notes |
| --- | --- | --- | --- |
| PyPI + `uvx` | Published as `claude-glm52-supervisor` | Yes | One-off isolated execution without a repo clone. |
| PyPI + `uv tool install` | Published as `claude-glm52-supervisor` | Yes | Isolated env with commands on `PATH`. |
| GitHub Release installer | Published release assets | Yes | Download, verify checksum, install to user prefix. |
| Manual release tarball | Fallback | Yes | Most inspectable, more manual. |
| Homebrew tap | Validated skeleton | No | Keep for maintainer E2E/legacy convenience only. |
| npm/pnpm | Not planned | No | Wrong ecosystem for these stdlib Python wrappers. |

## Safety and maintenance model

- Installers and package entry points must never write API keys, `.env`, auth
  tokens, provider configs, or shell history.
- They must never edit `~/.claude` or `~/.claude-glm52-worker` without an
  explicit future `--apply` style command.
- They must never install or start Claude Code or CLIProxyAPI.
- Post-install validation should run only offline `--version`, `paths`,
  `doctor --offline`, and `setup --print`. No network, no Claude Code, no
  secrets.

## Recommended PyPI/uv use

Use `uvx` for disposable one-off checks:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

Use `uv tool install` when `claude-glm52` should stay available on `PATH`:

```bash
uv tool install claude-glm52-supervisor

claude-glm52 setup --print
```

`doctor --offline` checks only installed package files and never reaches Claude
Code, CLIProxyAPI, the network, or secret-bearing config.

## GitHub Release installer

Use this when you want to inspect and verify the exact release assets before
installing:

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/checksums.txt
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-supervisor-0.0.2.tar.gz
shasum -a 256 -c checksums.txt
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

## Source-checkout development

For development from this repository, run the compatibility wrappers directly:

```bash
python3 outputs/claude-glm52.py --version
python3 outputs/claude-glm52.py doctor --offline
python3 outputs/claude-glm52-delegate.py --help
```

Future package release requirements:

- keep `LICENSE` included in package metadata
- choose and declare an SPDX license only if broader reuse rights are intended
- keep `pyproject.toml`, `src/`, and `[project.scripts]` validated
- configure a PyPI pending Trusted Publisher for repository
  `AkiGarage/ClaudeCodeGLM-supervisor`, workflow `release.yml`, environment
  `pypi`
- publish through PyPI Trusted Publishing from GitHub Actions; do not create a
  long-lived PyPI token
- validate both wheel and sdist before announcing `uvx`

## Homebrew tap layout

The Homebrew tap remains in the repo as a maintainer validation target, not as
the normal user install path.

```text
packaging/homebrew-tap/
  Formula/
    claude-glm52.rb        # formula; version/sha256 come from public source releases
  README.md                # tap usage and release checklist
```

Wrappers installed on `PATH` (each wrapper is `chmod 0555`, so the source
tarball does not need to preserve the executable bit):

| Binary | Backing file | Runtime |
| --- | --- | --- |
| `claude-glm52` | `outputs/claude-glm52.py` | `python@3.11` |
| `claude-glm52-delegate` | `outputs/claude-glm52-delegate.py` | `python@3.11` |
| `claude-glm52-batch` | `outputs/claude-glm52-batch.py` | `python@3.11` |
| `claude-glm52-subagent` | `outputs/claude-glm52-subagent.sh` | `/bin/bash` |
| `claude-glm52-reviewer` | `outputs/claude-glm52-reviewer.sh` | `/bin/bash` |

The formula defines a `head` stanza pointing at the **remote `main`** branch of
the clean public source repository. `--HEAD` installs therefore build from that
public remote `main`; they do not install this private development tree or any
uncommitted local tree. Current Homebrew rejects direct formula paths, so
`audit` and `install` must target a tap.

For a true pre-release Homebrew E2E of local, unpushed code, maintainers should
build a source tarball from the checkout, copy the formula into a temporary tap,
replace the formula's `url` with that `file://` tarball and its real `sha256`,
then run `brew install --build-from-source <tap>/claude-glm52` and `brew test`.
Do not use `--HEAD` for that local-tarball E2E; `--HEAD` is for remote `main`.

### Optional runtime dependencies

These are **not** installed by the formula. Bring them yourself:

```bash
brew install --cask claude-code     # Claude Code worker CLI
brew install ripgrep                # speeds up read-only review
brew install coreutils              # provides `timeout` for safety ceiling
```

Install and run CLIProxyAPI from its official release, then point Claude Code
at the verified local endpoint (`http://127.0.0.1:8317` by default) and alias
`claude-opus-4-6[1m] -> glm-5.2`.

## Release checklist for future versions

For the clean public repository:

1. Keep the current `LICENSE`, or explicitly replace it with the selected
   standard license before broad public reuse is advertised.
2. Build and inspect a clean snapshot:

   ```bash
   python3 scripts/build_public_snapshot.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --replace
   python3 scripts/stage_public_repo.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --version v0.0.2 --replace
   ```

3. Run `python3 scripts/public_audit.py` in the private checkout and audit the
   snapshot with `--all-files` if it is not a git repository yet.
4. Run the focused Python checks and unit tests.
5. Create or push only the staged clean snapshot/tag, not private development
   history. The tag passed to `stage_public_repo.py` must match
   `pyproject.toml`'s `[project].version`.

For the GitHub Release installer:

1. Build release assets from the clean public repo:

   ```bash
   packaging/release/build-release-assets.sh --version vX.Y.Z --out-dir dist/release
   ```

2. Publish `claude-glm52-supervisor-X.Y.Z.tar.gz`,
   `claude-glm52-installer.sh`, and `checksums.txt` as GitHub Release assets.
3. Sign the release assets if `cosign`, `minisign`, or GPG has been selected.
4. Validate the installer without touching global auth/config:

   ```bash
   sh -n packaging/install/claude-glm52-installer.sh
   shellcheck packaging/install/claude-glm52-installer.sh
   bash packaging/install/claude-glm52-installer.sh --dry-run
   ```

For PyPI/uvx:

1. Verify `pyproject.toml` and `[project.scripts]` entries.
2. Build both wheel and sdist.
3. In PyPI, create a pending Trusted Publisher with:
   - project: `claude-glm52-supervisor`
   - owner: `AkiGarage`
   - repository: `ClaudeCodeGLM-supervisor`
   - workflow: `release.yml`
   - environment: `pypi`
4. Publish with PyPI Trusted Publishing from GitHub Actions, not a long-lived
   PyPI token. The release workflow intentionally publishes to PyPI only from
   manual `workflow_dispatch` with `publish_pypi=true`; tag pushes build draft
   GitHub Release assets but do not publish to PyPI.
5. Verify the package locally before publishing:

   ```bash
   uvx --from dist/*.whl claude-glm52 doctor --offline
   uv tool install --force dist/*.whl
   claude-glm52 doctor --offline
   ```

For Homebrew, if it remains published, treat it as optional maintainer
validation only:

```bash
ruby -c packaging/homebrew-tap/Formula/claude-glm52.rb
brew audit --strict --formula AkiGarage/homebrew-tap/claude-glm52
brew install --build-from-source AkiGarage/homebrew-tap/claude-glm52
brew test claude-glm52
```

## Troubleshooting

- `claude-glm52 doctor --offline` returns nonzero
  - One of the required runtime files is missing. Reinstall from the release
    asset, reinstall the PyPI tool, or refresh the source checkout.
- `claude-glm52 doctor` (online) warns about `tool:claude`
  - Install Claude Code: `brew install --cask claude-code`.
- `claude-glm52 doctor` warns about `tool:cliproxyapi`
  - Install CLIProxyAPI from its official release and run it before delegating.
- `claude-glm52 doctor` warns about `tool:timeout`
  - `brew install coreutils` for the runaway-task safety ceiling.
- A wrapper reports `runtime guard requested but no timeout binary was found`
  - Same fix: install `coreutils`, or run without the safety ceiling only on a
    machine you trust.
- Release installer fails with a checksum mismatch
  - The downloaded asset does not match `checksums.txt`. Stop, delete the
    downloaded files, and verify the release assets before retrying.
- `uvx` cannot find `claude-glm52-supervisor`
  - Confirm `uv` can reach PyPI and that the package name is exactly
    `claude-glm52-supervisor`.
- You only see WARN lines and nothing else
  - That is expected on machines that have not yet installed Claude Code or
    CLIProxyAPI. WARNs are deferrable; FAILs are not.
