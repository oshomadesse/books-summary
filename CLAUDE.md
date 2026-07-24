---
tags:
  - engineering
  - books-summary
project_name: 📖 書籍要約くん
summary: 毎朝AIが本を選定しインフォグラフィック付きサマリーをDiscord通知、書籍ノートをLLMリンクでグラフ化
---
# 読書サマリー自動生成システム（books-summary）

## 目的
毎朝7:00に「今日読むべき本」をAIが自動選定・リサーチし、インフォグラフィック付きのサマリーを生成して Discord `#📚06_books` に通知する。書籍ノートは Obsidian に蓄積され、関連書籍どうしが wikiリンクでグラフとして繋がる。

## システム概要（2026-07-24 第3弾: 06_Books 移設・Discord 版）
**クラウドの Claude Routine** が毎朝 7:00 JST に起動し、選書→リサーチ→図解生成→ノート作成→既読本との LLM リンク→`main` push までを Claude 1本で完結。push を受けた **GitHub Actions が Pages 配信と Discord/LINE 通知**を行い、ローカル Mac は 7:20 に pull してノートを vault へ移送・逆リンクするだけ。**外部 AI API 不使用**（Claude サブスク内）。

### アーキテクチャ
```
① Claude Routine "daily-reading-summary"
   (trig_01UUowz2BR5ao6tvqc8URNbD / cron 0 22 * * * UTC = 毎朝7:00 JST / claude-sonnet-5)
   ROUTINE.md の手順: 選書(state/books_read.json 参照)
   → Web Deep Research → infographics/ に HTML 生成
   → 100_Inbox/Books-YYYY-MM-DD.md 生成
   → Step4.5: 関連書籍を既読リストと意味照合し [[Books-YYYY-MM-DD|書名]] リンク
   → state/books_read.json 追記 + state/latest.json 更新（related_dates 含む）
   → main へ commit & push（[skip ci] 禁止）
      ↓ push (state/latest.json 変更で発火)
② GitHub Actions .github/workflows/daily-notify.yml
   job deploy: infographics/ を GitHub Pages へ Actions 配信（URL は従来互換）
   job notify: Pages 200 待ち → Discord Embed ＋ [✅確認(bookconfirm:date)] [🔍詳細(bookdetail:date)]（両方紫）
               → LINE Flex（push時のみ発火＝手動テストでは送らない。continue-on-error・並走中、安定したら廃止）
   (secrets: DISCORD_BOT_TOKEN / LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID)
      ↓
③ ローカル Mac: LaunchAgent com.oshomadesse.bookssummary.pull (毎朝7:20 JST)
   ~/.local/bin/books-summary-pull.sh:
   fetch → brctl ハイドレート → merge（未push有なら rebase 回収）
   → post_pull: ノートを vault 100_Inbox へ移送＋[skip ci]コミット
   → backlink_books.py が related_dates の各ノートに逆リンク追記（brctl 前置き）
      ↓
④ Discord interaction（随時）: .nexus-discord デーモンが .nexus.json の名札で
   src/discord_books.py へ委譲。
   - ✅確認 → 図解URLを ephemeral で返す（URLボタンは灰色固定のため紫化はこの方式）
   - 🔍詳細 → モーダル質問 → headless NEXUS が当日ノートを読んで深掘り回答
   - 常設メニュー「🔎 検索」→ モーダル「特定の本やテーマを伝える」→ NEXUS が
     既読ノート照合 or Web提案で該当本を提出（menu_keeper が60秒ごと最下部を維持、
     message_id は state/discord_menu.json）
   - 通常発言は普通の NEXUS 対話
```

### 管理ポイント
| 対象 | 場所 |
|---|---|
| Routine の指示書 | `ROUTINE.md`（リポ直下。編集して push すれば翌朝から反映） |
| Routine の管理画面 | https://claude.ai/code/routines |
| 読了リスト（唯一の正・append-only） | `state/books_read.json` |
| 当日メタ（通知・逆リンクが読む） | `state/latest.json` |
| 通知 workflow | `.github/workflows/daily-notify.yml` + リポ secrets |
| Discord ハンドラ | `src/discord_books.py`（変更したら Discord で `/restart`） |
| チャンネル紐付け | `.nexus.json`（channel_id 1528306664422510644 = `#📚06_books`） |
| ローカル同期 | `~/Library/LaunchAgents/com.oshomadesse.bookssummary.pull.plist`（原本 `src/`） |
| 設計正本 | `docs/設計記録.md`（ライフサイクル10要素）・依存グラフは `README.md` |

## ディレクトリ構成（graph-scaffold 準拠: 読む=.claude／行う=src／書く=state／見る=docs）
```
06_Books/
├── ROUTINE.md            # ★ クラウド Routine の実行指示書（システムの心臓部）
├── README.md             # 表紙＋mermaid 依存グラフ（変更時は同じ push で更新）
├── CLAUDE.md             # このファイル
├── .nexus.json           # Discord チャンネル名札＋handler 指定
├── .github/workflows/daily-notify.yml
├── state/
│   ├── books_read.json   # 既読リスト（append-only の正本）
│   ├── latest.json       # 当日メタ
│   ├── backfill/         # 一括リンクの判定台帳とバックアップ
│   └── legacy/           # 旧システムのデータ遺物
├── 100_Inbox/            # Routine の生成先（pull 後に vault へ移送、通常は .gitkeep のみ）
├── infographics/         # 図解 HTML マスター = GitHub Pages 配信ルート（Actions 配信）
├── docs/                 # 設計記録.md・更新記録.md（人間ドキュメント専用）
└── src/
    ├── books-summary-pull.sh       # pull＋移送＋逆リンク（原本。配備先 ~/.local/bin）
    ├── com.oshomadesse.bookssummary.pull.plist
    ├── backlink_books.py           # 逆リンク追記（launchd 経路・brctl 前置き）
    ├── discord_books.py            # 🔍詳細ボタン→モーダル→NEXUS 注入ハンドラ
    ├── tools/backfill_links.py     # 過去ノート一括リンク（scan/apply・1回もの）
    └── legacy/                     # 旧ローカル実行システム（参照用・実行されない）
```

## 運用

### 通常運用
何もしなくてよい。7:00 生成 → 7:0x Discord 通知 → 7:20 Mac が pull・移送・逆リンク。
Mac スリープ中でも生成と通知は止まらない（クラウド完結）。pull は次回起動時に追いつく。

### 手動で今すぐ実行したいとき
このリポで Claude Code から `RemoteTrigger` の `run`（trigger_id: `trig_01UUowz2BR5ao6tvqc8URNbD`）。
または https://claude.ai/code/routines から手動実行。通知だけ再送するなら
`gh workflow run daily-notify.yml --repo oshomadesse/books-summary`。

### ローカルに反映されないとき
```bash
bash ~/.local/bin/books-summary-pull.sh   # ログ: ~/Library/Logs/BooksSummary/pull.log
```

### 出力フォーマット・選書条件を変えたいとき
`ROUTINE.md` を編集して push するだけ。

### 過去ノートの関連書籍を一括リンクしたいとき（バックフィル）
```bash
python3 src/tools/backfill_links.py scan          # 照合台帳を再生成
# state/backfill/decisions.json の ambiguous を LLM 判定（accepted/rejected）
python3 src/tools/backfill_links.py apply --dry-run
python3 src/tools/backfill_links.py apply         # 適用前に state/backfill/backup/ へ自動バックアップ
```

## ⚠️ 過去の障害から学んだ制約（重要）
1. **このリポジトリは iCloud「デスクトップと書類」同期の配下にある。**
   iCloud の「ストレージ最適化」がファイル実体を夜間にクラウド退避させ、
   launchd 起動のプロセスは退避ファイルを読めず `EDEADLK (Resource deadlock avoided)`
   で死ぬ（2026-07-04〜07 の 4 日連続障害の根因）。
2. その対策として **`.git` の実体は `~/.gitdirs/books-summary` に移設済み**
   （ワークツリー直下の `.git` は `gitdir:` 参照ファイル）。clone し直す場合はこの構成を再現すること。
3. launchd から既存ファイルを読む処理は、**必ず `brctl download` でハイドレーションしてから**
   （pull の merge も、backlink_books.py のノート読みもこの型。2026-07-12〜17 の6日連続障害の教訓）。
4. iCloud は同期競合時に **`.git` 参照ファイルを「.git 2」へリネームして実質消す**ことがある
   （2026-07-08〜11 の4日連続障害）。pull スクリプトは `--git-dir` 明示＋参照ファイル毎回自己修復で対処済み。
5. **`src/books-summary-pull.sh` を編集したら `~/.local/bin/` へも複製する**（launchd は配備先を実行する）。

## 旧システム（src/legacy/・state/legacy/、2026-07-07 停止）
Gemini + GPT-5 + Claude API をローカル LaunchAgent で回していた第1弾の遺物。参照用に残置、実行されない。
Google Sheets の読了リストは `state/books_read.json` へ移行済み（シートは凍結）。
