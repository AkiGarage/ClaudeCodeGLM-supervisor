# Codex Setup Prompt

Copy the prompt below into a new Codex session when you want Codex to set up
ClaudeCodeGLM Supervisor on a macOS or Linux machine.

```text
You are Codex helping me set up ClaudeCodeGLM Supervisor end to end.

Goal:
- Install or upgrade the public PyPI package `claude-glm52-supervisor`.
- Verify that `claude-glm52`, `claude-glm52-delegate`, and `claude-glm52-batch` work.
- Check the local Claude Code + CLIProxyAPI + Z.AI GLM-5.2 route.
- Leave me at the point where I can safely run a bounded no-edit delegation smoke test.

Safety rules:
- Do not read, print, copy, or ask me to paste API keys, `.env` files, auth tokens, SSH keys, keychain contents, provider configs, or shell history.
- Do not edit global Claude Code config, shell profiles, provider config, or login/auth files without showing the exact change and asking first.
- Do not use sudo unless I explicitly approve the exact command.
- Do not commit, push, or modify any project repository unless I explicitly ask.
- Prefer harmless checks first. If a required tool is missing, explain the exact install command before running it.

Setup steps:
1. Detect OS, shell, CPU architecture, and current working directory.
2. Check these commands and versions when available:
   - `python3 --version`
   - `bash --version`
   - `git --version`
   - `uv --version`
   - `claude --version`
   - `cliproxyapi --help`
   - `rg --version`
   - `timeout --version`
3. If `uv` is missing, install it by the narrowest safe method for this machine, or stop with a copy-paste command if installation needs my approval.
4. Run:
   `uv tool install --upgrade claude-glm52-supervisor`
5. Ensure the tool binary directory is on PATH. If it is not, print the exact PATH line I should add; do not edit my shell profile unless I approve.
6. Create the worker config directory if needed:
   `mkdir -p "$HOME/.claude-glm52-worker"`
7. Use this worker config for the current session:
   `export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"`
8. Run:
   - `claude-glm52 --version`
   - `claude-glm52 paths`
   - `claude-glm52 doctor --offline`
   - `claude-glm52 setup --print`
9. Check whether Claude Code is installed and authenticated without printing secrets.
10. Check whether CLIProxyAPI is installed and whether a local endpoint such as `http://127.0.0.1:8317` appears reachable.
11. If CLIProxyAPI or Z.AI GLM-5.2 routing is not ready, stop and tell me the exact missing piece. Do not invent config values.
12. If `claude-glm52 doctor` has no FAIL entries and Claude Code + CLIProxyAPI look ready, run this no-edit smoke test from a harmless directory:
    `claude-glm52-delegate --role review --cwd . --timeout 120 --retries 0 --no-usage-log --no-quota-snapshot "Return exactly: ok. Do not edit files."`
13. If the smoke test is unsafe in the current directory, create a temporary empty directory and run it there instead.

Final report:
- Summarize what was installed or already present.
- Include the exact commands that passed.
- List any WARN or FAIL items from doctor.
- State whether the no-edit smoke test passed.
- Give the next command I should run for real work.
```
