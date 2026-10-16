# INCIDENT-CORRELATION — schema, grouping rules, replay evidence

## Schema (director.db, append-only events + upsert-free identity)

```
incidents(incident_id PK, project, correlation_id, identity_key,
          first_seen, last_seen, state open|resolved|superseded, severity,
          suspected_cause, verified_cause NULL, owner, related_rules,
          evidence[], proposed_action, resolution, reopen_count)
incident_events(incident_id, trigger_key, ts)
```

incident_id = hash(correlation_id, identity_key, window-bucket) — stable,
replay-safe.

## Grouping laws (deterministic, config in deps.yaml `correlations:`)

1. Same correlation group + same identity + overlapping window → ONE
   incident. Symptoms accumulate (evidence, worst severity wins).
2. Different groups → NEVER merged, however close in time.
3. `suspected_cause` comes from the correlation rule and is **never**
   verified. `verified_cause` is written only by resolution evidence.
4. Recurrence after resolution → reopen (`reopen_count`) with history.

Groups shipped: factory-supply-exhaustion (queue_empty+starvation),
intake-contamination (AppleDouble), watcher-failure (stale watcher + stale
LKG symptom), canon-availability (outage + unreachable).

## Required scenarios — status

| scenario | result |
|---|---|
| empty queue + starvation risk | ✅ one incident `factory-supply-exhaustion` (shadow run) |
| AppleDouble + queue growth | ✅ `intake-contamination` (shadow: 176,709 dead jobs) |
| repeated manufacturer endpoint failures | ⛔ telemetry unavailable — producer-side adapter spec + handoff proposed (see SHADOW-RUN) |
| sentinel watcher failure + stale LKG | ✅ one incident `watcher-failure` (shadow run) |
| machine unreachable vs service unhealthy | ✅ separate events by law (fleet tests) |
| multiple agents report same blocker | ✅ conflict path + single-incident grouping; hints never merge |
| resolved incident recurring | ✅ reopen with history (test) |

## Replay evidence (live run + tests)

- Shadow run 1: 3 incidents from 7 proposals (queue_empty+starvation merged;
  junk-flood alone; watcher alone). Run 2 (500 new events): **0 new
  incidents/proposals** — window buckets + fingerprint dedup hold.
- Failure injection (`tests/test_director_03.py`): same-cause merge,
  distinct-never-merge, suspected≠verified, reopen-on-recurrence,
  replay-no-duplicates, watcher+LKG merge.

## Mitigation vs root cause vs verified recovery

- **Mitigation** = `proposed_action` on an open incident (observe mode:
  proposed only).
- **Root-cause repair** = owner action outside the director (e.g. factory
  `._` fix) — tracked via incident evidence growth/decay.
- **Verified recovery** = `resolve(incident_id, evidence)` — only from
  replayed resolution evidence, never from a model opinion (LLM may
  suggest causal links; they stay hypotheses in `suspected_cause`).
