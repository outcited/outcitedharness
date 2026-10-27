# Diagnostics — 2026-10-27 (read-only, no changes made on the mini)

## D1. Why 21 ready tasks but 1 running agent (production city)

**Verdict: stalled execution caused by a supervisor-side projection fault.
NOT a resource limit. NOT dependency blocking.**

Evidence chain:

| Check | Result | Rules out |
|---|---|---|
| Dependency analysis | 0/28 open beads have `blocked_by`/`needs`; `bd ready` returns 20+ items instantly | blocked scheduling |
| Store health (direct) | `bd -C city list` 0.15s; `bd ready` 0.17s; Dolt servers up (city/staging/harness/pilot-rig) | store down / dolt hang |
| Disk | 202Gi free on / | disk pressure |
| Agent pools | engineer 0/2 (min=0), harness-engineer 0/2, bd.dog 1/2 running | — (pools CAN scale) |
| Supervisor view | `ready` projection query returns **0/0** while status reports ready:21; `partial_errors`: "reading complete ready projection from cache: **bead cache unavailable**"; store-health "list timed out: context deadline exceeded" | — |

Mechanism: engineer pools size from the supervisor's ready view
(`scale_check` demand). The supervisor's proxied BdStore ready-projection
cache is never proven (`ErrCacheUnavailable` — an unproven projection must
decline, per internal tests in gascity), so pools see zero demand and never
spawn. The one running agent (bd.dog-1) was started by an ORDER
(`do-wisp-lah661`), not pool scaling — consistent with orders path working
while the cache path is faulted. Supporting bead: `do-d5x` "Startup health:
engineer-1-pool" (already in the city's queue — known symptom).

Both cities share the fault (staging reports the same two partial errors,
same empty `.gc/cache/`), so this is a supervisor/proxied-provider issue in
gc 1.5.1, not city-specific state.

## D2. Partial bead-cache / store-health errors (both cities)

Facts: `.gc/cache/` dirs exist (2026-10-24) but are EMPTY in both cities;
direct bd CLI is fast; city dolt server log shows heavy short-lived
connection churn (NewConnection/ConnectionClosed pairs, same second); city
`bd db-proxy-child` carries `--stop-epoch` (retirement marker) while staging/
harness proxies don't; 4 db-proxy children total, one per beads root, all
70h+ old (same era as supervisor uptime).

Interpretation: the supervisor's proxied provider fails to (re)build its
ready-projection cache — either its internal list deadline is too short for
the proxied path, or the proxy lifecycle (`--stop-epoch` on the city proxy)
left it talking to a retiring proxy. In-process timeouts ⇒ cache never
proven ⇒ every cache-only read declines ⇒ partial status.

## Recommended remediation (NONE EXECUTED — requires Sam's approval)

1. Preferred: supervisor restart during a quiet window (`gc supervisor` on
   the mini). Work survives in beads; sessions are adopted on restart
   (documented orchestrator behavior). Expected effect: cache rebuilds,
   ready projection returns, engineer pools scale to ready work.
   ⚠️ This IS an orchestration change — approval gate.
2. Alternative: check gc release notes > 1.5.1 for proxied-BdStore fixes
   and upgrade if a fix shipped (bigger change; needs its own gate).
3. Observation-only canary first: `gc status` inside the city dir on the
   mini (read-only) to see if the CLI view (non-proxied) already shows
   ready=21 — if yes, confirms the supervisor-only scope of the fault.
4. After fix: NEO's `city-status-partial` alert should clear; if engineers
   still don't scale, next hypothesis is agent-scope/label matching between
   ready beads and engineer pools (checked only after the cache fault is
   gone, to avoid two variables).
