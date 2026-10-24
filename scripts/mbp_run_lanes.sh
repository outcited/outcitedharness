#!/bin/bash
# Runs ON a worker box (MBP / asus2 / tomorrow's). Executes the CPU facet
# stages for one wave against the LOCAL corpus copy. Per-stage ok-<facet>
# flags record which facets completed — the M5 merge marks done per facet,
# so a slow/failing facet never blocks the others from banking.
#
# Facets: thermal, mode, aec, errata, pillars, paxes, conn, ti
# (all pure pdf-parse — model lanes like pin_tables/typical_curves are NOT
# here; they belong to TP4/OpenRouter batch).
set -u
cd ~/Harnessv1
Q=/tmp/mbp-queue.json
OUT=lane-out
CORPUS_ROOT="${CORPUS_ROOT:-/Users/samsonkim/corpus-vault}"
PY="${LANES_PY:-$HOME/harness-venv/bin/python}"
mkdir -p "$OUT"
rm -f "$OUT"/ok-* "$OUT"/*.jsonl

run_stage() {  # name script extra-args...
  local name=$1; shift
  perl -e 'alarm 3300; exec @ARGV' "$PY" "$@" && touch "$OUT/ok-$name" \
    || echo "stage $name FAILED"
}

run_stage thermal scripts/extract_thermal_metrics.py \
  --queue "$Q" --pdf-root "$CORPUS_ROOT" --out "$OUT/thermal-w.jsonl" \
  --workers 8 --doc-timeout 45

# serial lanes get sharded across 6 queue slices
python3 - <<'PY'
import json
docs = json.load(open("/tmp/mbp-queue.json"))["documents"]
for i in range(6):
    json.dump({"documents": docs[i::6]}, open(f"/tmp/mbp-queue-m{i}.json", "w"))
PY
shard_stage() {  # name script extra-args...
  local name=$1; shift
  local pids=() i
  for i in 0 1 2 3 4 5; do
    perl -e 'alarm 3300; exec @ARGV' "$PY" "$@" \
      --queue "/tmp/mbp-queue-m$i.json" --out "$OUT/$name-w-m$i.jsonl" &
    pids+=($!)
  done
  local rc=0 p
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  [ "$rc" = "0" ] && touch "$OUT/ok-$name" || echo "stage $name FAILED"
}

shard_stage mode scripts/extract_mode_currents.py

run_stage aec scripts/extract_aec_grades.py \
  --queue "$Q" --out "$OUT/aec-w.jsonl" --workers 8

run_stage errata scripts/extract_errata_deltas.py \
  --queue "$Q" --out "$OUT/errata-w.jsonl" --workers 8 --doc-timeout 45

run_stage pillars scripts/extract_pillar_facts.py \
  --queue "$Q" --out "$OUT/pillars-w.jsonl" --workers 8

run_stage paxes scripts/extract_power_v2_axes.py \
  --queue "$Q" --pdf-root "$CORPUS_ROOT" --out "$OUT/paxes-w.jsonl" --workers 8

run_stage conn scripts/extract_connector_parametrics.py \
  --queue "$Q" --out "$OUT/conn-w.jsonl" --workers 8

shard_stage ti scripts/extract_ti_parametrics.py --pdf-root "$CORPUS_ROOT"

echo "OK FACETS: $(ls "$OUT" | grep '^ok-' | sed 's/^ok-//' | tr '\n' ',')"
