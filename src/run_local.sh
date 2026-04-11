#!/bin/bash
# ==========================================================================
# books-summary: ローカル日次ワークフロー実行スクリプト
#
# LaunchAgent com.oshomadesse.bookssummary.run から毎朝 07:00 JST に起動され、
# integrated_reading_workflow.py をローカルで実行して生成物を main に push する。
#
# 置き換え前: auto_pull.sh + post-merge フック + CI 上での生成
# 置き換え後: ローカルで生成 → commit & push（本スクリプト）
# ==========================================================================

set -uo pipefail

# ---- 設定 ----
REPO_DIR="/Users/seihoushouba/Documents/Oshomadesse-pc/11_Engineering/📖 books-summary"
VAULT_ROOT="/Users/seihoushouba/Documents/Oshomadesse-pc"
INBOX_DIR="$VAULT_ROOT/100_Inbox"
BRANCH="main"
LOG_FILE="$REPO_DIR/data/integrated/run_local.log"
LOCK_DIR="/tmp/books-summary-run.lock"

# LaunchAgent は login shell ではないため PATH を明示する
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.pyenv/shims"
export LANG="ja_JP.UTF-8"

# 必要に応じて pyenv 等のパスに書き換える（例: $HOME/.pyenv/shims/python3）
PYTHON_BIN="python3"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

# ---- 多重起動防止 (mkdir は atomic) ----
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "⚠️  既に実行中のため終了: $LOCK_DIR"
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

# ---- LINE フォールバック通知（bash レベルの失敗用）----
notify_line_failure() {
    local reason="$1"
    local env_file="$REPO_DIR/.env"
    [ -f "$env_file" ] || return 0
    local token to
    token=$(grep -E '^LINE_CHANNEL_ACCESS_TOKEN=' "$env_file" | head -n1 | cut -d= -f2- | tr -d '"')
    to=$(grep -E '^LINE_TO=' "$env_file" | head -n1 | cut -d= -f2- | tr -d '"')
    [ -n "$token" ] && [ -n "$to" ] || return 0
    curl -sS -X POST https://api.line.me/v2/bot/message/push \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"to\":\"$to\",\"messages\":[{\"type\":\"text\",\"text\":\"📚 books-summary run_local.sh 失敗: $reason\"}]}" \
        >> "$LOG_FILE" 2>&1 || true
}

log "======== run_local.sh 開始 ========"

# ---- リポジトリへ移動 ----
if ! cd "$REPO_DIR"; then
    log "❌ cd に失敗: $REPO_DIR"
    notify_line_failure "cd failed"
    exit 1
fi

# ---- ネットワーク疎通確認（スリープ復帰直後は Wi-Fi 未接続の瞬間がある）----
log "🌐 ネットワーク疎通確認..."
NET_OK=0
for i in $(seq 1 10); do
    if curl -sSfI --max-time 5 https://github.com >/dev/null 2>&1; then
        NET_OK=1
        break
    fi
    sleep 6
done
if [ "$NET_OK" -ne 1 ]; then
    log "❌ ネット接続タイムアウト（約60s）"
    notify_line_failure "network unreachable"
    exit 1
fi
log "✅ ネット接続 OK"

# ---- リモート最新を取り込み（CI 手動実行分などを吸収）----
log "⬇️  git fetch / pull --ff-only origin $BRANCH"
git fetch origin "$BRANCH" >> "$LOG_FILE" 2>&1 || log "⚠️  git fetch 失敗（継続）"
git pull --ff-only origin "$BRANCH" >> "$LOG_FILE" 2>&1 || log "⚠️  git pull --ff-only 失敗（継続）"

# ---- CI が置いた artifacts/Books-*.md を 100_Inbox に回収 ----
# （通常ルートでは artifacts/ は使わないが、workflow_dispatch で手動 CI を叩いた
#   場合のフォールバックとして吸収する）
shopt -s nullglob
artifact_notes=("$REPO_DIR"/artifacts/Books-*.md)
shopt -u nullglob
if [ "${#artifact_notes[@]}" -gt 0 ]; then
    log "📦 artifacts/Books-*.md を $INBOX_DIR へ回収 (${#artifact_notes[@]}件)"
    mkdir -p "$INBOX_DIR"
    for f in "${artifact_notes[@]}"; do
        mv -f "$f" "$INBOX_DIR/" >> "$LOG_FILE" 2>&1 || true
    done
    git add -u artifacts/ >> "$LOG_FILE" 2>&1 || true
    if ! git diff --cached --quiet; then
        git commit -m "🧹 Cleanup artifacts [skip ci]" >> "$LOG_FILE" 2>&1 || true
    fi
fi

# ---- ワークフロー用の環境変数 ----
export PUBLIC_EXPORT_DIR="docs"
export PUBLIC_BASE_URL="https://oshomadesse.github.io/books-summary/"
export PUBLIC_GIT_AUTO_PUSH="1"
export PUBLIC_PAGES_WAIT_TIMEOUT="300"
export VAULT_ROOT
export WORKFLOW_START_TIME="$(date +%s)"

# ---- メイン: 統合ワークフロー実行 ----
log "🚀 integrated_reading_workflow.py 実行"
if ! "$PYTHON_BIN" "$REPO_DIR/src/integrated_reading_workflow.py" >> "$LOG_FILE" 2>&1; then
    log "❌ integrated_reading_workflow.py が非0終了"
    notify_line_failure "workflow python exited non-zero"
    exit 1
fi
log "✅ integrated_reading_workflow.py 完了"

# ---- 生成物を commit ----
log "📝 変更ステージング (infographics/ docs/)"
git add infographics/ docs/ >> "$LOG_FILE" 2>&1 || true

if git diff --cached --quiet; then
    log "ℹ️  追加コミット対象なし（Step5 の内部 push で既に反映済みの可能性）"
else
    git commit -m "📚 Daily reading update [skip ci]" >> "$LOG_FILE" 2>&1 \
        || log "⚠️  git commit 失敗"
fi

# ---- push リトライ（2/4/8/16 秒バックオフ）----
push_with_retry() {
    local delays=(2 4 8 16)
    local attempt
    for attempt in 0 1 2 3 4; do
        if git push origin "$BRANCH" >> "$LOG_FILE" 2>&1; then
            log "✅ git push 成功 (attempt $((attempt + 1)))"
            return 0
        fi
        log "⚠️  git push 失敗 (attempt $((attempt + 1)))"
        # non-fast-forward を想定して rebase で一度だけ救済
        git pull --rebase origin "$BRANCH" >> "$LOG_FILE" 2>&1 || true
        if [ "$attempt" -lt 4 ]; then
            sleep "${delays[$attempt]}"
        fi
    done
    return 1
}

if ! push_with_retry; then
    log "❌ git push を諦めました"
    notify_line_failure "git push failed after retries"
    exit 1
fi

log "======== run_local.sh 完了 ========"
