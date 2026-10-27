#!/bin/bash
# Activate the NEO<->Gas City observation services on M5.  Run MANUALLY, only
# after Sam approves activation (DEPLOY-ROLLBACK.md gate 4).  Idempotent.
set -euo pipefail
PLISTS=(com.neogascity.tunnel com.neogascity.publisher)
for p in "${PLISTS[@]}"; do
  install -m 0644 "$p.plist" ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/$p.plist 2>/dev/null || true
  launchctl load   ~/Library/LaunchAgents/$p.plist
  echo "loaded: $p"
done
launchctl list | grep neogascity
echo "NOTE: publisher waits for tunnel health; check http://127.0.0.1:8373/health"
