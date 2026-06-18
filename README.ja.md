![Codex controls Claude Code](docs/assets/codex-controls-claude-code.png)

<p align="center">
  <a href="./README.md"><img alt="Read in English" src="https://img.shields.io/badge/Read%20in-English-2f6feb?style=for-the-badge"></a>
  <a href="./README.ja.md"><img alt="Language Japanese" src="https://img.shields.io/badge/Language-%E6%97%A5%E6%9C%AC%E8%AA%9E-f97316?style=for-the-badge"></a>
  <img alt="Version v0.0.2" src="https://img.shields.io/badge/Version-v0.0.2-111827?style=for-the-badge">
</p>

# ClaudeCodeGLM Supervisor

ClaudeCodeGLM Supervisor は、Codex から Claude Code へ「範囲を決めた実装」や「読み取り専用レビュー」を任せ、その Claude Code の実行先を Z.AI GLM-5.2 に向けるための仕組みです。

Codex は、計画、判断、危険な操作の制御、検証、最終確認を担当します。Claude Code GLM-5.2 は、決められた作業だけをこなす作業担当として動きます。長めの実装を GLM 側に逃がしながら、最後の責任と監査は Codex に残す、という使い方を想定しています。

## インストール

このプロジェクトでは、custom Homebrew tap を通常のユーザー導線にはしない方針です。公開配布はまず checksum 検証付きの GitHub Release installer、次に PyPI package を `uvx` / `uv tool install` で使える形を目指します。

PyPI 化後の目標コマンドは次の形です。

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
uv tool install claude-glm52-supervisor

claude-glm52 setup --print
```

この repo は Python package layout を含んでいます。PyPI 公開までは、clean な source checkout から直接 wrapper を呼ぶか、local wheel を build して確認します。

```bash
python3 outputs/claude-glm52.py --version
python3 outputs/claude-glm52.py doctor --offline
python3 outputs/claude-glm52-delegate.py --help
```

背景と配布方針は [`docs/install.md`](docs/install.md) と [`docs/distribution-strategy.md`](docs/distribution-strategy.md) にあります。Homebrew tap 雛形は [`packaging/homebrew-tap/`](packaging/homebrew-tap/) に残しますが、maintainer 検証用です。

## 必要要件

まず必要なものは次のとおりです。

| 種類 | 必要なもの | 補足 |
| --- | --- | --- |
| OS | macOS または Linux | この README は主に macOS での導入を想定しています。 |
| Python | Python 3.11 以上 | このリポジトリのラッパーは Python で動きます。 |
| Shell | Bash | 実行スクリプトが Bash で書かれています。 |
| Git | `git` | 対象リポジトリの差分確認に使います。 |
| 検索ツール | `rg` | 必須ではありませんが、あると Codex 側の探索が速く安全です。 |
| Claude Code | `claude` コマンド | 公式の Native Install または Homebrew が現在の無難な選択肢です。 |
| CLIProxyAPI | ローカルの中継サービス | Claude Code と Z.AI GLM-5.2 の間に置きます。 |
| Z.AI | GLM-5.2 を使えるアカウントと API key | key の値はリポジトリに保存しないでください。 |
| 任意 | `npx` | 画像タスクで既定の Vision MCP 処理方式を使う場合だけ必要です。 |
| 任意 | GNU `timeout` | 長時間実行の安全停止に使います。macOS では Homebrew coreutils で入れられます。 |

秘密情報は環境変数やローカル設定から読みます。`.env`、API key、auth token、provider config、LaunchAgent などをこのリポジトリに commit しないでください。

## 現状の結論: tap ではなく Release installer と PyPI/uvx に寄せる

このリポジトリは、`outputs/` の CLI / wrapper、`docs/`、`tests/`、そして maintainer 検証用の `packaging/homebrew-tap/` を含んでいます。公開配布物は clean snapshot から作り、private な開発履歴や local artifact は含めません。

現時点でいちばん安全寄りの考え方は次の形です。

1. Claude Code は公式が案内する Native Install または Homebrew cask で入れる。
2. CLIProxyAPI は公式 release から、必要な版を確認して入れる。
3. このリポジトリは release installer または PyPI/uvx で入れる。
   それまでは source checkout から wrapper を直接呼ぶ。
4. tap 経由の検証は、release 前の maintainer 確認としてだけ扱う。
5. 画像機能だけ、必要なときに pin 済みの `@z_ai/mcp-server@0.1.4` を `npx` 経由で使う。

`npm install -g ...` は手軽ですが、グローバル npm package に強く依存する構成は supply-chain risk が気になります。そのため、この README では npm を主経路にはしていません。Claude Code についても、公式 Quickstart で案内されている Native Install / Homebrew を優先します。

PyPI package 化後の理想形は、次のような `uvx` / `uv tool install` の形です。

```bash
uvx --from claude-glm52-supervisor claude-glm52 doctor --offline
uv tool install claude-glm52-supervisor
claude-glm52 setup --print
claude-glm52 doctor
```

参考:

- Claude Code Quickstart: https://code.claude.com/docs/en/quickstart
- Claude Code setup: https://code.claude.com/docs/en/setup
- Claude Code Homebrew cask: https://formulae.brew.sh/cask/claude-code
- CLIProxyAPI: https://github.com/router-for-me/CLIProxyAPI

## セットアップ手順

ここでは、何も入っていない状態から動作確認までの流れを書きます。すでに入っているものがある場合は、該当する確認だけ実行してください。

### 1. 基本ツールを確認する

```bash
python3 --version
bash --version
git --version
```

`rg` がなければ、macOS では次のように入れられます。

```bash
brew install ripgrep
```

長時間実行の停止 guard を使う場合は、`timeout` も用意します。

```bash
brew install coreutils
/opt/homebrew/bin/timeout --version
```

Intel Mac では Homebrew の prefix が `/usr/local` の場合があります。その場合は `/usr/local/bin/timeout` を確認してください。

### 2. Claude Code を入れる

macOS では Homebrew が分かりやすいです。

```bash
brew install --cask claude-code
claude --version
claude --help
```

公式 Quickstart では Native Install も案内されています。環境に合わせて、公式 docs の最新手順を確認してください。

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

この curl 方式は公式手順ですが、shell script を直接実行します。会社端末や厳しめの環境では、Homebrew cask のほうが管理しやすいことがあります。

### 3. Z.AI GLM-5.2 の key を用意する

Z.AI の GLM-5.2 を使える API key を用意します。

key の値は README、log、task packet、git 管理ファイルには書かないでください。環境変数、OS の keychain、またはローカルの provider config から読み込ませます。

このリポジトリの検証済み経路では、Claude Code は直接 Z.AI を叩くのではなく、次の CLIProxyAPI endpoint に向けます。

```text
http://127.0.0.1:8317
```

### 4. CLIProxyAPI を入れて起動する

CLIProxyAPI は、Claude Code に Claude Code 互換のモデル名を見せながら、実際には Z.AI GLM-5.2 へ流すためのローカル中継サービスです。

このリポジトリの検証済み構成では、CLIProxyAPI がかなり重要です。CLIProxyAPI なしでも環境によっては動く可能性がありますが、1M context、64K output ceiling、alias、retry、usage snapshot まわりの保証は弱くなります。

CLIProxyAPI の入れ方は環境差があるため、公式 release / docs を確認し、必要な版を入れてください。

```bash
cliproxyapi --help
```

起動例です。config の場所は自分の環境に合わせてください。

```bash
$HOME/.local/bin/cliproxyapi -config /opt/homebrew/etc/cliproxyapi.conf
```

設定では、Claude Code から見える名前を GLM-5.2 に向けます。

```text
claude-opus-4-6[1m] -> glm-5.2
```

この alias により、Claude Code 側では扱いやすいモデル情報を保ちつつ、上流では GLM-5.2 を使います。

### 5. このリポジトリを取得する

```bash
git clone <this-repository-url>
cd ClaudeCodeGLM-supervisor
```

このリポジトリは、今のところ Python package として install する形ではありません。まずはリポジトリ内から直接ラッパーを呼ぶのが安全です。

```bash
python3 outputs/claude-glm52-delegate.py --help
python3 outputs/claude-glm52-batch.py --help
```

必要なら、あとで自分の `PATH` に短い shim を置けます。

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/outputs/claude-glm52-delegate.py" "$HOME/.local/bin/claude-glm52-delegate"
ln -s "$PWD/outputs/claude-glm52-batch.py" "$HOME/.local/bin/claude-glm52-batch"
```

すでに同名ファイルがある場合は、上書きせず、先に中身と向き先を確認してください。

### 6. 作業担当用の Claude Code 設定を分ける

普段使いの Claude Code 設定と、この作業担当用設定は分けることをおすすめします。

```bash
export CLAUDE_GLM52_WORKER_CONFIG_DIR="$HOME/.claude-glm52-worker"
mkdir -p "$CLAUDE_GLM52_WORKER_CONFIG_DIR"
```

このリポジトリの実行スクリプトは、通常 `~/.claude-glm52-worker` を使います。普段の Claude Code の hooks、MCP、自動記憶、slash command などを読み込みすぎないようにするためです。

### 7. 軽い動作確認をする

まずは編集しない review mode で、短い動作確認を実行します。

```bash
python3 outputs/claude-glm52-delegate.py \
  --role review \
  --cwd . \
  --timeout 120 \
  --retries 0 \
  --no-usage-log \
  --no-quota-snapshot \
  "Return exactly: ok. Do not edit files."
```

ここで失敗する場合は、いきなり重い実装を流さず、次を確認してください。

- `claude --version` が通るか。
- CLIProxyAPI が起動しているか。
- Claude Code が `http://127.0.0.1:8317` へ向いているか。
- Z.AI key がローカル設定や環境変数から読めているか。
- `timeout` が必要な環境で見つかるか。

## クイックスタート

すでに Claude Code、CLIProxyAPI、Z.AI key の準備が終わっている場合は、最短では次だけで確認できます。

```bash
git clone <this-repository-url>
cd ClaudeCodeGLM-supervisor
python3 outputs/claude-glm52-delegate.py --help
python3 outputs/claude-glm52-delegate.py \
  --role review \
  --cwd . \
  --timeout 120 \
  --retries 0 \
  --no-usage-log \
  --no-quota-snapshot \
  "Return exactly: ok. Do not edit files."
```

公開 release は clean snapshot から作ります。private な開発履歴や local work artifact を配布 tarball / wheel に含めないため、手元の未push作業は上記の直接実行で確認してください。
maintainer は公開前に `python3 scripts/build_public_snapshot.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --replace` で snapshot を作り、`scripts/public_audit.py --root ... --all-files` で監査します。
push 前の最終 staging は `python3 scripts/stage_public_repo.py --out-dir /tmp/ClaudeCodeGLM-supervisor-public --version v0.0.2 --replace` で、local git commit/tag と release assets 生成まで確認できます。`--version` は `pyproject.toml` の package version と一致させます。

## 現在の検証済み構成

| 項目 | 内容 |
| --- | --- |
| 指揮役 | Codex |
| 作業担当 | Claude Code |
| 中継 | CLIProxyAPI |
| 上流モデル | Z.AI GLM-5.2 |
| Claude Code から見えるモデル | `claude-opus-4-6[1m]` alias |
| 検証済み context window | 1,000,000 tokens |
| 検証済み Claude Code 出力上限 | 64,000 tokens |
| 画像対応 | 先に Vision MCP / OCR で文字情報へ変換してから渡す |

GLM-5.2 自体は、model / API layer ではより大きな出力に対応できます。ただし、この Claude Code 経由の作業経路で検証済みなのは 64K 出力までです。128K の単発出力が必要な場合は、Claude Code 経由に無理に載せず、別の direct GLM-5.2 経路を検証して使ってください。

## どう使うものか

基本の流れは次のとおりです。

1. Codex がリポジトリを読み、作業計画を立てます。
2. Codex が、変更してよい file、守る制約、合格条件、検証 command を含む task packet を作ります。
3. ClaudeCodeGLM Supervisor が、その task packet を Claude Code GLM-5.2 に渡します。
4. Claude Code が、指定された範囲内で実装またはレビューします。
5. Codex が、返ってきた JSON、差分、検証結果を監査し、採用、修正、または狭い再実行を判断します。

日常運用では、次のような短い依頼で十分です。

```text
実装はCCGで
CCGで実装して
ClaudeCodeGLMに実装委託して
```

この場合の意味は、「Codex が先に計画し、CCG が範囲付きで実装し、Codex が最後に監査する」です。CCG は丸投げ先ではありません。仕様判断、危険な操作、最終承認は Codex 側に残します。

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

独立した複数作業の batch 実行:

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

GLM-5.2 coding worker は text-only として扱います。画像は先に Z.AI Vision MCP / OCR で解析し、短く整理した evidence text だけを task packet に入れます。raw image summary は result JSON や usage log に残しません。

## task packet の書き方

作業担当に渡す task packet は、短く具体的にします。日本語の文章そのものを扱う作業でなければ、作業担当向け packet は簡潔な英語が速く安定しやすいです。

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

- 変更してよい file を明記する。
- `/` や `~` からの広範囲検索を禁止する。
- file 削除、secret 編集、commit、push、auth/config 変更を禁止する。
- 可能なら検証 command を明記する。
- 作業担当の最後の返答は短くし、大きな成果物は file に書かせる。

## 使用量と quota の記録

delegate の結果には、次のような情報が入ります。

- `usageSummary`: Claude Code が報告した token と cost の合計。
- `usage_snapshots.before` / `usage_snapshots.after`: provider 側の使用量 snapshot。
- `usage_accounting.tokens_*`: ZCode と比較しやすい token fields。
- `usage_accounting.quota_percent_*`: 安全に差分計算できる場合だけ入る quota percent fields。

quota percentage は保守的に扱います。provider が「使用量や残量なしの percentage」だけを返す場合は、勝手に `0%` と見なしません。その場合は理由付きで `unavailable` にします。

## token 節約の実測

website、mini-game、backend reconciliation、policy routing、vision / OCR 4件を含む 8 作業の benchmark では、3 つの経路すべてが平均品質 10/10 で検証に通りました。

| 経路 | 報告された tokens | 所要時間 | strong pass |
| --- | ---: | ---: | ---: |
| ClaudeCodeGLM | 1,000,148 | 1984.2s | 8/8 |
| ZCode | 1,037,882 | 2236.4s | 8/8 |
| Codex self | 5,020,951 | 1877.8s | 8/8 |

これは「無料になる」という意味ではありません。Codex / GPT 側の token 消費を、GLM 側の実行に移すという意味です。範囲がはっきりした長めの作業では、品質を保ちながら Codex 側 token を大きく節約できる可能性があります。ただし、これは benchmark evidence であり、すべての作業への保証ではありません。

## 主要ファイル

| Path | 役割 |
| --- | --- |
| `pyproject.toml` | PyPI / uv 向け package metadata と console scripts |
| `src/claude_glm52_supervisor/` | import 可能な package 本体 |
| `outputs/claude-glm52-delegate.py` | source checkout 互換 shim |
| `outputs/claude-glm52-batch.py` | source checkout 互換 shim |
| `outputs/claude-glm52-subagent.sh` | Claude Code worker を起動する実行スクリプト |
| `tests/` | install CLI、usage、vision、process cleanup helper の unit tests |

## 検証 command

通常の変更後は、少なくとも次を実行します。

```bash
bash -n outputs/claude-glm52-subagent.sh
bash -n packaging/install/claude-glm52-installer.sh packaging/release/build-release-assets.sh
python3 -m py_compile src/claude_glm52_supervisor/*.py outputs/*.py
python3 -m unittest discover -s tests -v
uv build --out-dir /tmp/claude-glm52-dist
```

## 安全上の注意

- `.env`、API key、auth token、private key、local provider config、shell history を commit しない。
- secret を含む可能性がある prompt text は log に残さない。
- worker output は真実ではなく、確認すべき evidence として扱う。
- Codex を final auditor として維持する。
- image / OCR context は sanitize し、必要がない限り raw extracted text を永続化しない。

## License

[`LICENSE`](LICENSE) を参照してください。現在の notice は保守的な
rights-reserved 扱いで、open-source reuse や redistribution rights は付与していません。
将来 MIT / Apache-2.0 などを選ぶ場合は、この節と package metadata を同時に更新します。
