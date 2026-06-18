# Claude Code GLM-5.2 Delegation Contract

## Operating Model

- Codex is the orchestrator: intent capture, task shaping, risk assessment, final audit, and user communication.
- Claude Code GLM-5.2 is the execution unit: bounded implementation or read-only external review.
- Other orchestrators may call the same runners, but should pass bounded task packets rather than open-ended goals.

## Delegate To GLM-5.2 When

- The task has clear files, acceptance criteria, and validation commands.
- The work is implementation-heavy and does not require broad product judgment.
- Codex can quickly audit the resulting diff.
- The task benefits from GLM quota without reducing quality.

## Keep In Codex When

- The task is planning, architecture choice, user preference interpretation, security policy, money/risk decision, or final acceptance.
- The task needs recent external facts that have not been verified.
- The prompt would require GLM to infer too much missing context.
- A failed GLM attempt would likely create more cleanup than value.

## Implementation Packet Shape

Give Claude Code:

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

Required `Do not` items:

- Do not commit, push, rewrite history, or change secrets.
- Do not broaden scope beyond the packet.
- Do not search from `/` or `~`; if named files are insufficient, stop as blocked.
- Do not delete files or edit task packets/tests unless explicitly requested.
- Do not skip validation silently.
- Do not modify unrelated files.

## Review Packet Shape

Give Claude Code:

```text
Role: external reviewer
Scope:
Diff or files to review:
Risk areas:
Expected output:
```

Expected review output:

- Findings first, ordered by severity.
- File path and concrete evidence for each finding.
- If no issues, say no actionable findings and list residual risk.
- No edits.

## Image Input Policy

- Treat GLM-5.2 on this Claude Code worker route as text-only.
- Do not enable MCP globally or pass base64 image bodies into the GLM-5.2 prompt/logs.
- For image tasks, run the wrapper's `--image` preflight first. The default `--vision-backend mcp` starts Z.AI Vision MCP only for the preflight, converts local images into compact text context, then prepends that context to the bounded task packet.
- Use `--vision-mode auto` by default. Use `--vision-mode ocr` for terminal/error screenshots and dense text screenshots. Use `--vision-mode vision` for UI/layout, charts, product images, visual diff, or general scene understanding.
- The image summary is transient prompt context only. Result JSON and usage logs store sanitized metadata such as mode, model, byte size, and summary length, not the extracted image text.
- If required image preflight fails, fail closed and do not start GLM-5.2. Use `--vision-optional` only when the task can safely proceed without the image.
- Keep preflight output compact. The image-path rule is simple: never shove raw base64 through argv, prompts, or durable logs when a local file path plus a short visual summary is enough.
- `--vision-backend direct-zai` is an explicit fallback for accounts with separate Z.AI Open Platform vision/OCR resources. It is not the default for the current GLM Coding Plan route because live probes returned `glm-5v-turbo` subscription access denied and `glm-ocr` no-resource/balance errors.

## Language Policy

- Use English task packets by default for Codex -> Claude Code GLM-5.2 delegation.
- Keep the user's original request in Codex, then translate only the bounded execution packet into concise English.
- Preserve Japanese only when source wording, product voice, legal/user-facing copy, or Japanese-specific behavior is the actual task.
- If Japanese context must be included, put it in a short quoted context block and keep instructions/acceptance criteria in English.
- Add an explicit output cap: implementation returns under 10 concise bullet lines; review returns under 12 concise bullets.
- Ask reviewers to ignore delegation artifacts such as `delegate-result.json`, `review-result.json`, `*_result.json`, and task-packet files unless those files are the review target.
- Current active transport is local CLIProxyAPI at `http://127.0.0.1:8317`. The worker presents `claude-opus-4-6[1m]` to Claude Code for better model metadata, while CLIProxyAPI aliases that request to upstream `glm-5.2`; the verified context window is 1,000,000.
- Default timeout guidance after the 2026-06-15 safety fix: no short external timeout for manual/important implementation delegation, but keep the default 6h runaway ceiling and `--max-budget-usd 5.00` budget guard; 180-300s for automated read-only review/diagnosis; 900s for tiny automated implementation; 1800-3600s for larger implementation only when the packet has clear acceptance criteria and Codex will perform final audit.
- Do not use `--no-safety-ceiling` or `--no-budget-guard` for routine delegation.
- If a GLM-5.2 task times out once, do not blindly retry. Codex should shrink the packet, clarify relevant files/validation, or take over.
- For strict multi-case E2E, run a lightweight provider preflight first. If the preflight returns 529 or times out with no file changes/model usage, stop the batch and retry later rather than burning time on predictable provider failures.
- If an E2E case returns a transient provider error or no-change timeout, fail fast by default. Continue only for diagnostics with an explicit override.
- Use batch mode only for independent implementation packets with non-overlapping files or separate worktrees. Default to concurrency 2; raise to 3 only for small, clearly independent tasks.
- GLM-5.2 is strongest for long-horizon coding, complex debugging, performance optimization, automated research, large-scale implementation, and terminal/tool-use tasks with clear acceptance checks.
- GLM-5.2 supports 128K output at the model/API layer. The current Claude Code stable path now reports `maxOutputTokens: 64000` through the Opus 4.6 1M alias. Do not rely on 131072-token single responses through Claude Code stable; use file artifacts, chunking, a direct GLM API path, or a verified latest-channel Claude Code setup for larger generated text.
- Use the 1M context as reliability headroom, not as permission to paste an entire repository. Prefer a compact context map plus targeted files/specs/tests.

## Quality Gate

Codex must do all of these before treating GLM output as accepted:

- Inspect `git diff` or changed files.
- Treat wrapper `ok=false`, `policy_ok=false`, any `scope_violations`, or any deleted files as a failed delegation even when Claude reports success.
- Check for unrelated changes.
- Check for secrets or credential-like strings in generated outputs.
- Run or inspect validation results.
- Decide whether the diff satisfies the original user intent.

## Anti-Waste Rules

- Do not delegate vague planning to GLM.
- Do not ask GLM to do the same audit Codex already completed unless a second opinion is valuable.
- Prefer one bounded GLM task over multiple tiny back-and-forth calls.
- If GLM fails once due missing context, improve the packet before retrying.
- If GLM fails twice on the same task class, Codex takes over or changes approach.
- If GLM appears provider-unhealthy, stop delegating and report the provider state. More retries at that point create supervision overhead without new value.
- Avoid repeated ad hoc smoke loops against global `claude -p`; they can trigger provider cooldown and Claude Code internal retries. Use the wrapper/E2E harness because it has explicit guards and compact failure classification.

## Current Runners

Preferred wrapper:

```bash
claude-glm52-delegate --cwd /path/to/repo --timeout 900 --prompt-file /path/to/task.md --result-file /path/to/result.json
```

Image-aware wrapper:

```bash
claude-glm52-delegate --cwd /path/to/repo --image screenshot.png --vision-backend mcp --vision-mode auto --prompt-file /path/to/task.md --result-file /path/to/result.json
```

Fallback path:

```bash
python3 ./outputs/claude-glm52-delegate.py --cwd /path/to/repo --timeout 900 --prompt-file /path/to/task.md --result-file /path/to/result.json
```

Batch wrapper:

```bash
claude-glm52-batch --plan-file /path/to/batch-plan.json --concurrency 2 --result-file /path/to/batch-result.json
```

Raw implementation:

```bash
./outputs/claude-glm52-subagent.sh --cwd /path/to/repo "task packet..."
```

Review:

```bash
./outputs/claude-glm52-reviewer.sh --cwd /path/to/repo "review packet..."
```

The runners use isolated config at `${HOME}/.claude-glm52-worker`, force MCP off, ignore project/local settings, disable Chrome integration, keep Bash sandboxing on, disallow broad search/delete commands, and run Claude Code-visible `claude-opus-4-6[1m]` aliased by CLIProxyAPI to upstream `glm-5.2` with `--effort max`.

Quota logging uses the Z.AI quota API directly. It records sanitized quota fields and `quotaDelta` when a quota percentage is available; it does not shell out to a separate health CLI.

CLIProxyAPI service:

```bash
${HOME}/.local/bin/cliproxyapi -config /opt/homebrew/etc/cliproxyapi.conf
```

LaunchAgent:

```text
${HOME}/Library/LaunchAgents/com.example.cliproxyapi.plist
```
