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

# Pull changes
# Using --rebase to avoid merge commits if there are local changes (though artifacts should be clean)
# Using -X theirs to prefer remote changes if conflicts arise in artifacts (unlikely with auto-move)
OUTPUT=$(git pull origin main --rebase 2>&1)
EXIT_CODE=$?

echo "$OUTPUT" >> "$LOG_FILE"


if [ $EXIT_CODE -eq 0 ]; then
    echo "[$DATE] ✅ Pull successful." >> "$LOG_FILE"

    # Move artifacts to Vault Inbox
    INBOX_DIR="/Users/seihoushouba/Documents/Oshomadesse-pc/100_Inbox"
    ARTIFACTS_DIR="$REPO_DIR/artifacts"

    if [ -d "$ARTIFACTS_DIR" ]; then
        echo "[$DATE] Checking artifacts..." >> "$LOG_FILE"
        
        # Move files if they exist (post-merge hook might have already moved them)
        if ls "$ARTIFACTS_DIR"/Books-*.md 1> /dev/null 2>&1; then
            echo "[$DATE] Moving artifacts to $INBOX_DIR..." >> "$LOG_FILE"
            mv "$ARTIFACTS_DIR"/Books-*.md "$INBOX_DIR/" 2>> "$LOG_FILE"
        else
            echo "[$DATE] No artifacts found to move (already moved by hook?)" >> "$LOG_FILE"
        fi

        # Always try to clean up git (if files are gone from disk but in git index)
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
