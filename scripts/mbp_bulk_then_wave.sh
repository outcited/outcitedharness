#!/bin/bash
# Bulk-push the vault corpus ONCE to the old MBP's attached external
# (MACMOBILE, 500GB), then run the wave orchestrator against the MBP's
# local copy. No per-wave PDF shipping — lanes read local disk.
#
# 2026-10-15: replaces the rsync-per-wave loop that burned 56 waves with
# rows=0 (wrong --relative paths + launchd TCC "Operation not permitted").
set -u
MBP=samsonkim@100.109.86.30
SRC=/Volumes/M5_4TB/vault/
DST="$MBP:/Volumes/MACMOBILE/corpus/vault/"
STATE=/Users/samkim/Harnessv1/results/mbp-worker-20261014
LOG=$STATE/bulk-push.log

echo "$(date -u +%FT%TZ) bulk-push start" >> "$LOG"
ok=0
for i in 1 2 3 4 5; do
  if rsync -a --partial --stats "$SRC" "$DST" >> "$LOG" 2>&1; then
    ok=1; break
  fi
  echo "$(date -u +%FT%TZ) bulk-push attempt $i failed, retrying in 30s" >> "$LOG"
  sleep 30
done
if [ "$ok" != "1" ]; then
  echo "$(date -u +%FT%TZ) bulk-push FAILED after 5 attempts" >> "$LOG"
  exit 1
fi

LOCAL_N=$(find /Volumes/M5_4TB/vault -name '*.pdf' | wc -l | tr -d ' ')
REMOTE_N=$(ssh -o BatchMode=yes "$MBP" 'find /Volumes/MACMOBILE/corpus/vault -name "*.pdf" | wc -l' | tr -d ' ')
echo "$(date -u +%FT%TZ) bulk-push done local=$LOCAL_N remote=$REMOTE_N" >> "$LOG"
if [ "$LOCAL_N" != "$REMOTE_N" ]; then
  echo "$(date -u +%FT%TZ) COUNT MISMATCH — aborting before lanes" >> "$LOG"
  exit 1
fi

exec bash /Users/samkim/Harnessv1/scripts/mbp_wave_orchestrator.sh
