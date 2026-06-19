# ClaudeCodeGLM Supervisor Homebrew Formula

The recommended install path for ClaudeCodeGLM Supervisor is the PyPI package:

```bash
uv tool install claude-glm52-supervisor
```

This directory keeps an advanced Homebrew formula for users who already manage
local tools through Homebrew and understand custom taps.

## What The Formula Does

- Installs the supervisor files under Homebrew `libexec`.
- Creates executable wrappers for:
  - `claude-glm52`
  - `claude-glm52-delegate`
  - `claude-glm52-batch`
  - `claude-glm52-subagent`
  - `claude-glm52-reviewer`
- Runs Python wrappers with Homebrew `python@3.11`.
- Runs shell wrappers with `/bin/bash`.
- Uses only offline commands in `brew test`.

The formula does not install Claude Code, start CLIProxyAPI, write provider
credentials, or edit Claude Code config.

## Advanced Use

```bash
brew tap AkiGarage/homebrew-tap https://github.com/AkiGarage/homebrew-tap
brew install claude-glm52
claude-glm52 doctor --offline
```

After install, run:

```bash
claude-glm52 setup --print
claude-glm52 doctor
```

## Layout

```text
packaging/homebrew-tap/
  Formula/
    claude-glm52.rb
  README.md
```

## Validation

```bash
ruby -c packaging/homebrew-tap/Formula/claude-glm52.rb
```

When validating an installed tap, use the tap-qualified formula name:

```bash
brew audit --strict --formula AkiGarage/homebrew-tap/claude-glm52
brew install --build-from-source AkiGarage/homebrew-tap/claude-glm52
brew test claude-glm52
```

Do not pass a direct formula file path to `brew audit` or
`brew install --build-from-source`; current Homebrew expects a tap-qualified
formula target for those checks.
