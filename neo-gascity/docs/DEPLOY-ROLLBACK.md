# Deployment & Rollback Plan (candidate — requires Sam's approval to execute)

## Deploy

1. Sam ratifies the finding contract + approval-artifact convention (one GitHub
   issue on outcited/outcitedharness using dw.finding.v1, one approval
   artifact in the approvals directory).
2. Fix `gh auth login` on samsons-mac-mini (PR/CI receipts).
3. Land the bridge in `outcited/outcitedharness` (unmerged PR, branch
   `neo/gascity-bridge-01`): `bridge/`, `schemas/`, `tests/`, `demo/`, docs.
   CI = `python3 -m unittest discover -s tests` (20 tests, offline).
4. Tunnel unit (launchd on M5): `ssh -N -L 8373:127.0.0.1:8372
   samsonkim@100.80.101.116` with KeepAlive; NEO snapshots read through it.
   API stays localhost-only on the mini — no new network exposure.
5. Cron/launchd on M5: `neo_status.snapshot_cli` every 10 min → snapshots to
   the shared board + alerts mail to Sam's mailbox (existing agent-inbox).
6. First production ingest: ONE Sam-approved finding → designwins-orchestrator,
   watched live; then Mayor-side `gc sling` remains manual.

## Rollback

- Bridge is additive: no Gas City config changed, no agents added, no packs
  imported. Rollback = stop the launchd jobs, close the tunnel, delete the
  checkout. Receipts and demo bead `ds-gb1` are inert rows in staging (close
  manually via dashboard if desired).
- Emergency: `gt`-style e-stop equivalents — supervisor `PATCH
  /v0/city/{city}` suspend (documented) or `gc stop` on the mini. The bridge
  itself has no dispatch capability to recall: it can only create open beads.

## Invariants to preserve

- Only Sam's artifacts authorize ingests (issuer=sam, action=gascity-ingest).
- Bead creation queues work; dispatch (`gc sling`) is separate and manual.
- Never write to designwins-orchestrator without a per-finding artifact.
- GitHub comments NEVER upgrade authorization (workloop law).
