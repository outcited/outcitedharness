# DIRECTOR-01A — Review audit, corrective evidence, sentinel diagnosis

**base:** aadad37e (DIRECTOR-01) · **patch:** this commit · read-only throughout.

## 1. The 39-proposal audit (from the aadad37e backfill run)

### Escalations (8, all to sam)

| # | event | verdict | defect |
|---|---|---|---|
| 1 | `asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']` (Oct 3) | classification CORRECT (P0 freeze rec) | backfill paging on a week-old, since-recovered incident |
| 2–3 | `asus4: ['canon…'] -> ['SSH_FAIL']` / inverse | **FALSE POSITIVE** — SSH_FAIL is probe blindness, canon state UNKNOWN, not a confirmed outage | substring match on "canon" (#5) |
| 4–5 | `dgx1_canon_replica: None/UNREACHABLE -> [...]` | **mis-scoped** — replica redundancy, not the verdict path; should be P1/agent, never P0/sam | no primary/replica split |
| 6–8 | `e10b: ['canon-replica'] -> ...` | **duplicates of 4–5** — e10b IS dgx1 | no alias reconciliation (#6) |

### Messages (31, all to m5-opencode)

- Owner routing: correct in all 31 (evidence links present in all 39 — good).
- ~all were **historical backfill** (Oct 2–9) presented as fresh incidents (#7).
- 4 same-second filename collisions wrote duplicate draft files.
- Canon/replica events were **double-messaged** (specific rule + generic infra rule both fired).
- Verdict: **35 of 39 were unnecessary**; the only genuinely actionable fresh
  signal was the stale sentinel — which had NO rule and produced nothing.

### Missed consequential events

- NAS backup failures (`results/backup-*.log` — not wired to any feed).
- Sentinel watchers failing exit-1 for 7 days (marker only, no rule).
- `._` junk flood in the substrate queue (queue health not wired — DIRECTOR-02).

## 2. Sentinel diagnosis (#8) — root cause, not masked

- **nightly** (`~/Logs/sentinel-nightly.log`): `PermissionError` writing
  `last-known-good.json` — the sentinel dir
  (`/Volumes/M5_4TB/extract-results/sentinel/`) is owned `_lpoperator:1026`
  mode 775; the launchd job (user `samkim`) cannot write. Last successful
  write: Oct 9 03:15. ALERTS.md appends are blocked the same way.
- **hourly** (`~/Logs/sentinel.log`): `sqlite3.OperationalError: unable to
  open database file` — sentinel opens the retired `results/pipeline.db`;
  the live queue moved to `pipeline-live.db` with the `PIPELINE_DB` env
  override that `extract_dispatch.py` sets **in-process only** — the
  sentinel plists never got it.
- `launchctl list`: both jobs exit **1** every run.
- Production-side fix required (NOT performed — read-only mandate): repair
  the sentinel dir ownership/permissions; add `PIPELINE_DB` to both sentinel
  plists. The director's new `watcher-health-stale` rule now pages when the
  watcher itself goes dark — nobody was watching the watchers.

## 3. Corrective evidence (fresh replay, real feeds, --no-llm)

| run | events | new | assessments | historical | proposals |
|---|---|---|---|---|---|
| 1 (backfill) | 549 | 549 | 72 | **49 suppressed** | **1** (watcher-health investigation — the genuine fresh incident) |
| 2 (page 2) | 500 | 500 | 42 | 0 | 0 |
| 3 (page 3) | 490 | 490 | 78 | 0 | 0 |

- Factory pagination drained **1,000 + 490 = 1,490 = MAX(rowid)** — zero
  events lost across pages (the aadad37e bug would have skipped 990).
- Steady state: replay idempotent, `chain_ok: true`, 31 MB RSS, ≤ 0.05 s/run.
- e10b/dgx1 replica incidents now collapse to one canonical-identity trigger
  key within the 6 h window.

## 4. Fix-to-item map

| review item | fix | test |
|---|---|---|
| 1 head-cache rollback | `_head=None` on rollback; standalone journaling hardened | `test_rollback_head_cache` |
| 2 transactional outbox | `written` column + `flush_pending_writes` + atomic idempotent writer | `test_outbox_crash_heal`, `test_outbox_atomic_write` |
| 3 pagination cursor | cursor = last rowid returned | `test_factory_pagination_no_loss` (+ live 1,490/1,490) |
| 4 fleet cursor/multiline | exact byte accounting; every bullet an event; continuation bytes never discarded | `test_fleet_multiline_and_exact_cursor` |
| 5 structured matching | `parse_fleet_delta` → {box, from, to, services, change_kind}; rules match service tokens + direction + change kind; SSH_FAIL = probe_state ≠ outage | `test_canon_structured_matrix`, `test_parse_fleet_delta_shapes` |
| 6 alias reconciliation | `machine-aliases.yaml` (AGENT_NETWORK-derived) + canonical trigger keys | `test_alias_normalization` |
| 7 backfill suppression | source-ts age check; historical assessments journaled, proposals suppressed | `test_backfill_suppression` |
| 8 sentinel diagnosis | root-caused above; `watcher-health-stale` rule; no LKG masking | `test_watcher_stale_classifies` |
| 9 tests | 11 new targeted tests; all 11 original acceptance tests still green (22 total) | — |
