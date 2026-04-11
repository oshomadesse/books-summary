---
tags:
  - engineering
  - books-summary
  - done
---
# 読書サマリー自動生成システム

## 目的
毎朝7:00に「今日読むべき本」をAIが自動選定・リサーチし、インフォグラフィック付きのサマリーを生成してLINEに通知することで、読書習慣の定着と知識のインプットを効率化する。

## システム概要
**ローカル Mac の LaunchAgent** が毎朝 7:00 JST に `run_local.sh` を起動し、
Python スクリプトで複数のAIモデル（Gemini, GPT-5, Claude）を組み合わせて
高品質な読書ノートを生成、そのまま `main` ブランチに push する。
GitHub Actions は手動フォールバック (`workflow_dispatch`) のみ。

### アーキテクチャ
- **実行環境**: ローカル Mac (LaunchAgent) ／ フォールバック: GitHub Actions (Ubuntu latest)
- **言語**: Python 3.11+
- **AIモデル**:
    - 推薦: Gemini 2.5 Flash
    - リサーチ: GPT-5 (or Gemini Pro)
    - 図解生成: Claude 4.5 Sonnet
- **データソース**: Google Custom Search API (補助), Google Sheets (除外リスト)
- **通知**: LINE Messaging API (Flex Message)

## 機能仕様

### 1. 除外リスト取得 (Step 1)
- **担当**: `sheets_connector.py`
- **内容**: Google Sheetsから過去に読んだ本や除外したい本のリストを取得する。

### 2. 書籍推薦 (Step 2)
- **担当**: `gemini_recommend.py`
- **モデル**: Gemini 2.5 Flash
- **内容**: 除外リストを考慮し、指定カテゴリ（ビジネス、自己啓発、心理学など）から今日読むべき本を5冊推薦する。
- **フィルタ**: 日本語タイトル以外の除外、禁止ワード（小説など）の除外。

### 3. 選書 (Step 3)
- **内容**: 推薦された5冊の中からランダムに1冊を選択する。

### 4. Deep Research (Step 4)
- **担当**: `chatgpt_research.py`
- **モデル**: GPT-5
- **内容**: 選択された本について詳細なリサーチを行い、核心的メッセージ、エグゼクティブサマリー、アクションプラン等をJSON形式で抽出する。

### 5. インフォグラフィック生成 (Step 5)
- **担当**: `claude_infographic.py`
- **モデル**: Claude 4.5 Sonnet
- **内容**: リサーチ結果を元に、概念図解を含む単一のHTMLファイルを生成する。
- **出力**: `infographics/[書籍名]_infographic.html` + `docs/[書籍名]_infographic.html`
- **公開**: GitHub Pages (`docs/`) で公開。URL: `https://oshomadesse.github.io/books-summary/`

### 6. ノート生成 (Step 6-7)
- **内容**: リサーチ結果とインフォグラフィックへのリンクを含むMarkdownノートを作成する。
- **出力**:
    - ローカル実行時 (通常): `$VAULT_ROOT/100_Inbox/Books-YYYY-MM-DD.md` に直接書き込む
    - GitHub Actions 手動実行時 (フォールバック): `artifacts/Books-YYYY-MM-DD.md` に書き込み commit & push。次回ローカル `run_local.sh` が先頭で `100_Inbox` へ回収する。

### 7. 事後処理・通知 (Step 8-9)
- **除外リスト更新**: 選ばれた本をGoogle Sheetsに追記する。
- **通知**: LINE Messaging API (Flex Message) で、生成されたノートへのリンクとサマリーをユーザーに通知する。

## ディレクトリ構成
```
📖 books-summary/
├── .github/workflows/
│   └── daily_workflow.yml              # CI 手動フォールバック (workflow_dispatch のみ)
├── src/
│   ├── integrated_reading_workflow.py  # 統合ワークフロー本体
│   ├── chatgpt_research.py             # リサーチ (GPT-5)
│   ├── claude_infographic.py           # インフォグラフィック生成 (Claude 4.5)
│   ├── gemini_recommend.py             # 推薦 (Gemini Flash)
│   ├── line_messaging.py               # LINE通知
│   ├── sheets_connector.py             # Google Sheets連携
│   ├── link_books.py                   # 関連書籍リンク整形
│   ├── run_local.sh                    # ★ ローカル日次実行スクリプト (LaunchAgent から起動)
│   ├── com.oshomadesse.bookssummary.run.plist  # LaunchAgent 定義 (07:00 JST)
│   └── requirements.txt                # 依存ライブラリ
├── data/
│   ├── integrated/                     # 統合ログ・run_local ログ (.gitignore)
│   └── modules/                        # モジュール別デバッグログ
├── artifacts/                          # 手動 CI 実行時のみ一時利用。ローカル通常ルートでは空
├── infographics/                       # 生成された HTML 図解 (マスター)
└── docs/                               # GitHub Pages 公開用 (index.html + HTML 図解)
```

### ファイル関係図
```
                 ┌──────────────────────────────────────┐
                 │ LaunchAgent (07:00 JST 毎日)         │
                 │ com.oshomadesse.bookssummary.run     │
                 └──────────────────┬───────────────────┘
                                    │ exec
                                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ src/run_local.sh                                            │
   │   1. flock で多重起動防止                                   │
   │   2. ネット疎通確認 (スリープ復帰後対策)                    │
   │   3. git fetch / pull --ff-only                             │
   │   4. artifacts/Books-*.md が残っていれば 100_Inbox へ回収   │
   │   5. python integrated_reading_workflow.py  (下記)          │
   │   6. git add infographics/ docs/ → commit                   │
   │   7. git push (失敗時 2/4/8/16s バックオフ)                 │
   └──────────────────────┬──────────────────────────────────────┘
                          │
                          ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ src/integrated_reading_workflow.py                          │
   │   step1 → sheets_connector.py    (除外リスト)               │
   │   step2 → gemini_recommend.py    (5冊推薦)                  │
   │   step3   選書                                              │
   │   step4 → chatgpt_research.py    (Deep Research)            │
   │   step5 → claude_infographic.py  (HTML 図解生成)            │
   │             └─ docs/ にコピー → _git_auto_push (内部push)   │
   │   step6   中間サマリ                                        │
   │   step7   100_Inbox/Books-YYYY-MM-DD.md を直接生成          │
   │   step8 → link_books.py / 除外リスト追記                    │
   │   step9 → line_messaging.py      (LINE Flex 通知)           │
   └─────────────────────────────────────────────────────────────┘

   生成物:
     infographics/*.html   ─┐
     docs/*.html           ─┼── git で main に push (run_local.sh 側)
     docs/index.html       ─┘
     $VAULT_ROOT/100_Inbox/Books-YYYY-MM-DD.md  (リポジトリ外、Obsidian Vault)
```

## 環境変数 (GitHub Secrets)

| 変数名 | 説明 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API Key (GPT-5用) |
| `ANTHROPIC_API_KEY` | Anthropic API Key (Claude用) |
| `GEMINI_API_KEY` | Google AI Studio API Key (Gemini用) |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API Token |
| `LINE_USER_ID` | LINE User ID (通知送信先) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Sheets/Drive API用 サービスアカウントJSON |
| `EXCLUDED_SHEET_ID` | 除外リスト用スプレッドシートID |

## 運用

### 通常運用: ローカル Mac (LaunchAgent)
毎日 7:00 JST に `~/Library/LaunchAgents/com.oshomadesse.bookssummary.run.plist`
が `src/run_local.sh` を起動する。生成物は 100_Inbox に直接書き込まれ、
`infographics/` と `docs/` は commit して `main` に push される。

**インストール手順 (初回のみ):**
```bash
cp src/com.oshomadesse.bookssummary.run.plist \
   ~/Library/LaunchAgents/com.oshomadesse.bookssummary.run.plist
launchctl unload ~/Library/LaunchAgents/com.oshomadesse.bookssummary.pull.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.oshomadesse.bookssummary.pull.plist
launchctl load ~/Library/LaunchAgents/com.oshomadesse.bookssummary.run.plist
# スリープ中に発火しないための wake 予約
sudo pmset repeat wakeorpoweron MTWRFSU 06:55:00
```

**ログ:**
- `data/integrated/run_local.log` (スクリプト本体のログ)
- `~/Library/Logs/BooksSummary/run-stdout.log` / `run-stderr.log` (LaunchAgent 側)

### フォールバック: GitHub Actions 手動実行
- `.github/workflows/daily_workflow.yml` は `workflow_dispatch` のみ有効
- GitHub の Actions タブから手動キック可能
- CI 実行時はノートを `artifacts/Books-YYYY-MM-DD.md` に書き出して push
- 次回ローカル `run_local.sh` の先頭で自動的に `100_Inbox/` に回収される

### ⚠️ ローカル運用の注意点
1. **スリープ中は LaunchAgent が発火しない**。`pmset repeat wakeorpoweron` で
   06:55 に wake 予約すること。蓋閉じ・バッテリー駆動だと wake しないケースあり。
2. **スリープ復帰直後は Wi-Fi 未接続**の瞬間がある。`run_local.sh` は
   `curl https://github.com` で最大 60 秒待つ。
3. **LaunchAgent は login shell ではない** ため `.zshrc` の PATH が読まれない。
   plist の `EnvironmentVariables` と `run_local.sh` 冒頭で `PATH` を明示している。
   Python が pyenv 管理なら `PYTHON_BIN` を `$HOME/.pyenv/shims/python3` に書き換える。
4. **git push 認証**: osxkeychain または ssh-agent が前提。一度ログインシェルから
   `git push` を成功させておけば keychain に乗り LaunchAgent からも使える。
5. **多重起動防止**: `/tmp/books-summary-run.lock` を `mkdir` で atomic ロック。
6. **失敗時の通知**: Python 例外は既存ロジックで LINE に流れる。bash 段階の失敗は
   `run_local.sh` の `notify_line_failure` が `.env` からトークンを拾って送る。
