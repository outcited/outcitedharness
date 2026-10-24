#!/bin/bash
# SSH-pull PDFs from mac-mini's T7 spool → M5 local.
# Runs every 2-4 hours via launchd. Includes a 10-doc validation gate.
#
# PREREQUISITE: SSH key auth to mac-mini (Sam needs to set this up once).
#   ssh-copy-id samkim@mac-mini.local
#
# Usage: pull_from_mini.sh [source_path]
#   source defaults to /Volumes/T7/pdfs (mac-mini's T7 spool output)

set -euo pipefail

SOURCE="${1:-/Users/samsonkim/mnt/t7/spool/MFG_DOSIER_FACTORY/blobs}"
DEST="/Users/samkim/Harnessv1/results/pdfs-incoming"
VALIDATION_N=10
LOCK="/tmp/pull_from_mini.lock"
LOG="/Users/samkim/Harnessv1/results/pdf-intake-$(date +%Y%m%d).log"

if [ -f "$LOCK" ]; then
    echo "pull already running" >&2
    exit 1
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

echo "=== PDF intake $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"

# Snapshot listing BEFORE the pull: rsync -a preserves source mtimes, so a
# `find -newer` counter can never fire (the bug that hid thousands of
# arriving blobs behind "new PDFs: 0"). With --ignore-existing, the
# before/after set difference IS the new-blob set, mtime-independent.
LIST_BEFORE=$(mktemp /tmp/pull-list-before.XXXXXX)
find "$DEST" -name "*.pdf" 2>/dev/null | sort > "$LIST_BEFORE"

# Pull new PDFs (content-addressed, idempotent)
mkdir -p "$DEST"
rsync -a \
    --partial \
    --timeout=120 \
    --ignore-existing \
    --stats \
    "samsonkim@100.80.101.116:${SOURCE}/" "$DEST/" >> "$LOG" 2>&1 || {
    echo "PULL FAILED — mac-mini unreachable or path wrong" >> "$LOG"
    exit 1
}

# Count new files (set difference, not mtimes)
LIST_AFTER=$(mktemp /tmp/pull-list-after.XXXXXX)
find "$DEST" -name "*.pdf" 2>/dev/null | sort > "$LIST_AFTER"
NEW_LIST=$(comm -13 "$LIST_BEFORE" "$LIST_AFTER")
NEW_COUNT=$(printf '%s' "$NEW_LIST" | grep -c . || true)
echo "new PDFs: $NEW_COUNT" >> "$LOG"
rm -f "$LIST_BEFORE" "$LIST_AFTER"

# Validation gate: if new files arrived, run a 10-doc sanity burn
if [ "$NEW_COUNT" -gt 0 ]; then
    echo "running validation burn ($VALIDATION_N docs)..." >> "$LOG"
    # Pick the first N new PDFs
    printf '%s\n' "$NEW_LIST" | head -"$VALIDATION_N" > /tmp/validation-batch.txt
    
    # TODO: hook into burn_aisle once it supports arbitrary PDF lists
    # For now, just verify the files are readable and non-empty
    PASS=0; FAIL=0
    while read -r pdf; do
        if [ -s "$pdf" ] && [ $(stat -f%z "$pdf") -gt 1000 ]; then
            PASS=$((PASS+1))
        else
            FAIL=$((FAIL+1))
        fi
    done < /tmp/validation-batch.txt
    
    echo "validation: $PASS pass, $FAIL fail (of $VALIDATION_N)" >> "$LOG"
    if [ "$FAIL" -gt "$((VALIDATION_N / 2))" ]; then
        echo "VALIDATION FAILED — >50% of new PDFs are empty/corrupt" >> "$LOG"
        echo "ALERT: validation gate failed — check mac-mini T7 output" >> "$LOG"
        exit 2
    fi
fi

echo "=== intake complete $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
