# DIRECTOR-02 — shadow-run report (real DesignWins feeds, read-only)

**date:** 2026-10-16 · **mode:** observe-only · no messages sent, nothing
dispatched, no production touched. Workspace: `/tmp/d02-shadow`.

## Run configuration

Source feeds: `docs/feeds.real.yaml` (fleet CHANGES.md, sentinel, ra03
factory_events, facet ledgers, pipeline budget) + derived feeds (fleet
registry from live Tailscale + fleet_state snapshots + AGENT_NETWORK.md;
factory continuity from ra03 factory.sqlite3 + pipeline-live.db + facet
ledgers + pdf-intake log) + project config `projects/designwins/`
(policy.yaml authority + model governance, fleet.yaml roles/thresholds).

## Results

| run | events new | assessments | historical (suppressed) | proposals | markers | wall | RSS |
|---|---|---|---|---|---|---|---|
| 1 (backfill) | 555 | 78 | 49 | **7** | 1 | ~18 s | 38 MB |
| 2 (replay) | 500 (live factory churn) | 42 (P3, action=none) | 0 | **0** | 1 | ~5 s | 36 MB |

Journal chain verifies after both runs. Replay is idempotent: no duplicate
proposals, no re-paged incidents.

## The 7 fresh findings (all evidence-backed, correctly owned)

1. **AppleDouble contamination — 176,709 dead `._` jobs** in the substrate
   queue (evidence: pipeline-live.db jobs table; threshold 1,000). The
   factory-side fix is in progress; director proposes investigation, never
   deletes data. — *the §4 detection requirement, live*
2. **asus3/nemotron endpoint UNREACHABLE while asus3 is online** — service
   failure distinguished from machine failure (§3 law holds).
3. **samson's MacBook Pro connectivity unknown→offline** (tailscale first
   sight) — connectivity event only; services never inferred.
4. **localhost connectivity unknown→offline** — registry normalization gap:
   the local machine appears under an unresolved hostname (see gaps).
5. **sentinel last-known-good 200 h stale** — watchers failing
   (PermissionError root cause documented in 01A), watcher-health rule.
6. **Extraction queues empty — workers idle** (ready backlog 0).
7. **Predicted starvation 0.0h vs 24h target** — coverage breach. NOTE the
   UNKNOWN discipline held: consumption rate = UNKNOWN (only 1 ledger
   observation; the missing measurement is named); starvation is 0.0
   because backlog is literally 0 (queue_empty + starvation = one cause,
   two symptoms — correlation candidate).

## Factory continuity measurements (evidence-based)

- ready backlog: **0 docs** (ra03 chunks queued + pipeline pending)
- consumption rate: **UNKNOWN** (need ≥2 ledger observations — named)
- acquisition: **+666 docs/day** over the observed intake-log span
  (2026-10-15T09Z..2026-10-16T03Z checkpoints, 66,336→66,994 files)
- junk dead jobs: **176,709** · stalled leases: **UNKNOWN** (lease column
  not present in this factory.sqlite3 schema variant — named)
- starvation: 0.0 h (backlog-zero case)

## Historical discipline

49 historical assessments (Oct 2–12 CHANGES sections + the Oct 6 GOLD DRIFT
alert) were journaled with `[historical…]` rationale and produced ZERO
proposals. Replayed events dedupe by fingerprint.

## Model governance

This run used `--no-llm` (deterministic-only). Policy enforcement is
proven by tests: metered providers hard-refused without spend
authorization, allowlist-only endpoints, usage ledger records refusals.
One earlier probe run with LLM enabled recorded usage to `model_usage`.
