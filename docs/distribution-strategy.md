# Install Channels

ClaudeCodeGLM Supervisor supports a small set of install paths. Most users
should use the PyPI package through `uv`.

## Recommended: PyPI With uv

Use `uv tool install` for a persistent CLI install:

```bash
uv tool install claude-glm52-supervisor
claude-glm52 doctor --offline
```

Use `uvx` for one-off checks:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

Why this is recommended:

- no repository clone is required
- commands are installed into an isolated tool environment
- upgrades are straightforward with `uv tool upgrade`
- the package metadata points back to the public GitHub repository

## Release Installer

Use the GitHub Release installer when you want a direct release-asset install:

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

The installer verifies the downloaded archive against `checksums.txt` before
extracting files. It does not install Claude Code, start CLIProxyAPI, or write
provider credentials.

## Source Checkout

Use a source checkout when developing the project or inspecting the wrappers:

```bash
git clone https://github.com/AkiGarage/ClaudeCodeGLM-supervisor.git
cd ClaudeCodeGLM-supervisor
python3 outputs/claude-glm52.py doctor --offline
python3 outputs/claude-glm52-delegate.py --help
```

## pipx

`pipx` is an acceptable fallback if `uv` is unavailable:

```bash
pipx install claude-glm52-supervisor
claude-glm52 doctor --offline
```

## pip install --user

`pip install --user` works, but it is not the preferred path because it can be
easier to mix tool dependencies with a user's normal Python environment.

```bash
python3 -m pip install --user claude-glm52-supervisor
```

## Homebrew

Homebrew is not the recommended install path for this project. The CLI is a
Python package, and `uv` gives a simpler install and upgrade flow without
asking users to add a custom tap.

## Channel Summary

| Channel | Best for | Command shape |
| --- | --- | --- |
| PyPI + `uv tool install` | daily use | `uv tool install claude-glm52-supervisor` |
| PyPI + `uvx` | one-off check | `uvx --from claude-glm52-supervisor claude-glm52 ...` |
| GitHub Release installer | direct asset install | `bash claude-glm52-installer.sh --prefix "$HOME/.local"` |
| Source checkout | development and inspection | `python3 outputs/claude-glm52.py ...` |
| `pipx` | fallback isolated install | `pipx install claude-glm52-supervisor` |
