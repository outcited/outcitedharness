#!/bin/bash
# Generic facet-wave orchestrator — runs ON the M5 (user session).
#
# One instance per worker box. Env:
#   BOX        user@host | local            (where lanes execute)
#   MODE       front | tail | middle        (queue slice)
#   FACETS     e.g. thermal,mode | aec,errata,pillars,paxes,conn,ti
#   BOX_ROOT   remote corpus root           (empty when BOX=local)
#   CANON_ROOT canonical vault root for provenance rewrite
#   WAVE       docs per wave (default 200)
#   LANES_PY   python with pymupdf+pydantic on the worker
#
# Ledger: per-facet done-<facet>.txt (shared STATE on this machine); a doc is
# waved when any of the instance's facets is undone for it. Per-facet ok flags
# from the lane runner decide what gets banked. Failures park after 2.
set -u
cd /Users/samkim/Harnessv1
BOX="${BOX:-local}"
MODE="${MODE:-front}"
FACETS="${FACETS:-thermal,mode}"
BOX_ROOT="${BOX_ROOT:-}"
CANON_ROOT="${CANON_ROOT:-/Volumes/M5_4TB/vault}"
WAVE="${WAVE:-200}"
LANES_PY="${LANES_PY:-$HOME/harness-venv/bin/python}"
STATE=results/mbp-worker-20261014
WAVE_FILE="/tmp/wave-$MODE.json"
MISSING_FILE="$STATE/missing-$MODE.json"
mkdir -p "$STATE" "$STATE/inbox-$MODE"
touch "$STATE/parked.txt"

if [ "$BOX" != "local" ]; then
  scp -q scripts/mbp_run_lanes.sh "$BOX:mbp_run_lanes.sh"
fi

while true; do
  SELECTED=$(python3 scripts/wave_select.py "$STATE" "$WAVE" --mode "$MODE" \
    --facets "$FACETS" --out "$WAVE_FILE")
  TODO=${SELECTED%% *}
  if [ "$TODO" = "0" ]; then echo "$(date -u +%FT%TZ) [$MODE] ALL FACETS COMPLETE"; break; fi

  echo "$(date -u +%FT%TZ) [$MODE] wave: $SELECTED (facets: $FACETS)"
  # existence-filter the wave into a lane queue; missing -> missing list
  stage_queue() {
    python3 - "$WAVE_FILE" "$MISSING_FILE" "$1" <<'PYEOF'
import json, sys, os
wave_file, missing_file, root = sys.argv[1:4]
paths = json.load(open(wave_file))
docs, missing = [], []
for p in paths:
    if "/vault" not in p:
        continue
    mp = root + p.split("/vault", 1)[1] if root != "/Volumes/M5_4TB/vault" else p
    if os.path.exists(mp):
        docs.append({"source_path": mp, "part_number": p.rsplit("/", 1)[-1][:-4][:96]})
    else:
        missing.append(p)
json.dump({"documents": docs}, open("/tmp/mbp-queue.json", "w"))
json.dump(missing, open(missing_file, "w"))
print(len(docs))
PYEOF
  }

  if [ "$BOX" = "local" ]; then
    DELIVERED=$(stage_queue "/Volumes/M5_4TB/vault")
  else
    scp -q "$WAVE_FILE" "$BOX:/tmp/wave-current.json"
    DELIVERED=$(ssh -o BatchMode=yes "$BOX" "python3 - $(printf '%q' "$BOX_ROOT") <<'PYEOF'
import json, os, sys
root = sys.argv[1]
paths = json.load(open('/tmp/wave-current.json'))
docs, missing = [], []
for p in paths:
    if '/vault' not in p:
        continue
    mp = root + p.split('/vault', 1)[1]
    if os.path.exists(mp):
        docs.append({'source_path': mp, 'part_number': p.rsplit('/',1)[-1][:-4][:96]})
    else:
        missing.append(p)
json.dump({'documents': docs}, open('/tmp/mbp-queue.json', 'w'))
json.dump(missing, open('/tmp/mbp-missing.json', 'w'))
print(len(docs))
PYEOF")
    scp -q "$BOX:/tmp/mbp-missing.json" "$MISSING_FILE" 2>/dev/null
  fi

  python3 - "$MISSING_FILE" <<'PYEOF'
import json, sys
try:
    missing = json.load(open(sys.argv[1]))
except Exception:
    missing = []
if missing:
    with open("results/mbp-worker-20261014/missing-docs.log", "a") as fh:
        for p in missing:
            fh.write(p + "\n")
print(f"missing-files: {len(missing)}")
PYEOF

  if [ "${DELIVERED:-0}" = "0" ]; then
    echo "$(date -u +%FT%TZ) [$MODE] nothing deliverable yet, sleeping 120"
    sleep 120
    continue
  fi

  echo "$(date -u +%FT%TZ) [$MODE] lanes: $DELIVERED deliverable on $BOX"
  INBOX="$STATE/inbox-$MODE"
  rm -rf "$INBOX"; mkdir -p "$INBOX"
  if [ "$BOX" = "local" ]; then
    CORPUS_ROOT=/Volumes/M5_4TB/vault LANES_PY="$LANES_PY" bash scripts/mbp_run_lanes.sh 2>&1 | tail -3
    OK_FACETS=$(ls lane-out 2>/dev/null | grep '^ok-' | sed 's/^ok-//' | paste -sd, -)
    mv lane-out/*.jsonl "$INBOX/" 2>/dev/null
    rm -f lane-out/ok-* 2>/dev/null
  else
    ssh -o BatchMode=yes "$BOX" "CORPUS_ROOT=$BOX_ROOT LANES_PY=$LANES_PY bash mbp_run_lanes.sh" 2>&1 | tail -3
    OK_FACETS=$(ssh -o BatchMode=yes "$BOX" "ls lane-out 2>/dev/null | grep '^ok-' | sed 's/^ok-//' | paste -sd, -")
    for f in $(ssh -o BatchMode=yes "$BOX" "ls lane-out/*.jsonl 2>/dev/null"); do
      rsync -a -e ssh "$BOX:lane-out/$(basename $f)" "$INBOX/" 2>/dev/null
    done
  fi

  python3 scripts/wave_merge.py "$STATE" "$INBOX" \
    --ok-facets "${OK_FACETS:-}" --wave "$WAVE_FILE" --missing "$MISSING_FILE" \
    --box-root "$BOX_ROOT" --canon-root "$CANON_ROOT"

  if [ "$BOX" != "local" ]; then
    ssh -o BatchMode=yes "$BOX" 'rm -f lane-out/* /tmp/mbp-queue*.json /tmp/mbp-missing.json' 2>/dev/null
  else
    rm -f /tmp/mbp-queue*.json
  fi
  echo "$(date -u +%FT%TZ) [$MODE] wave: complete"
done
