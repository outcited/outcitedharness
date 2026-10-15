#!/bin/bash
# Nightly backup: local results/ → NAS (cold archive)
# Runs at 2 AM via launchd. Rate-limited so it coexists with extraction.
# Usage: nightly_backup.sh [destination]
#   destination defaults to yosemiteraid5 backup NFS

set -euo pipefail

SOURCE="/Users/samkim/Harnessv1/results"
DEST="${1:-/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/results-archive}"
LOCK="/tmp/nightly_backup.lock"
LOG="/Users/samkim/Harnessv1/results/backup-$(date +%Y%m%d).log"

# Only one backup at a time
if [ -f "$LOCK" ]; then
    echo "backup already running (lock exists)" >&2
    exit 1
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

echo "=== nightly backup $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
echo "source: $SOURCE" >> "$LOG"
echo "dest:   $DEST" >> "$LOG"

# Check destination is reachable (3s timeout)
if ! timeout 3 ls "$DEST" >/dev/null 2>&1; then
    echo "DESTINATION UNREACHABLE — skipping backup (NAS likely down)" >> "$LOG"
    echo "skipped: destination unreachable" >> "$LOG"
    exit 0  # not an error — NAS being down is expected during recovery
fi

# Rate-limited rsync: 50 MB/s cap, lowest I/O priority
# --exclude active pipeline DBs (they're being written to)
rsync -a \
    --bwlimit=50 \
    --nice 19 \
    --partial \
    --timeout=300 \
    --exclude="*.db" \
    --exclude="*.db-wal" \
    --exclude="*.db-shm" \
    --exclude=".DS_Store" \
    --exclude="._*" \
    --stats \
    "$SOURCE/" "$DEST/" >> "$LOG" 2>&1

echo "=== backup complete $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
