#!/usr/bin/env bash
# M5 critical-artifact backup -> Synology NAS via rsync-over-SSH (stable; SMB drops).
# Auth: SSH password via ~/.nas_askpass (chmod 700). Key auth blocked (no NAS home dir).
set -u
NAS_USER=charley
NAS_HOST=192.168.4.54
NAS_PATH=/volume1/Electronicdataassets/backups/m5
ASKPASS=$HOME/.nas_askpass
STAGE=/Volumes/M5_4TB/nas-staging
SRC=/Volumes/M5_4TB/extract-results
REPO=/Users/samkim/Harnessv1

export SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force DISPLAY=:0
RSH="ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
DEST="${NAS_USER}@${NAS_HOST}:${NAS_PATH}"

rc=$($RSH "$NAS_USER@$NAS_HOST" "echo reachable" 2>/dev/null | grep -c reachable)
if [ "$rc" != "1" ]; then echo "NAS unreachable — aborting"; exit 1; fi

mkdir -p "$STAGE"
for db in catalog pipeline; do
  [ -f "$SRC/$db.db" ] && sqlite3 "$SRC/$db.db" ".backup '$STAGE/$db.db'" && echo "snapshot $db.db"
done

rsync -a --partial -e "$RSH" "$STAGE/catalog.db" "$STAGE/pipeline.db" "$DEST/"
rsync -a --partial --delete -e "$RSH" "$SRC/burn-power-v1/" "$DEST/burn-power-v1/"
rsync -a --partial --delete -e "$RSH" "$SRC/burn-vault-v1/" "$DEST/burn-vault-v1/"
rsync -a --partial --delete -e "$RSH" /Volumes/M5_4TB/agent-inbox/ "$DEST/agent-inbox/"
rsync -a --partial --delete -e "$RSH" "$REPO/results/lora-v2-pairs/" "$DEST/lora-v2-pairs/"
rsync -a --partial -e "$RSH" "$REPO/"{RUNBOOK.md,CONTEXT_HANDOFF.md,ARCHITECTURE.md,LEARNING_FACTORY.md,MODEL_SETTINGS.md,SYSTEM_SUMMARY.md,TRUTH_PLAN.md} "$DEST/control/" 2>/dev/null
date -u +%FT%TZ > /tmp/nas_last_backup
$RSH "$NAS_USER@$NAS_HOST" "cat > $NAS_PATH/LAST_BACKUP" < /tmp/nas_last_backup
echo "DONE $(date -u +%FT%TZ)"
