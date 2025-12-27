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
        echo "[$DATE] Moving artifacts to $INBOX_DIR..." >> "$LOG_FILE"
        
        # Move files instead of copying to avoid duplication
        # Using mv to move files from artifacts to Inbox
        mv "$ARTIFACTS_DIR"/Books-*.md "$INBOX_DIR/" 2>> "$LOG_FILE"
        MOVE_EXIT=$?
        
        if [ $MOVE_EXIT -eq 0 ]; then
            echo "[$DATE] ✅ Move successful." >> "$LOG_FILE"
            
            # Clean up artifacts from git to keep the repo clean
            echo "[$DATE] Cleaning up artifacts from git..." >> "$LOG_FILE"
            cd "$REPO_DIR" || exit 1
            
            # Stage the deletion (since files are already moved/deleted from disk)
            git add -u artifacts/
            
            # Commit and push the deletion
            git commit -m "🧹 Cleanup artifacts [skip ci]" >> "$LOG_FILE" 2>&1
            git push >> "$LOG_FILE" 2>&1
            
        else
            echo "[$DATE] ❌ Move failed with exit code $MOVE_EXIT." >> "$LOG_FILE"
        fi
    fi

else
    echo "[$DATE] ❌ Pull failed with exit code $EXIT_CODE." >> "$LOG_FILE"
fi
