# DIRECTOR-04-ARCHITECTURE — coordination, ownership, verified recovery

**base:** 03 @ 6f097cd1 · **this:** 04 @ (see commit) · observe-only; live
delivery/authority remain disabled until separately approved.

## New coordination layer (generic core)

| module | role |
|---|---|
| `tasks.py` | task-ownership contract: explicit lifecycle, evidence-bearing transitions, epochs + fencing, one accountable owner, contributors, reconciliation requests for unknown ownership |
| `messaging.py` | controlled agentmail delivery: authorization gate (default CLOSED), idempotent message ids, bounded retries, expiry→escalation, mailbox reconciliation, ack tracking, full audit |
| `incidents.py` + | recovery lifecycle `detected→investigating→mitigation_proposed→repair_in_progress→recovery_observed→recovery_verified→closed` (+ blocked/escalated/reopened), evidence-gated |
| `manufacturers.py` | read-only consumer of the mac-mini telemetry contract; endpoint regressions trigger investigation-with-history, never blind retries |
| `funnel.py` | supply funnel reconciliation: acquired→…→verified with the admission-gap diagnosis |

## Coordination flow

```
incident (correlated symptoms, 03)          task (04)
  └─ proposed action ──► offer to owner ──► acknowledged (ack ≠ delivery)
                                              │
         message (gated delivery, mailbox) ◄──┤
                                              ▼
                                     active → submitted
                                              │
                        verification (evidence required)
                                              ▼
                       verified → closed   |  failed → active
```

Incident side runs in parallel: repair progress + recovery evidence
(root-cause-fixed / verified-fresh-run) → recovery_verified → closed.
Symptom disappearance alone can NEVER close. LLM output is never evidence.

## Laws encoded in code (not prompts)

- transitions without evidence raise; illegal transitions raise
- stale epoch writes raise FencingError (no silent reclaim)
- delivery gate: `enabled AND authorization_ref` — both required
- delivery ≠ ack ≠ completion (tested as separate states)
- recovery_verified requires root-cause-fixed or verified-fresh-run rows
- closed only from recovery_verified

## What stays config

projects/designwins/policy.yaml gains `delivery: {enabled, authorization_ref}`
(default disabled). Everything else is engine mechanics over existing
protocol/DB boundaries. No new truth store: tasks/messages/incidents live
in the same director.db journal database, append-event style.
