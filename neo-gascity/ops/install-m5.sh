#!/bin/bash
# Activate the NEO<->Gas City observation services on M5.  Run MANUALLY, only
# after Sam approves activation (DEPLOY-ROLLBACK.md).  Idempotent.
#
# The shipped plists carry a __NEO_GC_ROOT__ placeholder; this script resolves
# the checkout root from its own location (the repo root, one level above
# neo-gascity/) and substitutes it into the INSTALLED copies, so the layout
# never goes stale (fixes the activation defect found 2026-10-27: publisher
# plist pointed at the pre-merge ops/ path and exited 2).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX_PLIST="$ROOT/neo-gascity/ops/com.neogascity.publisher.plist"
TUNNEL_PLIST="$ROOT/neo-gascity/ops/com.neogascity.tunnel.plist"

[ -f "$PREFIX_PLIST" ] || { echo "missing $PREFIX_PLIST — run from the merged checkout"; exit 1; }
grep -q "__NEO_GC_ROOT__" "$PREFIX_PLIST" || { echo "publisher plist lacks template placeholder"; exit 1; }
[ -x "$ROOT/neo-gascity/ops/status_publish.py" ] || { echo "status_publish.py not found under $ROOT"; exit 1; }

for p in com.neogascity.tunnel com.neogascity.publisher; do
  SRC="$ROOT/neo-gascity/ops/$p.plist"
  DST="$HOME/Library/LaunchAgents/$p.plist"
  sed "s|__NEO_GC_ROOT__|$ROOT|g" "$SRC" > "$DST"
  plutil -lint "$DST" >/dev/null
  launchctl unload "$DST" 2>/dev/null || true
  launchctl load "$DST"
  echo "loaded: $p -> $DST"
done
launchctl list | grep neogascity
echo "NOTE: publisher waits for tunnel health; check http://127.0.0.1:8373/health"
