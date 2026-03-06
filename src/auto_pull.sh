#!/bin/bash

# Define variables
REPO_DIR="/Users/seihoushouba/Documents/Oshomadesse-pc/11_Engineering/📖 books-summary"
LOG_FILE="$REPO_DIR/data/integrated/auto_pull.log"
DATE=$(date "+%Y-%m-%d %H:%M:%S")

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

echo "[$DATE] Starting auto-pull..." >> "$LOG_FILE"

# Navigate to repository
cd "$REPO_DIR" || {
    echo "[$DATE] ❌ Failed to cd to $REPO_DIR" >> "$LOG_FILE"
    exit 1
}

# Commit any staged changes (e.g. artifact deletions from post-merge) before pull
# This prevents the deadlock where staged deletions block git pull --rebase
if ! git diff --cached --quiet; then
    echo "[$DATE] 📝 Found staged changes. Committing before pull..." >> "$LOG_FILE"
    git commit -m "🧹 Cleanup artifacts [skip ci]" >> "$LOG_FILE" 2>&1
    git push >> "$LOG_FILE" 2>&1
    echo "[$DATE] ✅ Pre-pull cleanup committed." >> "$LOG_FILE"
fi

# Stash local changes if needed so pull won't fail
STASHED=0
if ! git diff --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    STASH_NAME="auto_pull_$(date +%Y%m%d_%H%M%S)"
    echo "[$DATE] ⚠️ Working tree dirty. Stashing as $STASH_NAME" >> "$LOG_FILE"
    STASH_OUTPUT=$(git stash push -u -m "$STASH_NAME" 2>&1)
    STASH_EXIT=$?
    echo "$STASH_OUTPUT" >> "$LOG_FILE"
    if [ $STASH_EXIT -ne 0 ]; then
        echo "[$DATE] ❌ Failed to stash local changes. Aborting." >> "$LOG_FILE"
        exit 1
    fi
    STASHED=1
fi

# Pull changes
# Using --rebase to avoid merge commits if there are local changes (though artifacts should be clean)
# Using -X theirs to prefer remote changes if conflicts arise in artifacts (unlikely with auto-move)
OUTPUT=$(git pull origin main --rebase 2>&1)
EXIT_CODE=$?

echo "$OUTPUT" >> "$LOG_FILE"


if [ $EXIT_CODE -eq 0 ]; then
    echo "[$DATE] ✅ Pull successful." >> "$LOG_FILE"

    # Artifacts are moved by .git/hooks/post-merge. Just ensure git state is clean.
    ARTIFACTS_DIR="$REPO_DIR/artifacts"

    if [ -d "$ARTIFACTS_DIR" ]; then
        echo "[$DATE] Cleaning up artifacts from git..." >> "$LOG_FILE"
        cd "$REPO_DIR" || exit 1
        
        # Stage deletions
        git add -u artifacts/
        
        # Commit if there are changes
        if ! git diff --cached --quiet; then
            git commit -m "🧹 Cleanup artifacts [skip ci]" >> "$LOG_FILE" 2>&1
            git push >> "$LOG_FILE" 2>&1
            echo "[$DATE] ✅ Git cleanup successful." >> "$LOG_FILE"
        else
            echo "[$DATE] No git cleanup needed." >> "$LOG_FILE"
        fi
    fi

else
    echo "[$DATE] ❌ Pull failed with exit code $EXIT_CODE." >> "$LOG_FILE"
fi

# Restore stashed work if we created one
if [ $STASHED -eq 1 ]; then
    echo "[$DATE] 🔁 Restoring stashed changes..." >> "$LOG_FILE"
    POP_OUTPUT=$(git stash pop --index 2>&1)
    POP_EXIT=$?
    echo "$POP_OUTPUT" >> "$LOG_FILE"
    if [ $POP_EXIT -eq 0 ]; then
        echo "[$DATE] ✅ Stash restored." >> "$LOG_FILE"
    else
        echo "[$DATE] ⚠️ Stash pop resulted in conflicts. Please resolve manually." >> "$LOG_FILE"
    fi
fi
