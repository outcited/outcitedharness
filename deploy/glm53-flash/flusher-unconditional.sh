#!/usr/bin/env bash
# Keep GB10 unified-memory headroom deterministic for the full model-load window.
set -euo pipefail

duration="${1:-5400}"

if ! sudo -n true 2>/dev/null; then
  echo "FATAL: passwordless sudo is required to drop page cache." >&2
  exit 1
fi

echo "flusher: dropping page cache every 60s for ${duration}s (pid $$)"
end=$((SECONDS + duration))
while ((SECONDS < end)); do
  sync
  if ! echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null; then
    echo "FATAL: page-cache drop failed; refusing an unguarded model load." >&2
    exit 1
  fi
  sleep 60
done
echo "flusher: window elapsed"
