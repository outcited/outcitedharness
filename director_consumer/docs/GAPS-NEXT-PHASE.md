# DIRECTOR-02 — known gaps and next-phase recommendations

## Known gaps (accepted at this gate)

1. **Fleet self-identity:** the local machine surfaces as a hostname like
   `localhost` in Tailscale status; registry needs a self-id rule
   (map Self key → canonical id). Observed in shadow run finding #4.
2. **Stalled-lease measurement:** this production `factory.sqlite3` lacks a
   queryable `lease_expires_at` on chunks — reported UNKNOWN with the
   missing measurement named. Improvement: count `chunk_expired` events as
   stall evidence instead.
3. **Consumption rate:** needs ≥2 ledger observations; the 10-min loop
   self-heals this within one interval. Reported UNKNOWN until then.
4. **Incident correlation** is window-dedup only: `queue_empty` +
   `starvation_risk` (one cause, two symptoms) filed as two assessments
   with one root. Next phase: cause-signature grouping into one incident
   record with merged evidence.
5. **Per-manufacturer acquisition visibility** is indirect (intake_anomaly
   only). Manufacturer MCP/API regressions, bot sensitivity and crawler
   failures need a crawler-side feed from mac-mini/N100 lanes (out of
   reach from M5 mirrors today).
6. **Agent session discovery** is declared (fleet.yaml) + identity parse
   (AGENT_NETWORK.md); live OpenCode session probing (worktrees, branches,
   task ownership) is prepared-but-disabled (§7: do not interrupt
   interactive sessions; spawning interface prepared, not enabled).
7. **Worker-hour model:** continuity measures documents; extraction
   worker-hours need per-doc CPU timing (facet lanes uninstrumented).
   Doc-count and worker-hours are reported separately per the mandate —
   the hours column is UNKNOWN until instrumented.
8. **Fleet snapshot staleness:** fleet_state snapshots stopped 2026-10-12
   (CHANGES.md remains live). Registry reports service health with the
   snapshot's timestamp as provenance — stale telemetry is visible, not
   guessed. Production-side cadence fix is owned by ops.
9. **Metered spend authorization flow:** `authorization_ref` plumbing and
   hard refusals are enforced in the router; the human task-authorization
   workflow itself is Sam-side process, not software.

## Acceptance criteria status

| criterion | status |
|---|---|
| 1 discovery of machines/agents/services/workloads | ✅ registry from 4 sources; workloads = containers/services |
| 2 M5→Ryzen migration recognized | ✅ fleet.yaml migration + status=migrating + briefing flag |
| 3 backlog coverage or explicit UNKNOWN | ✅ shadow run: docs known, hours UNKNOWN with named measurement |
| 4 early bottleneck identification | ✅ starvation/queue_empty/junk-flood fired on live state |
| 5 evidence-backed root cause + owner | ✅ every assessment carries source refs + owner authority |
| 6 no duplicate incidents / no false paging on replay | ✅ run 2 = 0 proposals |
| 7 project isolation + authority | ✅ projects/designwins policy + isolation tests |
| 8 no unauthorized metered calls | ✅ router hard-refusal tests + usage ledger |
| 9 state persists across restarts/outages | ✅ failure-injection suites (01A/01B/02) |
| 10 useful operational briefing | ✅ briefings/BRIEFING-*.md per run |

## Next phase (recommended order)

1. Correlate same-cause symptoms into single incidents (gap 4).
2. Crawler/factory feed from mac-mini (gap 5) — manufacturer-level
   acquisition health.
3. Per-doc timing instrumentation for worker-hours (gap 7).
4. Advisory phase: delivery of proposed messages behind human approval
   (messaging is built and tested; delivery stays disabled until granted).
5. Deploy to Ryzen #1 per UBUNTU-DEPLOYMENT.md; retire M5 runtime role.
6. Agent-session discovery (read-only worktree/branch scan) and the
   durable task-ownership model for future director-managed agents.
