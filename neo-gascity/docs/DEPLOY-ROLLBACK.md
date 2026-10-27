# Deployment & Rollback Plan (release candidate — verified head on PR #52)

Status: CI green (bridge-tests + repo checks). launchd NOT active; PR NOT merged.
This document matches the implementation as of head `81688c9`+.

## Deploy — read-only observation activation (GO scope)

1. Sam merges PR #52 (branch `neo/gascity-bridge-01`).
2. M5: checkout the merged tree to `~/designwins-neo-gascity` (the publisher's
   dedicated checkout is created automatically at `~/designwins-neo-gascity/status-repo`,
   marked `.neo-status-checkout`; nothing else is touched).
3. Load the two PREPARED launchd agents (the only activation switch):
   ```
   cd ~/designwins-neo-gascity/ops && bash install-m5.sh
   launchctl list | grep neogascity        # both loaded
   curl -s http://127.0.0.1:8373/health    # tunnel alive
   ```
   - `com.neogascity.tunnel` — SSH keepalive M5:8373 → mini:8372 (localhost-only
     on the mini; no new network exposure).
   - `com.neogascity.publisher` — every 10 min: builds snapshots (both cities,
     read-only GETs), sanitizes via allowlist, commits FRESHNESS.json heartbeat
     every run, history entries only on stable state change, pushes to the
     `neo/status` branch only. Failures exit non-zero (visible in
     `/tmp/neogascity-publisher.err`) and never partially publish.
4. NEO reads GitHub only: `status/LATEST.json`, `status/FRESHNESS.json`
   (staleness > 15 min = publisher/tunnel alert), `status/ALERTS.md`,
   per-city snapshots + history on branch `neo/status` of
   `outcited/outcitedharness`. No M5 access required.

## Deploy — production ingestion (separate, later approval)

1. Operator (NOT the bridge service user) creates the production trust root on
   a host the service cannot write, e.g. on seven:
   `sudo install -d -m 0750 -o samsonkim /etc/gascity-approvals` with
   ancestors root-owned (already true under /etc) — root owner uid MUST differ
   from the bridge's runtime uid (enforced by `load_approval`).
2. Bridge service env: `GC_INGEST_TRUST_PROD=/etc/gascity-approvals`.
3. First ingest: one real finding (`dw.finding.v1` issue) + one Sam-authored
   artifact (`dw.approval.gascity-ingest.v1`, digest-bound, max_actions 1) →
   `bridge handoff` → receipt `BEAD_ACCEPTED` (event-proved) → operator runs
   `gc sling` manually on the mini → agent executes → PR + CI → NEO verifies
   `WORK_VERIFIED`.
4. gh auth on the mini is already fixed (2026-10-27).

## Rollback (read-only activation)

1. `launchctl unload ~/Library/LaunchAgents/com.neogascity.{tunnel,publisher}.plist`
2. The `neo/status` branch is additive history — delete the branch or ignore it;
   `main` was never touched by the publisher (push target is the pinned
   `neo/status` ref only).
3. Remove `~/designwins-neo-gascity` — nothing else was configured.
4. Gas City itself is untouched by any of the above (zero writes; the bridge
   has no dispatch code path — only `create_bead` behind Sam's artifacts).

## Invariants (enforced by tests, 73 offline + CI)

- Trust = verified ownership chain + placement + full JSON Schema + digest
  binding + atomic single-use ledger. Issuer strings/mode bits/GitHub content
  authorize nothing; the bridge's own uid cannot own a production root.
- Bead creation queues work; dispatch (`gc sling`) is separate and manual.
- Never write to `designwins-orchestrator` without a per-finding artifact.
- Publisher: sanitized allowlist output, marked dedicated checkout only,
  `neo/status` branch only, fails visibly.
- GitHub comments NEVER upgrade authorization (workloop law).
