#!/bin/bash
# catalog-release — hourly: fleet facets -> catalog-live.db (Sam directive
# 2026-10-22: "why tomorrow? why not push now — and then tomorrow again?")
# The catalog index refreshes itself. Stale index = the sentinel's problem.
set -u
PY=/Users/samkim/Harnessv1/.venv/bin/python
LOG="$HOME/Logs/catalog-release.log"
exec >> "$LOG" 2>&1
echo "=== $(date '+%F %T') catalog-release"
cd /Users/samkim/Harnessv1
for h in "samsonkim@100.89.168.75 seven" "samkim@100.107.135.19 kloey" "samkim@100.91.13.94 winter" "samsonkim@100.69.201.109 happy"; do
  host=$(echo "$h" | cut -d' ' -f1); name=$(echo "$h" | cut -d' ' -f2)
  rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "${host}:lane-out/" "$HOME/staging/fleet-lanes/${name}/" || echo "pull failed: $name"
done
$PY scripts/facet_catalog_load.py --lanes-dir "$HOME/staging/fleet-lanes/seven" --lanes-dir "$HOME/staging/fleet-lanes/kloey" --lanes-dir "$HOME/staging/fleet-lanes/winter" --lanes-dir "$HOME/staging/fleet-lanes/happy"
sqlite3 results/catalog-live.db "SELECT 'claims_total', count(*) FROM claims_canonical"
