# How NEO consumes Gas City status (GitHub-only, no M5 access)

## Constraint

NEO (ChatGPT side) has a GitHub connection. It must NOT be assumed to have
direct access to M5, the Mac mini, or any fleet host.

## Mechanism

`ops/status_publish.py` (M5, every 10 min once activated) pushes to the
**`neo/status` branch of `outcited/outcitedharness`** (never `main`):

```
status/
  LATEST.json                        <- pointer: captured_at, per-city alert rules, content digest
  ALERTS.md                          <- human-readable continuity alerts, both cities
  designwins-orchestrator.neo-snapshot.json/.md
  designwins-staging.neo-snapshot.json/.md
  history/<YYYYMMDDTHHMMSSZ>.json    <- appended on CHANGE or hourly heartbeat
receipts/                            <- dw.gascity-acceptance.v1 ingest receipts (copied by handoff runs)
```

NEO reads: file-contents API or a plain fetch of `neo/status`
(`https://github.com/outcited/outcitedharness/tree/neo/status/status`).
Audit trail: commit history on the branch = publication log; each history
entry carries the alert count and change/heartbeat disposition.

## Integrity properties

- Publisher is read-only toward Gas City (GET endpoints only) and write-only
  to `neo/status`. It cannot touch `main`, cannot open PRs, cannot dispatch.
- Commit identity `neo-publisher <neo@designwins>`; commits are pushed with
  the existing M5 deploy key.
- Change-digest policy keeps the branch small: snapshots always fresh,
  history grows only on change or 55-min heartbeat.
- If NEO sees a stale `LATEST.json` (>15 min), that ITSELF is an alert
  (publisher or tunnel down) — NEO can check tunnel liveness indirectly via
  supervisor uptime deltas between consecutive snapshots.

## Reverse direction (NEO → loop)

NEO publishes engineering findings as issues on allowlisted repos using the
`dw.finding.v1` body; ingest refuses anything else (gh_workloop law). NEO
never submits beads itself — authorization stays with Sam's artifacts.

## Seeding evidence

One manual seed run (2026-10-27) created the branch and pushed the first
snapshots; see PR description and `status/history/` first entry.
