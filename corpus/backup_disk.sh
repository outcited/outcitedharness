#!/usr/bin/env bash
# corpus_backup_disk.sh — plug in a disk, back up ALL raw corpora, verified.
#
# usage: corpus_backup_disk.sh /mountpoint/of/disk [cas-source]
#
# what lands on the disk:
#   cas/          every content blob (sha-named)
#   INDEX.jsonl   sha -> size, locations
#   REGISTRY.json what/where/roles
#   BACKUP.manifest  file count + bytes + sha256 of INDEX — the receipt
#
# law: the disk is a BACKUP, never the source of truth. Canonical is the NAS
# (/volume1/Electronicdataassets/corpus). Restores: rsync the cas/ back.
set -euo pipefail

DEST="${1:?usage: corpus_backup_disk.sh <disk-mountpoint> [cas-source]}"
SRC="${2:-/Users/samkim/mnt/canonical-rw/corpus}"
CORPUS_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -d "$DEST" ] || { echo "destination $DEST does not exist (mount the disk first)"; exit 1; }
[ -d "$SRC/cas" ] || { echo "no cas/ under $SRC — is the canonical store mounted?"; exit 1; }

echo "[backup] target: $DEST"
mkdir -p "$DEST/corpus"

echo "[backup] rsync cas/ (this is the long part)..."
rsync -a --info=progress2 "$SRC/cas/" "$DEST/corpus/cas/"

echo "[backup] index + registry..."
cp -f "$SRC/INDEX.jsonl" "$DEST/corpus/INDEX.jsonl" 2>/dev/null \
  || cp -f "$CORPUS_DIR/INDEX.jsonl" "$DEST/corpus/INDEX.jsonl"
cp -f "$CORPUS_DIR/REGISTRY.json" "$DEST/corpus/REGISTRY.json"

SRC_FILES=$(find "$SRC/cas" -type f | wc -l | tr -d ' ')
DST_FILES=$(find "$DEST/corpus/cas" -type f | wc -l | tr -d ' ')
SRC_BYTES=$(du -sk "$SRC/cas" | cut -f1)
DST_BYTES=$(du -sk "$DEST/corpus/cas" | cut -f1)
INDEX_SHA=$(sha256 -q "$DEST/corpus/INDEX.jsonl" 2>/dev/null || sha256sum "$DEST/corpus/INDEX.jsonl" | cut -d' ' -f1)

cat > "$DEST/corpus/BACKUP.manifest" <<EOF
backup_date: $(date -u +%Y-%m-%dT%H:%M:%SZ)
source: $SRC
files: $DST_FILES
source_files: $SRC_FILES
kb: $DST_BYTES
source_kb: $SRC_BYTES
index_sha256: $INDEX_SHA
verify: file count must equal source_files; restore with: rsync -a DEST/corpus/cas/ CANONICAL/cas/
EOF

echo "[backup] DONE — receipt at $DEST/corpus/BACKUP.manifest"
cat "$DEST/corpus/BACKUP.manifest"
[ "$SRC_FILES" = "$DST_FILES" ] || { echo "[backup] WARNING: file count mismatch"; exit 2; }
