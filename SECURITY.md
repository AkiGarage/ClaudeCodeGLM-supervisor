# Security Policy

## Supported Use

This repository is a local orchestration layer for delegating bounded Claude Code
tasks through a GLM-5.2 route. It should never contain API keys, bearer tokens,
private keys, personal machine paths, or provider account data.

## Maintainer Hygiene

Before publishing a branch, release archive, or tap formula, maintainers should
run the public audit gate from the repository root:

```bash
python3 scripts/public_audit.py
```

The audit intentionally fails on tracked local ledgers, generated run JSON,
logs, private machine paths, personal names, and likely secret assignments. A
failing audit means the public branch or release archive still needs cleanup.

## Secrets

- Keep provider keys in your shell, launchd environment, secret manager, or a
  private local config that is ignored by git.
- Do not commit `.env`, API key dumps, raw provider quota responses, prompt logs
  containing credentials, or worker artifacts that include private paths.
- Use placeholders such as `<ZAI_API_KEY>` in examples.

## Reporting

If you find a committed secret or privacy leak, rotate the affected credential
first. Then open a private GitHub Security Advisory if available, or contact the
maintainers without posting the secret value in a public issue.
