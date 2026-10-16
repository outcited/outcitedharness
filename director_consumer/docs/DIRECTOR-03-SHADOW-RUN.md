# DIRECTOR-03 — shadow-run report (live, read-only)

**workspace:** /tmp/d03-shadow · no messages sent, nothing dispatched,
production untouched. Two runs + chain verification.

## Runs

| run | events | assessments | historical | proposals | incidents |
|---|---|---|---|---|---|
| 1 | 553 | 76 | 49 suppressed | 5 | 3 opened |
| 2 (replay) | 500 (live factory churn) | 42 (P3, action=none) | 0 | 0 | 0 new |

`verify` → chain ok after both runs.

## What the director understood (definition-of-done proof)

**Incidents (3, correctly correlated):**
1. `factory-supply-exhaustion` P1 — queue_empty + starvation_risk merged
   into ONE incident (suspected cause: upstream acquisition; verified
   cause: still NULL). Root-cause repair is the factory's `._` fix +
   pipeline enqueues — the director tracks, never acts.
2. `intake-contamination` P2 — AppleDouble contamination 176,709 dead jobs
   (suspected: factory intake hygiene; owner hint: mac-mini factory).
3. `watcher-failure` P1 — sentinel stale LKG + watcher failure correlated
   (suspected: watcher failure — root cause KNOWN from 01A diagnosis:
   sentinel dir permission + missing PIPELINE_DB env; owner: ops).

**Ownership (17 real task IDs):** FACTORY-ORCHESTRATION-02/03,
PINOUT-*, EVIDENCE-SUPPLY-*, FACET03-* etc. — all correctly resolved to
`unknown` ownership (commits carry task IDs without agent attribution);
no false conflicts, no guessed owners. The reconciler is ready for the
next real conflict.

**Continuity:** starvation 0.0h basis=backlog-zero (real: queues empty);
acquisition +666 docs/day healthy; worker-hours 9.64h PARTIAL over the
1,060 class-mapped backlog docs (coverage gap named below); consumption
rate UNKNOWN pending 2nd observation (now recorded).

**Fleet:** 15 machines reconciled; m5 flagged `migrating`; service
telemetry UNKNOWN (snapshot 4 days stale — correct freshness law); 2
connectivity observations (offline first-sights) proposed as P1
investigations.

## Proposed actions (delivered as drafts only)

- 5 messages to `agent:m5-opencode`: junk-flood investigation, 2 machine
  connectivity checks, watcher investigation, starvation/queue note.
- 0 human escalations (nothing hit the Sam-only boundary this window).

## Manufacturer acquisition intelligence (§3) — adapter spec + handoff

Mac-mini factory telemetry is NOT available from this host (NAS Public
unreachable; no crawler logs mirrored). Per the mandate, no manufacturer
observations are fabricated. **Smallest producer-side adapter spec** for
the factory owner (mac-mini):

```
Producer: mac-mini factory process (existing) — write-only append
File: <factory-data-root>/manufacturer-telemetry.jsonl
Cadence: append on each crawl attempt; rotate daily
Record (one JSON object per line):
  ts | manufacturer | method (mcp|api|crawl) | endpoint | endpoint_version
  | outcome (ok|http_<code>|auth|rate-limited|bot-blocked|timeout)
  | documents_delivered | documents_failed | notes
Consumer: director reads a mirror of this file read-only (feeds.yaml).
```

**Proposed handoff (draft, not sent):** to `mac-mini` — request the
telemetry line format above (or an existing equivalent), so manufacturer
endpoint failures trigger investigation-with-history instead of blind
retries. Until then, manufacturer-level health remains UNKNOWN and
intake_anomaly is the only available signal.

## Failures injected / verified

Matrix from §9 covered by `tests/test_director_03.py` (24 tests) + the
48 inherited: starvation valid/unknown/unavailable, prioritized-supply
UNKNOWN, one-incident merges, distinct-never-merge, suspected≠verified,
reopen, replay, ownership conflict/hint/stale/unknown, session disconnect
+ title-law, stale snapshot, fresh snapshot, localhost/self identity,
migration state, metered refusal, total model outage, project isolation.
