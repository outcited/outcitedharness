# DIRECTOR-07 PROGRESS — 2026-10-19 late session (successor to HANDOFF-DIRECTOR-07.md)

Read HANDOFF-DIRECTOR-07.md first; this file only records deltas.

## Done this session (all evidence in staged receipts + git)

**Branch:** `fix/sentinel-live-reconcile-20261019` @ `13aa618a` (pushed, base `2ef8eaf3`)
1. **§1.A DONE** — live sentinel.py committed (`0bff6428`, `c7ca5d87`) with 9 behavioral
   hermetic tests (incl. mode-000 fail-closed) + clean-clone proof + static red-before
   (old tree has no env seam; old code NOT executed on host — volume is mounted/reachable).
2. **§1.B DONE (code side)** — `13aa618a`: hash-chained `kind='investigation'` receipts via
   `DirectorState.journal_investigation`, acknowledged dedupe (persist-then-ack, terminal-only),
   typed source identity (assessment payload sha256), starvation fix. 17/17 tests on real
   DirectorState; director-suite failure set IDENTICAL to base (8 pre-existing, not mine).
3. **§C DONE — GCI rotation EXECUTED end-to-end** (receipt: issue #12 comment 6284527447):
   root script token-free (3-source fallback, fails closed), token file 600 svc-user-owned,
   user gci.conf 600 + rotated, asus4 `/etc/harness-gci.env` swapped + `harness-gci.service`
   restarted. Evidence: NEW→200, OLD→401, all services active. GitHub PAT rotated too
   (classic, repo scope, keychain — receipts + PR #14 posted: 6279985713/6279985904).

## 2026-10-20 LATE SESSION — #7 PRODUCT BOARD + §D CLOSED (issue #7 receipts 6296655739/6299844398/6299909030/6300311995)

- **PR #14 + #16 MERGED** → `director/06-fleet-intelligence` @ `376c5906` (Sam authorized).
- **Product graph LIVE on seven**: deployed merged code to user observer (backup `app.bak-20261020`),
  `workspace/product-graph.json` carries 2 real conflicts (path-assumptions @ **owner-review** on
  Sam's ruling; endpoint-not-implemented @ proposed). Scanner: `scripts/dw_contract_scan.py`.
- **Contract ruling dispatched**: cr-core #103 (implement `/api/designwins/v1/engineering/decisions`
  + resolver + FE-reachable proof) · FE #72 (`DECISION_API_URL` base carries `/api/designwins`).
- **§D CLOSED**: root service + health timer disabled by Sam; single authority = user observer.
  Rollback data on disk (root workspace, mirrors, app backup).
- **Operating directive recorded**: "Seven has authority over the system" (Sam, 2026-10-20).
- Kloey added to fleet registry (extractor + normalization, active).

## OPEN (next agent)

1. Factory artifact discovery on mac-mini: locate intake-telemetry.json / receiver SHA
   manifests / corpus counts (collector + work_items Postgres confirmed LIVE via
   ~/mfg-collector.log; ~/mfg-factory scripts 10d stale). Route: seven→mac-mini works
   (`samsonkim@100.80.101.116`, dedicated director-observer@seven key, one line in
   authorized_keys to revoke). M5 also has key access.
2. Watch cr-core #103: when the endpoint lands on outcited.com main, re-run scan → conflict 2
   resolves; board auto-updates on next tick.
3. §E model-policy scoping (ASUS2-only) — fold into next maintenance window, one sudo (Sam's).
4. 8 pre-existing director test failures at base (01a, 01b, 02×3, 03, consumer×2) — other lanes.
5. Phase advances beyond owner-review need trusted-transport ACKs (issue #2 work).

## Known state / gotchas

- GCI auth = single static token in asus4 `/etc/harness-gci.env` (no registry/revoke API).
  Rotation = env swap + restart. Backup w/ dead token: `/etc/harness-gci.env.bak-rot20261020`.
- Root service user = `harness-director` (NOT samsonkim): scripts must stay world-executable,
  token readable only via the 600 svc-user-owned file. samsonkim lacks group membership +
  no setfacl on seven → file handoff to service user needs one sudo chown (Sam's password).
- Local `director/06-fleet-intelligence` ref stale; origin tip now `376c5906` (post-merge). Untouched.
- `/private/tmp/verify-06` worktree holds unused branch `fix/sentinel-db-path` @ `2ef8eaf3`. Untouched.
- Main worktree (m5/wave-orchestration) still has the unstaged sentinel.py edit — safe to drop
  (fix merged via PR #14).
- User observer deps: `~/staging/feeds/` (superset of old root feeds), workspace
  `~/seven-director/workspace/`, code `~/seven-director/app/` @ 376c5906 lineage.
