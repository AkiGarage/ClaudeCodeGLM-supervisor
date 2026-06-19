![Codex controls Claude Code](docs/assets/codex-controls-claude-code.png)

<p align="center">
  <a href="./README.md"><img alt="Read in English" src="https://img.shields.io/badge/Read%20in-English-2f6feb?style=for-the-badge"></a>
  <a href="./README.ja.md"><img alt="Language Japanese" src="https://img.shields.io/badge/Language-%E6%97%A5%E6%9C%AC%E8%AA%9E-f97316?style=for-the-badge"></a>
  <img alt="Version v0.0.3" src="https://img.shields.io/badge/Version-v0.0.3-111827?style=for-the-badge">
</p>

# ClaudeCodeGLM Supervisor

ClaudeCodeGLM Supervisor は、Codex から Claude Code へ範囲を決めた実装やレビューを任せ、その Claude Code の実行先を Z.AI GLM-5.2 に向けるための CLI です。

Codex は、計画、制約設計、危険な操作の制御、検証、最終確認を担当します。Claude Code GLM-5.2 は、指定された範囲だけを実行する作業担当として動きます。

## インストール

推奨のインストール方法は、PyPI package を `uv` で使う方法です。

一時的に確認するだけなら:

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
```

常用 CLI として入れるなら:

```bash
uv tool install claude-glm52-supervisor
claude-glm52 setup --print
```

GitHub Release の asset を直接確認して入れたい場合は、checksum 検証付き installer も使えます。

```bash
curl -fsSLO https://github.com/AkiGarage/ClaudeCodeGLM-supervisor/releases/latest/download/claude-glm52-installer.sh
bash claude-glm52-installer.sh --prefix "$HOME/.local"
```

## Codex にセットアップさせる

いちばん簡単なのは、Codex に環境確認とセットアップを任せる方法です。
[`docs/codex-setup-prompt.md`](docs/codex-setup-prompt.md) の prompt を新しい Codex session にそのまま貼り付けてください。

その prompt は Codex に次を実行させます。

- `uv` 経由で `claude-glm52-supervisor` を install / upgrade
- Python、Bash、Git、Claude Code、CLIProxyAPI、任意の `timeout` を確認
- Claude Code worker 用の分離 config directory を作成
- secret を読まない、表示しない
- offline / online doctor を実行
- local tools が揃っている場合だけ、編集しない smoke test を実行
- 残った手作業を短い setup report として出力

## 必要要件

必須:

| 種類 | 必要なもの |
| --- | --- |
| OS | macOS または Linux |
| Python | Python 3.11 以上 |
| Shell | Bash |
| Git | `git` |
| Claude Code | `claude` command |
| CLIProxyAPI | ローカルの Anthropic-compatible gateway |
| Z.AI | GLM-5.2 を使える account / API key |

推奨:

- `uv`
- `rg`
- GNU `timeout`
- Claude Code worker 専用 config directory: `~/.claude-glm52-worker`

API key、`.env`、auth token、provider config、shell history は git に入れないでください。

## セットアップ概要

1. Supervisor CLI を入れます。

   ```bash
   uv tool install claude-glm52-supervisor
   claude-glm52 doctor --offline
   ```

2. Claude Code を入れて認証します。

   ```bash
   claude --version
   ```

3. CLIProxyAPI を起動し、次のような local endpoint を用意します。

   ```text
   http://127.0.0.1:8317
   ```

4. CLIProxyAPI で、Claude Code から見える alias を GLM-5.2 に向けます。

   ```text
   claude-opus-4-6[1m] -> glm-5.2
   ```

5. Claude Code worker 用の config directory を分けます。

   ```bash
   export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
   mkdir -p "$CLAUDE_GLM52_WORKER_CONFIG_DIR"
   ```

6. setup guide と doctor を実行します。

   ```bash
   claude-glm52 setup --print
   claude-glm52 doctor
   ```

## 軽い動作確認

実作業の前に、編集しない review task を実行します。

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

失敗する場合は、`claude --version`、CLIProxyAPI の起動状態、`claude-glm52 doctor` の出力を確認してください。

## 主な command

単発の実装:

```bash
claude-glm52-delegate \
  --cwd /path/to/repo \
  --timeout 900 \
  --retries 1 \
  --prompt-file task-packet.md \
  --result-file delegate-result.json
```

読み取り専用レビュー:

```bash
claude-glm52-delegate \
  --role review \
  --cwd /path/to/repo \
  --timeout 300 \
  --prompt-file review-packet.md \
  --result-file review-result.json
```

複数作業の batch 実行:

```bash
claude-glm52-batch \
  --plan-file batch-plan.json \
  --concurrency 2 \
  --result-file batch-result.json
```

画像を含む作業:

```bash
claude-glm52-delegate \
  --cwd /path/to/repo \
  --image screenshots/error.png \
  --vision-backend mcp \
  --vision-mode auto \
  --prompt-file task-packet.md \
  --result-file delegate-result.json
```

GLM-5.2 coding worker は text-only として扱います。画像は先に Z.AI Vision MCP / OCR で解析し、短く整理した evidence text だけを task packet に入れます。

## task packet の形

```text
Role: implementation worker
Goal:
Repo/CWD:
Files likely relevant:
Allowed files:
Constraints:
Acceptance criteria:
Validation commands:
Do not:
Return:
```

必ず入れたい制約:

- 変更してよい file を明記する
- `/` や `~` からの広範囲検索を禁止する
- file 削除、secret 編集、commit、push、auth/config 変更を禁止する
- 可能なら検証 command を明記する
- 最後の返答は短くし、大きな成果物は file に書かせる

## 検証済みの構成

| 項目 | 内容 |
| --- | --- |
| 指揮役 | Codex |
| 作業担当 | Claude Code |
| 中継 | CLIProxyAPI |
| 上流モデル | Z.AI GLM-5.2 |
| Claude Code から見えるモデル | `claude-opus-4-6[1m]` alias |
| 検証済み context window | 1,000,000 tokens |
| 検証済み Claude Code 出力上限 | 64,000 tokens |
| 画像対応 | Z.AI Vision MCP / OCR preflight 後に text context injection |

GLM-5.2 自体は、model / API layer ではより大きな出力に対応できます。ただし、この Claude Code 経由の作業経路で検証済みなのは 64K 出力までです。128K の単発出力が必要な場合は、別の direct GLM-5.2 経路を検証して使ってください。

## 関連 docs

- [インストール詳細](docs/install.md)
- [install channel の説明](docs/distribution-strategy.md)
- [Codex setup prompt](docs/codex-setup-prompt.md)

## License

[`LICENSE`](LICENSE) を参照してください。現在の notice は保守的な rights-reserved 扱いで、open-source reuse や redistribution rights は付与していません。
