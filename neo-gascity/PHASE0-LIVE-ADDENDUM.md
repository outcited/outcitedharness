# PHASE 0 LIVE ADDENDUM — mini inspected 2026-10-27

Blocker from PHASE0-REPORT.md §4.1 is CLEARED: Tailscale re-authed, mini reachable.

## Live Gas City installation (verified read-only)

| Fact | Value |
|---|---|
| Host | Samsons-Mac-mini.local, macOS 15.7.7, up 19d, Tailscale 100.80.101.116 |
| gc binary | /opt/homebrew/bin/gc, **v1.5.1**; bd 1.3.2, dolt present |
| Supervisor | `gc supervisor run` PID 40699, **127.0.0.1:8372**, uptime ~70h |
| Cities | `designwins-orchestrator` (prod, ~/designwins-orchestrator/city) + `designwins-staging` (test) |
| Packs | `bd`, `core` only — **no Gastown pack, NO LLM Mayor agent** |
| Coordination | deterministic `core.control-dispatcher` (`gc convoy control --serve`); supervisor reconciler |
| Agents (prod) | bd.dog pool (1 running: `bd__dog-do-wisp-lah661`), control-dispatchers (city+pilot-rig+harness), engineer pool 0/2, harness-engineer pool 0/2 — OpenCode provider on ASUS2 Qwen3.8-27B |
| Rigs | pilot-rig, harness (prefix `hw`) |
| Work queue | 28 open / 21 ready / 1 in_progress; includes `gh:outcitedharness`-labeled findings (hw-wk9 FAILCLOSED…, hw-b5l, hw-wfn, hw-37d) + city-scope mirrors (`do-*`, duplicate group) |
| Orders live | order-tracking-sweep, spawn-storm-detect, gate-sweep firing/completing; `mol-dog-stale-db` running |
| Health | supervisor ok; both cities `partial` w/ bead-cache + store-health timeout errors (pre-existing) |
| **gh CLI** | **broken — token invalid for beargallbladder** (blocks PR/CI receipts) |
| CSRF | verified live: POST w/o X-GC-Request → 403; w/ header + empty body → 422 nothing written |
| Idempotency | Idempotency-Key honored on POST /beads (replayed live: same bead returned) |

## Step 3 — orchestration ownership

**Seven's director: `harness-director.service` DISABLED + inactive since
2026-10-20 20:04 ("observation-only observer, live delivery disabled").**
No dispatch since. **Gas City on the mini is the sole active execution
authority.** Director assets (approvals dir, policy.yaml, gh_workloop code)
remain authoritative for AUTHORIZATION conventions; nothing to restart.

## Step 4 — integration executed (isolated)

- Bridge built at ~/designwins-neo-gascity (bridge/, schemas/, tests/, demo/, docs/).
- Offline tests: **20/20 pass** (incl. 6 negative controls, CSRF, idempotent replay).
- Live demo (staging city only, per brief): refused-unauthorized → **accepted
  bead `ds-gb1`** → resubmit **duplicate (same bead)** → NEO snapshots both cities.
- Receipts: demo/out/*.json (`dw.gascity-acceptance.v1`, read-back + event seq 37493).
- Docs: docs/ARCHITECTURE.md, docs/DEPLOY-ROLLBACK.md.

## What remains before NEO supervises Gas City directly

1. Sam: `gh auth login` on the mini (PR/CI receipt leg).
2. Sam: ratify dw.finding.v1 + approval-artifact convention (1 test issue + 1 artifact).
3. Land unmerged PR `neo/gascity-bridge-01` in outcited/outcitedharness.
4. M5 launchd: tunnel keepalive + 10-min snapshot job + alert mail.
5. Optional: investigate bead-cache/store-health partial errors (pre-existing).
6. Demo bead ds-gb1 left open in staging (inert; close anytime via dashboard).
