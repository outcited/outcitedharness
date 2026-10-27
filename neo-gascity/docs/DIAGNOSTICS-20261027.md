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

## Recommended remediation (P4 — smallest safe change; NONE EXECUTED, approval gate)

**Preferred: single supervisor restart in a quiet window.** `gc supervisor`
serves BOTH cities from one process — restart affects production
`designwins-orchestrator` too, which is why it is explicitly approval-gated.

- Pre-flight (read-only): capture `GET /v0/city/{c}/status` for both cities +
  `gc -C city status` on the mini (non-proxied view) as the baseline.
- Execute (operator, on the mini): stop and re-run the supervisor the way it
  is hosted today (PID 40699; `gc supervisor run`), e.g.
  `pkill -f 'gc supervisor run'` then relaunch under the same mechanism it
  was started with (tmux/launchd — confirm with `ps -o ppid= -p <pid>` first).
- Verify (read-only, ≤5 min):
  1. `GET /health` → status ok, cities_running=2;
  2. `GET /v0/city/designwins-orchestrator/status` → `partial:false`,
     empty `partial_errors`, ready projection ≈ 21 (vs `bd ready` ground truth);
  3. `GET .../beads?status=ready` returns items (not 0/0);
  4. within ~2 ticks: engineer pool reports running sessions > 0 with ready
     work still present;
  5. sessions from before the restart show `adopted` (not respawned) — no
     lost session identity; `do-wisp-lah661` dog session continues.
- Rollback: another supervisor restart is itself the rollback for a bad
  restart (stateless process over durable stores). If gc 1.5.1 proves
  defective on restart, downgrade path: `brew install
  gascity@1.5.0`-style pin is NOT assumed available — instead keep 1.5.1 and
  file upstream; the store layer (bd/dolt) is independent and untouched.
- Why safe: work lives in Dolt-backed beads (survives), event log is
  append-only (`.gc/events.jsonl`), sessions are adopted on supervisor
  restart (documented orchestrator behavior, api.md "adopting_sessions").
- If a restart does NOT clear it: upgrade gc (>1.5.1) checking release notes
  for proxied-BdStore/cache fixes — separate, larger gate.
- After remediation: NEO's `city-status-partial` alert is the automatic
  canary (should clear within one publisher cycle).
