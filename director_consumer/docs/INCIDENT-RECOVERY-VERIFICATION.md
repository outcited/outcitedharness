# INCIDENT-RECOVERY-VERIFICATION — lifecycle and evidence law

## Lifecycle

```
detected → investigating → mitigation_proposed → repair_in_progress
   → recovery_observed → recovery_verified → closed
reopened (from closed/verified), blocked, escalated (any active state)
```

All transitions evidence-gated; illegal moves raise.

## The distinction law (why symptom-clear cannot close)

| signal | evidence kind | suffices for verified recovery? |
|---|---|---|
| symptom disappeared | `symptom-clear` | **NEVER alone** |
| mitigation applied | `fix-deployed` | no — mitigation ≠ repair |
| root cause measured absent | `root-cause-fixed` | **yes** |
| fresh successful runs observed | `verified-fresh-run` | **yes** |

`recovery_verified` rejects unless a `root-cause-fixed` or
`verified-fresh-run` row exists. `closed` only from `recovery_verified`.
An LLM conclusion is never evidence (rows come from measured facts).

## Real-world scenarios (all exercised)

**Factory starvation** — detect queue-empty+inventory → assign owner →
offer/ack via mailbox → investigation shows the admission gap (inventory
never enters execution queues) → repair (admission step) → independent
queue-depth measurement (`root-cause-fixed`) → verified → closed. SHADOW
RUN reproduced this end-to-end on the live incident.

**AppleDouble contamination** — deleting existing junk is `symptom-clear`
only; verified recovery requires evidence that NEW contamination stopped
(`root-cause-fixed`: 0 `._` files across fresh deliveries). Tested.

**Sentinel watcher failure** — known defects (dir perms + missing
`PIPELINE_DB`); the repair request is prepared; `recovery_verified`
requires `verified-fresh-run` (successful watcher runs with refreshed
LKG), not "looks up". Tested.

**Manufacturer endpoint regression** — new failures compared against
telemetry history; version changes are hypotheses ("verify, do not
assume"); no fabricated observations when the feed is absent. Tested.

**Recurrence** — closed → `detected` with reopen counting; the shadow
incident store keeps first-seen/last-seen and recovery evidence forever.

## Restart / replay safety

Incident identity is hash-stable (correlation + identity + window);
re-observed symptoms update the same incident (no duplicates); recovery
evidence is append-only.
