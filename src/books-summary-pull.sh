#!/bin/bash
# books-summary: main を pull するだけの日次ジョブ。
# 生成・push はクラウド Routine (daily-reading-summary) 側で完結しており、
# ここは Obsidian 用にローカルへ同期するだけ。失敗しても翌回で回復する。
#
# ワークツリーは iCloud「デスクトップと書類」同期の配下にあり、2つの地雷がある:
#   (a) .git 参照ファイルが競合リネーム (".git 2") で消える
#       → git-dir を明示 + 参照ファイルを自己修復（2026-07-08〜11 障害）
#   (b) 既存ファイルの実体が夜間に退避 (dataless) され、merge が更新対象を
#       読めず EDEADLK で中断 → fetch 後に差分ファイルを brctl download で
#       取り寄せてから merge（2026-07-12〜17 障害）
# .git の実体は iCloud の外 (~/.gitdirs/books-summary) にあり退避されない。
REPO="/Users/seihoushouba/Documents/Oshomadesse-pc/11_Engineering/01_個人/📖 books-summary"
GIT_DIR="$HOME/.gitdirs/books-summary"
LOG_DIR="$HOME/Library/Logs/BooksSummary"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pull.log"
log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
GIT(){ git --git-dir="$GIT_DIR" --work-tree="$REPO" "$@"; }

log "== pull 開始 =="

if [ ! -d "$GIT_DIR" ]; then
  log "❌ $GIT_DIR が存在しない。構成を確認して"
  exit 1
fi

# (a) .git 参照ファイルの自己修復（新規ファイルの書き込みは iCloud 退避と無関係で安全）
if [ ! -e "$REPO/.git" ]; then
  echo "gitdir: $GIT_DIR" > "$REPO/.git"
  log "🔧 .git 参照ファイルが消えていたので再作成した"
fi
# iCloud が競合時に作る ".git 2" 等のコピーは削除（放置すると紛らわしいだけ）
find "$REPO" -maxdepth 1 -type f -name ".git [0-9]" -delete 2>/dev/null

# (b) リモートとの差分ファイルのうち iCloud 退避 (dataless) されたものを取り寄せる。
# brctl download はデーモンへの依頼なので launchd からでも実体化できる
# （launchd プロセス自身の read は透過DLされず EDEADLK になる、が回避される）。
hydrate_changed(){
  GIT -c core.quotepath=false diff --name-only HEAD origin/main 2>>"$LOG" | \
  while IFS= read -r f; do
    p="$REPO/$f"
    [ -f "$p" ] || continue
    ls -lO "$p" 2>/dev/null | grep -q dataless || continue
    log "💧 iCloud 退避を検出 → 実体を取り寄せ: $f"
    /usr/bin/brctl download "$p" 2>>"$LOG"
    for j in {1..24}; do  # 最大 120 秒待つ
      ls -lO "$p" 2>/dev/null | grep -q dataless || break
      sleep 5
    done
  done
}

# スリープ復帰直後の未接続対策（最大60秒待つ）
for i in {1..12}; do
  curl -s --max-time 4 https://github.com >/dev/null 2>&1 && break
  sleep 5
done

cd "$REPO" || { log "❌ ワークツリーに cd できない"; exit 1; }
for i in {1..5}; do
  if GIT fetch origin main >> "$LOG" 2>&1; then
    hydrate_changed
    if GIT merge --ff-only origin/main >> "$LOG" 2>&1; then
      log "✅ pull 成功"
      exit 0
    fi
  fi
  log "⚠️ pull 失敗 (attempt $i/5) → 30秒待ってリトライ"
  sleep 30
done
log "❌ pull 失敗（次回実行で回復を期待）"
exit 1
