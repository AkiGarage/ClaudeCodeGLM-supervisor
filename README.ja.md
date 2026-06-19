![Codex controls Claude Code](docs/assets/codex-controls-claude-code.png)

<p align="center">
  <a href="./README.md"><img alt="Read in English" src="https://img.shields.io/badge/Read%20in-English-2f6feb?style=for-the-badge"></a>
  <a href="./README.ja.md"><img alt="Language Japanese" src="https://img.shields.io/badge/Language-%E6%97%A5%E6%9C%AC%E8%AA%9E-f97316?style=for-the-badge"></a>
  <img alt="Version v0.0.3" src="https://img.shields.io/badge/Version-v0.0.3-111827?style=for-the-badge">
</p>

# ClaudeCodeGLM Supervisor

ClaudeCodeGLM Supervisor は、Codex から Claude Code へ範囲を決めた実装やレビューを任せ、その Claude Code の実行先を Z.AI GLM-5.2 に向けるための CLI です。

Codex は、計画、制約設計、危険な操作の制御、検証、最終確認を担当します。Claude Code GLM-5.2 は、指定された範囲だけを実行する作業担当として動きます。

## 仕組み

ClaudeCodeGLM Supervisor は、Codex が制御を保ったまま、長めの実装やレビューだけを作業担当に渡すためのものです。

1. Codex がリポジトリを読み、委託すべき作業か判断します。
2. Codex が、変更してよい file、制約、合格条件、検証 command を含む task packet を作ります。
3. supervisor が、その task packet を GLM-5.2 route の Claude Code に渡します。
4. Claude Code は、指定された範囲の実装またはレビューだけを行います。
5. Codex が、結果、差分、検証結果を確認し、採用するか、修正するか、さらに狭い task に分け直します。

日常運用では、`implement with CCG`、`use CCG for implementation`、`ClaudeCodeGLM に実装委託して` のような短い依頼で十分です。この場合の意味は、Codex が先に計画し、作業担当が範囲付きで実行し、Codex が最後に監査する、という流れです。

この worker route は丸投げ用ではありません。仕様判断、危険な操作、大きな refactor、commit、push、最終承認は、人間が明示しない限り Codex 側に残します。

## 使う場面

この supervisor は、Codex だけで一気に編集するには少し大きいが、範囲と検証は厳密に管理したい作業に向いています。たとえば次のような場合です。

- 変更範囲が小さく定義された feature 実装
- Codex が期待挙動を決めた後の test 追加や更新
- 合格条件が明確な読み取り専用 review
- 独立した複数 task を小さな batch に分けて実行する作業
- screenshot や画像 evidence を text に変換してから coding task に渡す作業

一方で、主に product judgment が必要な作業、security-sensitive な config、credential setup、破壊的な file 操作、広範な architecture 変更、release 承認は委託に向きません。その判断は Codex と人間の側に残してください。

## 安全モデル

この package は保守的に動くように設計されています。

- task packet には worker が編集してよい file を明記します。
- worker prompt では、secret access、広範囲 filesystem search、file 削除、commit、push、auth/config 編集を禁止します。
- Codex が差分を読み、validation を再実行します。
- offline check は Claude Code、CLIProxyAPI、Z.AI、secret を含む config を呼びません。
- usage / quota snapshot は evidence として扱い、自動的に使い続ける許可としては扱いません。

## CLIProxyAPI を使う理由

この route では、Claude Code と Z.AI の間に [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) を置きます。CLIProxyAPI が、Claude Code と GLM-5.2 の間の実用的な互換 layer になるためです。

- Claude Code から見える model 名を保ちながら、上流を GLM-5.2 に向けられます。
- Claude Code に、この route で検証済みの大きな context / output 挙動に合う model metadata を見せられます。
- local routing、alias、retry、複数 key / provider 構成を一箇所で扱えます。
- Claude Code の endpoint 設定を何度も直接書き換えるより、構成がきれいに保てます。
- delegated work の usage / quota evidence を取りやすい安定した中継点になります。

この supervisor は、CLIProxyAPI project と contributors の取り組みに支えられています。現在の推奨 setup を実用的にしている gateway layer を維持している community に感謝します。

CLIProxyAPI なしで動くかどうかについては、環境によっては Z.AI の Anthropic-compatible endpoint へ Claude Code から直接つなげる可能性があります。ただし、この package の supported route ではありません。CLIProxyAPI なしでは、model alias、大きな context 用 metadata、output ceiling、retry behavior、usage snapshot、provider routing まわりの setup が増え、検証済みの保証も弱くなります。実運用では、Claude Code config の手作業が増え、worker の挙動の再現性が下がり、Codex が delegated run を監査するときの evidence も弱くなります。

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

- macOS または Linux の shell environment
- Python 3.11 以上
- Bash
- Git
- Claude Code CLI の install と認証
- CLIProxyAPI の install と local 起動
- GLM-5.2 access を持つ Z.AI account / API key

推奨:

- install / upgrade 用の `uv`
- repository inspection を速くする `rg`
- runaway task guard 用の GNU `timeout`
- Claude Code worker 専用 config directory、通常は
  `~/.claude-glm52-worker`

Sensitive value は runtime に local environment または provider config から読みます。API key、`.env` file、auth token、provider config、shell history は commit しないでください。

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

GLM-5.2 coding worker は text-only として扱います。画像 file は先に軽量な Z.AI Vision MCP / OCR preflight で解析します。抽出した evidence text だけを task packet に入れ、raw image summary は result JSON や usage log に保存しません。

## task packet の形

task 自体が日本語 text を扱う場合を除き、packet は短い English で書くのを推奨します。

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
