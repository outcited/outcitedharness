# DIRECTOR-01 — Journal schema, storage plan, replay & failure-injection results

## Journal schema (director.db, workspace-local sqlite)

```
journal(id, ts, kind, payload, prev_hash, entry_hash)   -- hash-chained, append-only
  kind ∈ {marker, assessment, llm, run}
  marker      {kind: feed_stale|feed_missing|feed_error|contradiction, feed, detail, observed_ts}
  assessment  {rule_id, severity, impacts[], action, authority, rationale,
               evidence[{feed, source_id, original_ts, observed_ts, locator}],
               event_kind, event_summary, trigger_key, model_assessment?}
  llm         {status: ok|unavailable, source|reason}
  run         {mode, map_version, events_seen, events_new, assessments,
               proposals_new, markers, elapsed_s}

cursors(feed PK, cursor, updated_at)        -- opaque per-adapter position
seen_events(fingerprint PK, first_seen)     -- event dedup (pruned at 200k)
proposals(trigger_key PK, ptype, filename, authority, created_at, body)
```

**Storage plan:** one sqlite file + `outbox/` + `escalations/` per workspace on
the controller's local NVMe. Full-state backup = copy the db (append-only ⇒
copy is always consistent enough; chain verify catches divergence). Size at
fleet volume: ~1 entry per event × ~500 events/run × 144 runs/day ≈ 70k rows/day
≈ ~30 MB/day uncompressed — trivial for 32 GB; prune `seen_events` keeps it flat.

**Effectively-once:** ingest batch = single `BEGIN IMMEDIATE` txn (journal +
proposals + seen + cursor). Crash mid-batch ⇒ full rollback ⇒ next run re-polls
identical events ⇒ dedup + proposal PKs make the outcome identical.

## Replay results (real feeds, read-only, --no-llm)

Feeds: `docs/feeds.real.yaml` (fleet CHANGES.md, sentinel, ra03 factory_events,
facet ledgers, pipeline-live budget_spend).

| run | events_seen | events_new | assessments | proposals_new | wall | RSS |
|---|---|---|---|---|---|---|
| 1st (backfill) | 531 | 531 | 61 | 39 (31 msg + 8 esc) | 0.05 s | 31 MB |
| 2nd (replay) | **0** | **0** | **0** | **0** | 0.005 s | 30 MB |

`verify` → `{"chain_ok": true, "broken_at": null}`.
Genuine finding on first contact: `feed_stale(sentinel)` — last-known-good
175.5 h old vs 36 h threshold (sentinel LKG has not refreshed in a week).

## Failure injection (tests, offline)

| injection | result |
|---|---|
| replay same feeds twice | zero duplicate assessments/proposals (`test_replay_twice_no_duplicates`) |
| "restart" mid-stream (new consumer, appended events) | only new events processed, position preserved (`test_restart_preserves_position`) |
| journal row tampered (`UPDATE journal SET payload='forged'`) | `verify_chain` → false, broken_at=id (`test_journal_tamper_detection`) |
| all LLM endpoints dead | ingestion + classification continue; `llm{unavailable}` journaled; chain intact (`test_llm_total_loss_ingestion_continues`) |
| stale source (lkg mtime −40 h) | explicit `feed_stale` marker, fail-closed (`test_freshness_fail_closed`) |
| canon endpoint change | P0 freeze **recommendation** to sam as escalation draft only (`test_canon_change_promotion_freeze_recommendation_only`) |
| NAS backup failure | impacts ⊆ {backups, served_export}; no production scopes (`test_nas_backup_failure_is_scoped`) |
| routine facet progress | handoff message to agent, **no** human escalation (`test_routine_task_proposed_handoff_no_escalation`) |
| Sam-reserved spend anomaly | escalation draft citing authority rule, no agent message, no action (`test_sam_reserved_generates_escalation_draft_only`) |
| model enrichment present | stored with `verified:false, advisory_only:true`; classification unchanged (`test_model_assessment_never_fact`) |

## Resource measurements (MS-A2 sizing: 32 GB Ryzen 9955HX)

- Cold run: 0.05 s wall, 0.03 s CPU, **31 MB RSS** (531 events incl. 500-row
  factory_events page).
- Steady-state delta run: ~5 ms wall, 30 MB RSS.
- At 10-min cadence: < 0.01% CPU duty cycle. Python 3.11 + stdlib + pyyaml.
- Verdict: the consumer is negligible on MS-A2; the box has headroom for the
  mirrors, journal retention, and the future advisory phase.
