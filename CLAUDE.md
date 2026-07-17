---
tags:
  - engineering
  - books-summary
project_name: 📖 書籍要約くん
summary: 毎朝AIが本を選定しインフォグラフィック付きサマリーをLINE通知
---
# 読書サマリー自動生成システム

## 目的
毎朝7:00に「今日読むべき本」をAIが自動選定・リサーチし、インフォグラフィック付きのサマリーを生成してLINEに通知することで、読書習慣の定着と知識のインプットを効率化する。

## システム概要（2026-07-07 全面刷新: Claude Routine 版）
**クラウドの Claude Routine** が毎朝 7:00 JST に起動し、選書からリサーチ、
インフォグラフィック生成、ノート作成、`main` への push までを **Claude 1本**で完結させる。
LINE 通知は push を受けた GitHub Actions が行い、ローカル Mac は `git pull` するだけ。
**外部 AI API（Gemini / GPT-5 / Anthropic API）は不使用**。Claude サブスクリプション内で動く。

### アーキテクチャ
```
┌──────────────────────────────────────────────────────────┐
│ ① Claude Routine "daily-reading-summary"                 │
│    (trig_01UUowz2BR5ao6tvqc8URNbD / cron 0 22 * * * UTC  │
│     = 毎朝 07:00 JST / model: claude-sonnet-5)           │
│    リポジトリ直下の ROUTINE.md の手順を実行:              │
│      選書(data/books_read.json 参照)                      │
│      → Web Deep Research                                  │
│      → infographics/ + docs/ に HTML 生成                 │
│      → 100_Inbox/Books-YYYY-MM-DD.md 生成                 │
│      → data/books_read.json 追記 + data/latest.json 更新  │
│      → main へ commit & push（[skip ci] 禁止）            │
└──────────────────────────┬───────────────────────────────┘
                           │ push (data/latest.json 変更)
                           ▼
┌──────────────────────────────────────────────────────────┐
│ ② GitHub Actions .github/workflows/line-notify.yml       │
│    GitHub Pages の 200 応答を待機 (max 300s)              │
│    → LINE Flex Message 送信                               │
│    (secrets: LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID)    │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ ③ ローカル Mac: LaunchAgent                               │
│    com.oshomadesse.bookssummary.pull (毎朝 07:20 JST)     │
│    ~/.local/bin/books-summary-pull.sh が git pull のみ実行│
│    → Obsidian Vault にノートが同期される                  │
└──────────────────────────────────────────────────────────┘
```

### 管理ポイント
| 対象 | 場所 |
|---|---|
| Routine の指示書 | `ROUTINE.md`（リポジトリ直下。編集すれば翌朝から反映） |
| Routine の管理画面 | https://claude.ai/code/routines |
| 読了リスト（唯一の正） | `data/books_read.json`（旧 Google Sheets は 2026-07-07 で凍結） |
| LINE 通知 | `.github/workflows/line-notify.yml` + リポジトリ secrets |
| ローカル同期 | `~/Library/LaunchAgents/com.oshomadesse.bookssummary.pull.plist`（原本は `src/` に保管） |

## ディレクトリ構成
```
📖 books-summary/
├── ROUTINE.md                # ★ クラウド Routine の実行指示書（システムの心臓部）
├── .github/workflows/
│   └── line-notify.yml       # LINE Flex 通知（data/latest.json の push で発火）
├── data/
│   ├── books_read.json       # 読了リスト（選書の除外に使用、Routine が毎日追記）
│   └── latest.json           # 当日分メタ情報（LINE 通知 Action が読む）
├── 100_Inbox/                # Books-YYYY-MM-DD.md（Routine が生成、git 管理、Obsidian から閲覧）
├── infographics/             # 生成 HTML 図解（マスター）
├── docs/                     # GitHub Pages 公開用（https://oshomadesse.github.io/books-summary/）
└── src/                      # 旧ローカル実行システム（→「旧システム」参照）
    └── com.oshomadesse.bookssummary.pull.plist  # pull 専用 LaunchAgent 定義（現役）
```

## 運用

### 通常運用
何もしなくてよい。毎朝 7:00 JST に Routine が走り、7:20 に Mac が pull する。
Mac がスリープ中でも**生成は止まらない**（クラウド実行）。pull は次回起動時に追いつく。

### 手動で今すぐ実行したいとき
このリポジトリで Claude Code から `RemoteTrigger` の `run`（trigger_id: `trig_01UUowz2BR5ao6tvqc8URNbD`）を叩く。
または https://claude.ai/code/routines から手動実行。

### ローカルに反映されないとき
```bash
bash ~/.local/bin/books-summary-pull.sh   # ログ: ~/Library/Logs/BooksSummary/pull.log
```

### 出力フォーマットを変えたいとき
`ROUTINE.md` を編集して push するだけ（選書条件・リサーチ項目・インフォグラフィックの
デザイン指示・ノートテンプレートが全部そこにある）。

## ⚠️ 過去の障害から学んだ制約（重要）
1. **このリポジトリは iCloud「デスクトップと書類」同期の配下にある。**
   iCloud の「ストレージ最適化」がファイル実体を夜間にクラウド退避させ、
   launchd 起動のプロセスは退避ファイルを読めず `EDEADLK (Resource deadlock avoided)`
   で死ぬ（2026-07-04〜07 の 4 日連続障害の根因）。
2. その対策として **`.git` の実体は `~/.gitdirs/books-summary` に移設済み**
   （ワークツリー直下の `.git` は `gitdir:` 参照ファイル）。iCloud の外なので退避されない。
   リポジトリを clone し直す場合はこの構成を再現すること。
3. ワークツリー側のファイルも退避され得る。launchd から中身を読む処理は追加しないこと。
   **pull(merge) も既存ファイルの更新時に現物を読むため、対象が退避済みだと死ぬ**
   （2026-07-12〜17 の 6 日連続障害。「新規ファイルを書くだけなら安全」は半分誤りだった）。
   → pull スクリプトは fetch 後にリモート差分ファイルを `brctl download` で
   ハイドレーションしてから merge する（brctl はデーモンへの依頼なので launchd からでも実体化できる）。
4. iCloud は同期競合時に **`.git` 参照ファイルを「.git 2」へリネームして実質消す**ことがある
   （2026-07-08〜11 の 4 日連続障害）。
   → pull スクリプトは `--git-dir` 明示で参照ファイルに依存せず、参照ファイル自体も毎回自己修復する。

## 旧システム（src/ 以下、2026-07-07 停止）
Gemini(推薦) + GPT-5(リサーチ) + Claude API(図解) をローカル Mac の LaunchAgent で
毎朝実行していた構成。iCloud 退避問題で恒常的に不安定だったため Routine 版へ全面移行した。
- `src/*.py` は参照用に残置（フォーマットの出典。実行はされない）
- LaunchAgent `com.oshomadesse.bookssummary.run` は削除済み
- `.github/workflows/daily_workflow.yml`（API 依存の CI フォールバック）は削除済み
- Google Sheets の読了リストは `data/books_read.json` へ移行済み（シートは凍結）
