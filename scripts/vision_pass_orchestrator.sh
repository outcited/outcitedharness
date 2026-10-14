#!/usr/bin/env bash
# Orchestrate the post-extraction vision pass.
#
# 1. Wait for the CPU extraction lanes (pillars/aec/errata) to finish.
# 2. Prepare the full L0 vision backlog (render plot regions -> request/meta).
# 3. Run the vision lane through local TP4 (deepseek-v4.1-flash), 4 workers.
#
# Sam's law: run vision only AFTER the harness extraction steps. This script
# enforces that ordering — it blocks until no extract_* lane is running.
set -u
cd /Users/samkim/Harnessv1
PY=.venv/bin/python
OUT=results/vision-full-20261013
LOG=results/vision-full-20261013/orchestrator.log
mkdir -p "$OUT"
exec >>"$LOG" 2>&1

echo "=== orchestrator start $(date -u +%FT%TZ) ==="

echo "[1] waiting for CPU lanes to drain..."
while pgrep -f 'extract_(pillar_facts|aec_grades|errata_deltas)' >/dev/null; do
  sleep 30
done
echo "[1] CPU lanes done $(date -u +%FT%TZ)"

echo "[2] preparing vision backlog (renders + meta)..."
rm -f /tmp/vision-all-l0.jsonl
for f in \
  results/l0-reclass-20261012/l0-records.jsonl \
  results/l0-census-chunk4-20261007/l0-records.jsonl \
  results/l0-census-20261006/l0-records.jsonl \
  results/l0-census-unassigned-20261007/l0-records.jsonl \
  results/l0-census-incremental-20261009/l0-records.jsonl \
  results/l0-census-incremental2-20261009/l0-records.jsonl ; do
  [ -f "$f" ] || continue
  echo "[2] prepare from $f"
  $PY scripts/or_batch_vision.py prepare \
    --l0-records "$f" --doc-class datasheet \
    --out "$OUT/done-work.jsonl" \
    --requests-dir "$OUT/requests" --chunk 100
done
echo "[2] prepared: $(wc -l < "$OUT/requests/figure-meta.jsonl" 2>/dev/null) regions"

echo "[3] running TP4 vision lane (4 workers)..."
$PY scripts/cloud_vision_direct.py \
  --renders "$OUT/requests/renders" \
  --meta "$OUT/requests/figure-meta.jsonl" \
  --out "$OUT/vision-rows.jsonl" \
  --provider tp4 --workers 4

echo "=== orchestrator done $(date -u +%FT%TZ); rows=$(wc -l < "$OUT/vision-rows.jsonl" 2>/dev/null) ==="