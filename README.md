# books-summary

> 目的: 毎朝7:00に「今日読むべき本」をAIが選定・リサーチし、図解付きサマリーをDiscordへ届けて読書習慣と知識インプットを自動化する。
> 完成条件: 成果と評価は最低1つの Reality Anchor（外部事実）に接続する。

**第3弾まで実装済み**（第1弾: ローカルAPI版 → 第2弾: クラウドRoutine+LINE版 → 第3弾: 06_Books移設・Discord通知・LLM関連リンク版）。変更履歴は [docs/更新記録.md](docs/更新記録.md)

## フォルダとライフサイクル10要素（State / Node の置き場）

| 原則 | フォルダ | ライフサイクル要素 | 役割 |
|---|---|---|---|
| 読む | `.claude/` | Node定義 | AIが読む指示書（`.claude/skills/daily-reading/SKILL.md` はクラウドRoutineが読む本体） |
| 行う | `src/` | Node実装 | 機械が実行する処理系 |
| 書く | `state/` | State | 機械が書き残す記憶（生成物は `state/infographics/` 図解HTML・`state/inbox/` ノート一時置き場） |
| 見る | `docs/` | State | 人が読む記録（公開HTMLは `state/infographics/` から Pages Actions 配信） |
| 企む | `plans/` | State（一時） | NEXUSの実行計画。コミットメッセージに全文転記して削除 |

上記5フォルダはライフサイクル10要素のうち State / Node の置き場。Edgeは専用フォルダを持たない。`CLAUDE.md`の規則とスクリプト内の分岐が実体。

Judgementも専用フォルダを持たない。Edge上の条件分岐として実装内に存在する。

Humanノードも専用フォルダを持たない。人間本人と通知チャネル（Discord `#📚06_books`）が実体。

正本（append-only）: `state/books_read.json`（既読リスト。Routineが毎日1件追記、削除しない）

## ライフサイクル10要素

設計正本は [docs/設計記録.md](docs/設計記録.md)。ここでは一覧のみ示す。

| # | 要素 | 一行説明 |
|---|---|---|
| 0 | Purpose | 目的関数と制約。改訂できるのは9. Humanのみ |
| 1 | Start | 開始条件・トリガー・入力の型 |
| 2 | State | 正本・証跡・checkpoint。run跨ぎで残すものがloop9の成立条件 |
| 3 | Node | Agent/Tool/Human/Evaluatorの責務と入出力 |
| 4 | Edge | 遷移条件と接続 |
| 5 | Judgement | 機械が即決する分岐。即決できないなら7か9へ |
| 6 | Loop | node再試行の戻り先・上限・出口 |
| 7 | Verification | 3rd partyによる最終関門（評価者＝作り手と別ベンダー）。固定基準で裁く。自己採点しない |
| 8 | End | 全runが必ず到達する終端（正常・エラー両方） |
| 9 | Human | Endの外。通知を受け、feedbackが次runのStartになる。Validation（妥当性確認）層 |

3層ループは時間スケールが異なる。

- loop6（node再試行）: 秒〜分、同一run内
- loop7（verification差し戻し）: run単位
- loop9（human）: 時間〜日、run跨ぎの非同期

## Node種別

| 種別 | 色 | 表現 |
|---|---|---|
| Agent | 紫 | AIによる処理 |
| Tool | 緑 | スクリプトによる処理 |
| Human | 桃 | 人間による入力・判断・通知受領 |
| Evaluator | 橙 | 六角形のVerification（3rd party）担当者 |
| State | 青 | 円筒形の状態保存先 |

Judgementは Edge上の条件分岐であり Node ではない。

## 依存グラフ

```mermaid
flowchart TD
    %% この図はHumanがグラフを監視する唯一の場所。Node・Edge・Loop・State変更時は同じpushで必ず更新する（.claude/rules/docs-sync.md）。
    Start([Start: 毎朝7:00 JST クラウドRoutine起動]):::startend --> G{Judgement: books_read.jsonに今日のdateあり?}:::judgement
    G -->|あり・生成済み| EndSkip([End: 何もせず終了]):::startend
    G -->|なし| A["Agent: 選書→Deep Research→図解HTML→ノート生成<br>.claude/skills/daily-reading/SKILL.md"]:::agent
    A --> L[Agent: 関連書籍を既読リストとLLM意味照合し wikiリンク Step4.5]:::agent
    L --> SR[("State: state/books_read.json / state/latest.json<br>state/infographics/ / state/inbox/ ノート")]:::state
    SR --> P[Tool: git push origin main]:::tool
    P -->|reject時 rebaseして最大3回| P
    P --> W[Tool: GitHub Actions daily-notify.yml]:::tool
    W --> PG[(State: GitHub Pages state/infographics配信 URL不変)]:::state
    W --> DN[Human: Discord #books 通知 Embed+確認/詳細ボタン 両方紫]:::human
    DN -->|✅確認| CF[Tool: discord_books.py 図解URLをephemeral返信]:::tool
    MK[Tool: menu_keeper 60秒番人 常設🔎検索メニュー最下部維持]:::tool --> SM[Human: 検索モーダル 本やテーマを伝える]:::human
    SM --> DB2[Tool: discord_books.py → headless NEXUS注入]:::tool
    DB2 --> SA[Agent: 既読照合orWeb提案で該当本を提出]:::agent
    W -->|Discord送信失敗| ErrW([End: workflow失敗 → GitHub通知メール]):::startend
    Start2([Start: 毎朝7:20 launchd pull]):::startend --> PU[Tool: books-summary-pull.sh fetch→brctl→merge]:::tool
    PU -->|失敗5回| ErrP([End: pull失敗ログ 翌回自己回復]):::startend
    PU --> MV[Tool: state/inbox のノートをvault 100_Inboxへ移送 + skip-ci commit push]:::tool
    MV --> BL[Tool: backlink_books.py 逆リンク追記 brctl前置き]:::tool
    BL --> OB[(State: Obsidianグラフ 書籍ノート相互リンク)]:::state
    DN -->|🔍詳細→モーダル質問| HD[Human: しょーまの質問]:::human
    HD --> DB[Tool: discord_books.py → headless NEXUS注入]:::tool
    DB --> DA[Agent: ノートを読んで深掘り回答<br>応答の届き先は経路で分かれる<br>StartC→#📚06_books／StartG→#🤖00_general]:::agent
    StartC([Start: #📚06_books へ通常発言]):::startend --> DA
    StartG([Start: 📱ショートカット → #🤖00_general<br>名札 desc 一致/Haiku分類で本リポへ転送 cwdは本リポ]):::startend --> DA
    DA -.->|loop9・翌日の選書や指示に反映| Start

    classDef agent fill:#e4d4ff,stroke:#6f42c1,color:#222
    classDef tool fill:#d7f5df,stroke:#2e8b57,color:#222
    classDef human fill:#ffd6e7,stroke:#c94f7c,color:#222
    classDef evaluator fill:#ffe0b2,stroke:#d97706,color:#222
    classDef state fill:#d6eaff,stroke:#2878b5,color:#222
    classDef judgement fill:#fff3b0,stroke:#b8860b,color:#222
    classDef startend fill:#e8e8e8,stroke:#555,color:#222
```

## エラー出口一覧

出口の届き先が空欄の行があってはなりません。

| ノード | 失敗条件 | 最大試行 | 出口の届き先 |
|---|---|---:|---|
| Routine（クラウド） | push reject | 3 | Routine実行履歴（claude.ai/code/routines）＋翌朝Discord通知が来ないことで発覚 |
| daily-notify.yml Discord送信 | HTTP 2xx以外 | 1 | workflow失敗 → GitHubの失敗通知メール |
| books-summary-pull.sh | fetch/merge失敗 | 5 | `~/Library/Logs/BooksSummary/pull.log`（翌回実行で自己回復） |
| ノート移送コミットのpush | reject | 3 | pull.log（次回実行の未pushコミット回収で自己回復） |
| backlink_books.py | ノート不在・退避タイムアウト | 各1 | pull.log（部分失敗でも pull は成功扱い） |
| discord_books.py | 台帳未検出・図解URL不明 | 1 | Discord上でエラー返答（ephemeral） |
| menu_keeper | メニュー貼り直し失敗 | 60秒ごと再試行 | daemon.log（次周期で自己回復） |

## むずかしい言葉なし版

朝7時にクラウドのAIが本を1冊選んで調べ、図解ページと読書ノートを作って保存する。7時すぎにスマホのDiscordへ「今日の一冊」が届き、✅確認を押すと図解が開き、🔍詳細を押すと質問箱が出てAIと深掘りできる。7時20分にMacが自動で最新を取り込み、ノートはObsidianの受信箱に移って、関連する過去の本と線で繋がる。壊れたときはDiscordに通知が来ないことで気づけて、ログは `~/Library/Logs/BooksSummary/` にある。
