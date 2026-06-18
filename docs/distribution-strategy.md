# Distribution Strategy

## Recommendation

Make the public repository `AkiGarage/ClaudeCodeGLM-supervisor` the canonical
source. Do not make a custom Homebrew tap part of the normal user path.

Recommended distribution order:

1. **GitHub Release installer** for the first public release, because it can
   work before the project is packaged for PyPI.
2. **PyPI package with `uvx` / `uv tool install`** as the best long-term
   developer UX once the package is published.
3. **Manual release tarball** as the inspectable fallback.
4. **Homebrew tap** only for maintainer E2E/legacy convenience, not default
   installation docs.

Primary release-installer flow:

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/checksums.txt
shasum -a 256 -c checksums.txt --ignore-missing
bash claude-glm52-installer.sh --prefix "$HOME/.local"

claude-glm52 doctor --offline
claude-glm52 setup --print
```

The docs can also show a shorter one-liner for experienced users, but the
download-verify-run path should be the default because it is easier to audit
and safer to copy into a security-sensitive setup guide.

Target PyPI/uv flow after PyPI publication:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
uv tool install claude-glm52-supervisor

claude-glm52 setup --print
```

Use `uvx` for one-off checks and `uv tool install` when the wrappers should be
available on `PATH`.

## Why not use Homebrew tap by default

The current Homebrew path is technically sound: pinned release tarball,
`sha256`, offline `brew test`, no secret writes, no global Claude config
mutation. The issue is user experience. A custom tap makes the install feel
like "add this package registry first, then install the thing," which is more
mental overhead than this tool needs.

Homebrew can remain useful for maintainer E2E and legacy users who already
installed the tap, but new user-facing docs should not ask people to tap a
custom registry.

## Install channels

| Channel | Role | User command shape | Safety model | Tradeoff |
| --- | --- | --- | --- | --- |
| GitHub Release installer | Primary now | download installer, verify checksum, run | versioned asset, checksum, optional signature, no secrets, user prefix | Need to maintain a tiny installer script |
| PyPI + `uvx` | Preferred long-term CLI UX | `uvx --from claude-glm52-supervisor claude-glm52 ...` | isolated disposable tool env, PyPI metadata, no repo clone | Requires PyPI release hygiene |
| PyPI + `uv tool install` | Persistent CLI install | `uv tool install claude-glm52-supervisor` | isolated persistent tool env, easy upgrade/uninstall | Requires `uv`; cache/env behavior must be documented |
| `pipx` | Acceptable fallback | `pipx install claude-glm52-supervisor` | isolated persistent env | Slower/less current than uv for this audience |
| `pip install --user` | Not primary | `python3 -m pip install --user ...` | familiar Python path | More likely to pollute user Python env and PATH |
| Manual tarball | Fallback | download tarball, extract, add wrappers to PATH | most inspectable | More manual steps |
| macOS `.pkg` | Later | download signed package | can be signed/notarized | More maintenance and Apple signing overhead |
| Homebrew tap | Maintainer/legacy only | `brew tap ...`, `brew install ...` | formula audit, `sha256`, offline tests | Tap ceremony feels clunky; avoid as default |
| npm/pnpm | Not recommended | global JS package install | familiar to some users | Wrong ecosystem for stdlib Python wrappers |

## PyPI and uvx assessment

`uvx` is a strong fit after packaging because it runs Python CLI tools in an
isolated temporary environment, while `uv tool install` creates a persistent
isolated tool environment with executables on `PATH`. This matches the desired
trust boundary: the installer should place wrappers, then `doctor` and
`setup --print` should guide the user without mutating auth/config.

Required work before PyPI publication:

1. Keep `LICENSE` included in package metadata. Replace the current
   rights-reserved notice with an SPDX license only if broader reuse rights are
   intended.
2. Keep command names stable through `[project.scripts]`:

   ```toml
   [project.scripts]
   claude-glm52 = "claude_glm52_supervisor.cli:main"
   claude-glm52-delegate = "claude_glm52_supervisor.delegate:main"
   claude-glm52-batch = "claude_glm52_supervisor.batch:main"
   ```

3. Keep shell script entry points behind Python console scripts where
   practical, or include the shell scripts as package data and resolve them
   with `importlib.resources`.
4. Publish through PyPI Trusted Publishing from GitHub Actions so no long-lived
   PyPI token is stored in the repo or Actions secrets. The PyPI pending
   publisher should use project `claude-glm52-supervisor`, owner `AkiGarage`,
   repository `ClaudeCodeGLM-supervisor`, workflow `release.yml`, and
   environment `pypi`. The workflow publishes to PyPI only from manual
   `workflow_dispatch` with `publish_pypi=true`; tag pushes are limited to
   validation, distribution build, and draft GitHub Release assets.
5. Build and test both wheel and sdist, then verify:

   ```bash
   uvx --from dist/*.whl claude-glm52 doctor --offline
   uv tool install --force dist/*.whl
   claude-glm52 doctor --offline
   ```

Avoid claiming public `uvx` support until the PyPI release exists. Local wheel
verification can use the `uvx --from dist/*.whl ...` form before publication.

References checked:

- uv tools: https://docs.astral.sh/uv/concepts/tools/
- uv installation: https://docs.astral.sh/uv/getting-started/installation/
- PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/using-a-publisher/
- PyPA entry points: https://packaging.python.org/en/latest/specifications/entry-points/

## Release installer design

The committed installer lives at
`packaging/install/claude-glm52-installer.sh`. It should stay small enough to
read quickly and should do only deterministic file operations:

1. Resolve version: explicit `--version vX.Y.Z` or GitHub latest release.
2. Download release tarball and `checksums.txt`.
3. Verify `sha256` before extraction.
4. Extract to:

   ```text
   ~/.local/share/claude-glm52/<version>/
   ```

5. Create or update wrappers in:

   ```text
   ~/.local/bin/
   ```

6. Print PATH guidance if `~/.local/bin` is not on PATH.
7. Run `claude-glm52 doctor --offline`.
8. Print `claude-glm52 setup --print` as the next step.

It must not:

- read or print API keys, tokens, `.env`, or provider config files
- edit `~/.claude`, `~/.claude-glm52-worker`, shell profiles, or global auth
  without an explicit flag
- install or start Claude Code or CLIProxyAPI
- run networked checks during post-install validation
- silently replace a newer installed version

Useful flags:

```bash
claude-glm52-installer.sh --prefix "$HOME/.local"
claude-glm52-installer.sh --version v0.1.0 --prefix "$HOME/.local"
claude-glm52-installer.sh --dry-run
claude-glm52-installer.sh --uninstall
```

Release assets are built by:

```bash
packaging/release/build-release-assets.sh --version v0.1.0 --out-dir dist/release
```

That script creates the release tarball, copies the installer, and writes
`checksums.txt`. Signing remains optional until `cosign`, `minisign`, or GPG is
selected.

## Setup experience

Keep setup safe by default:

- `claude-glm52 doctor --offline`: proves installed files and local wrappers.
- `claude-glm52 doctor`: warns about missing optional runtime tools.
- `claude-glm52 setup --print`: prints manual steps without mutation.
- Future `setup --apply --scope repo`: may write only repo-local routing files
  after showing a diff or dry-run summary.

This preserves the "Codex orchestrates, GLM worker executes bounded packets"
trust boundary and avoids installer-driven auth/config mutation.

## Public repository plan

The final public repo name should be:

```text
AkiGarage/ClaudeCodeGLM-supervisor
```

Because the current private repo already occupies that name, use a two-repo
handoff:

1. Freeze the private repo and run validation in the private checkout.
2. Rename the private repo to a development name, for example:

   ```text
   AkiGarage/ClaudeCodeGLM-supervisor-dev
   ```

3. Update the private checkout's `origin` to the renamed private repo.
4. Create a new public repo named:

   ```text
   AkiGarage/ClaudeCodeGLM-supervisor
   ```

5. Populate it from a clean snapshot, not private git history.
6. Publish the first public release from that clean repo.
7. Point installer, docs, and optional Homebrew formula at the public release
   assets.
8. Archive or redirect the earlier `AkiGarage/claude-glm52` public snapshot
   after the new repo is validated.

## Clean snapshot contents

Build the local snapshot with:

```bash
python3 scripts/build_public_snapshot.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --replace
python3 scripts/public_audit.py --root /tmp/ClaudeCodeGLM-supervisor-public --all-files
```

Stage it as a local git repository before touching GitHub:

```bash
python3 scripts/stage_public_repo.py \
  --out-dir /tmp/ClaudeCodeGLM-supervisor-public \
  --version v0.0.2 \
  --replace
```

That command initializes a local `main` branch, creates one snapshot commit,
tags it, runs audit/tests/package build, and writes release assets outside the
repo directory. It does not add a remote, push, create GitHub repositories, or
publish to PyPI. The tag must match `pyproject.toml`'s `[project].version`;
bump package metadata first if the public release should use a newer tag.

The snapshot builder uses an explicit allowlist and rejects generated/private
paths such as `CONTINUITY.md`, `HANDOFF.md`, `logs/`, `work/`, `artifacts/`,
`.env*`, `.git/`, build outputs, and Python cache files.

Include:

- `README.md`
- `README.ja.md`
- `SECURITY.md`
- `LICENSE`
- `docs/`
- `.github/workflows/release.yml`
- `outputs/`
- `src/`
- `pyproject.toml`
- `packaging/install/`
- `packaging/release/`
- `scripts/public_audit.py`
- `tests/`
- `packaging/homebrew-tap/` if Homebrew remains supported

Exclude:

- `.git/`
- `.env*`, secrets, tokens, private auth/config
- `CONTINUITY.md`
- `HANDOFF.md`
- `work/`
- `logs/`
- `artifacts/`
- private evaluation outputs
- generated caches
- local machine paths not needed by users

## Release gates

Before the public repo or release is advertised:

```bash
python3 scripts/public_audit.py
python3 scripts/build_public_snapshot.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --replace
python3 scripts/stage_public_repo.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --version v0.0.2 --replace
python3 scripts/public_audit.py --root /tmp/ClaudeCodeGLM-supervisor-public --all-files
python3 -m py_compile src/claude_glm52_supervisor/*.py outputs/*.py
python3 -m unittest discover -s tests -v
uv build --out-dir /tmp/claude-glm52-dist
```

For the installer:

```bash
sh -n packaging/install/claude-glm52-installer.sh
sh -n packaging/release/build-release-assets.sh
shellcheck packaging/install/claude-glm52-installer.sh
packaging/install/claude-glm52-installer.sh --dry-run --version v0.1.0 --prefix /tmp/claude-glm52-test
packaging/release/build-release-assets.sh --dry-run --version v0.1.0 --out-dir /tmp/claude-glm52-release
```

For Homebrew, if still published:

```bash
ruby -c packaging/homebrew-tap/Formula/claude-glm52.rb
brew audit --strict --formula AkiGarage/homebrew-tap/claude-glm52
brew install --build-from-source AkiGarage/homebrew-tap/claude-glm52
brew test claude-glm52
```

`shellcheck` and Homebrew validation are environment-dependent; record them as
skipped with a reason if unavailable.

## Open decisions

- License: decide whether to keep the conservative rights-reserved notice or
  replace it with MIT, Apache-2.0, or another standard SPDX license before
  broad public reuse is advertised.
- Signature: choose `cosign`, `minisign`, or GPG for release asset signatures.
- Installer host: prefer release asset downloads over `raw.githubusercontent`
  for versioned installs.
- Legacy repo: decide whether `AkiGarage/claude-glm52` is archived, redirected,
  or kept as a compatibility snapshot.
