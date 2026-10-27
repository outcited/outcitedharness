# Native Workloop — Production Admission Gate (prepared, NOT activated)

Date: 2026-10-27 · Prepared by m5-opencode · Every action below requires Sam's
explicit authorization before execution. Nothing here has been activated.

## P0 — Preserved proof: autonomous 3-step staging workflow

Admission (ONE command, 2026-10-27T20:15:19Z):
`gc sling neo-fixture neo-fixture-seq --formula` (staging city)

| Step | Bead | Closed (UTC) | Commit | Evidence |
|---|---|---|---|---|
| one | ds-5h5 | 20:28:54 | `b92ec85` | claim→PROOF.md line→close |
| two | ds-at2 | 20:31:51 | `e99c5d7` | became ready ONLY after one closed |
| three | ds-bm9 | 20:35:27 | `bafb9e9` | "workflow complete" line |
| finalize | ds-f4d | ✓ | — | native workflow-finalize step |
| root | ds-bz9 | ✓ | — | workflow closed |

- Formula: `city-staging/formulas/neo-fixture-seq.toml` (3 needs-chained
  steps, mol-do-work close contract per step).
- Routing: standalone formula stamps `gc.routed_to` on EVERY step
  (`internal/graphroute/graphroute.go:701`, fixes #796) — verified: all three
  steps + finalize carried routes at materialization.
- No intermediate manual sling: the only operator action in the window was
  the single admission; demand lines (`scaleCheck/poolDesired: neo-fixture = 1`)
  and spawns were engine-driven. Cold-pool spawn required `session = "tmux"`
  on the agent (ACP unroutable in a runtime built without the provider).
- Production parallel evidence: `do-1sm` claimed (20:03:45Z) and closed by the
  engineer unattended — but WITHOUT outcome metadata (bare sling, no formula
  contract) — the reason P3 mandates formula admission.
- **NOT independently proven: restart/replay** (supervisor restart was outside
  authorization). Bead/workflow state is durable (Dolt); adoption semantics
  documented; flagged open.

## P1 — bd.dog production diff (validated on staging, NOT applied)

Append to `~/designwins-orchestrator/city/city.toml`:
```toml
[[patches.agent]]
agent = "bd.dog"
provider = "opencode"
session = "tmux"
```
Validation performed live on staging (2026-10-27): with the patch applied,
`bd.dog` skip errors ("provider is required" → then "requires ACP transport")
STOPPED — skip count frozen 36→36 across a full controller tick; patch then
reverted. Mechanism: `ApplyPatches` → `applyAgentPatch` → `applyAgentMutation`
copies both `provider` and `session` (`internal/config/patch.go:553-558`;
city `AgentPatch.Session` documented `"acp" or "tmux"`). v1.5.1 schema-valid.
Hot-reloads via the config watcher — no supervisor restart needed.

## P2 — Admission matrix (production queue, 2026-10-27)

| Bead | Task | Status | GitHub source | Twin | Verdict |
|---|---|---|---|---|---|
| do-77q | CURVES-WINTER-01 | open, unblocked, Sam-created | gh:outcitedharness (attack4) | hw-wfn (open) | **Eligible** — needs winter host + MiMo vision coordination; medium blast radius |
| do-b6p | PACKAGE-TEMP-AXES-01 | open, unblocked, Sam-created | gh:outcitedharness (attack3) | hw-37d (open) | **Eligible — FIRST CANDIDATE** (registry-only, deterministic, in-repo acceptance criteria embedded in the bead) |
| do-xbs | INTERFACES-DENSITY-01 | open | gh:outcitedharness (attack2) | (rig mirror) | **EXCLUDED — superseded**: landed on main via PR #49 (`harness/electronics/interface_counts.py` @ `a14a953`); admission would duplicate shipped work |
| do-y5b | PIN-TABLES-VISION-01 | open, labeled `deferred` | gh:outcitedharness (attack5) | hw-b5l (open) | **EXCLUDED — deferred** by its own label |

Duplicate-work control: admit the do-* city bead ONLY (city `engineer` pool is
the proven path); the hw-* rig twins must be marked superseded at admission
time (one `bd update hw-* --append-notes` per twin, or leave flagged for the
Mayor) — rig-pool demand is Fault-B-blocked today, so hw-* routing would stall.
The 15 `gh-work` definition beads remain separate: **GitHub ingestion is not
execution authorization** (2 are explicitly `untrusted-definition`).

## P3 — Production acceptance contract (mol-do-work mechanism)

1. Authorization bound to the exact task (Sam names the bead; recorded here).
2. Admission: `gc sling engineer <bead> --on mol-do-work` — attaches the
   claim→work→close contract (the bare-sling do-1sm closure lacked outcome
   metadata; mol-do-work steps cannot close without it).
3. Agent `engineer` (city pool, proven), rig lane harness via work items.
4. Atomic claim via `bd --claim` (idempotent, lease-heartbeated).
5. Source-linked work: extraction law commit `b00584f5` cited in-bead; commits
   on a branch in `pilot-rig`.
6. Positive tests: the bead's own acceptance checklist (do-b6p: axis registry
   entries + knife-spec emission + typed-claims re-run) + existing dw-knives
   parity/gold suites. Negative: registry refuses unknown axes / duplicates.
7. PR to outcited/outcitedharness, CI green (checks + neo-gascity).
8. Outcome metadata: `gc.work_outcome=shipped`, `gc.work_commit`,
   `gc.work_verification` — REQUIRED (enforced by the formula's close block).
9. Independent verification before WORK_VERIFIED: NEO reads bead outcome +
   PR checks + CI via GitHub; closure alone is NOT acceptance.
10. No automatic merge/deploy — human reviews and merges.

## P4 — First rollout: do-b6p (PACKAGE-TEMP-AXES-01) — NOT EXECUTED

Why: registry-only (no inference engine), deterministic, embedded acceptance
criteria, smallest blast radius, all tests in-repo.

Operator sequence (on Sam's authorization):
```
# preflight (verify unchanged): bd show do-b6p open/unassigned; sessions list;
#   supervisor healthy; engineer pool idle
ssh mini: cd ~/designwins-orchestrator/city && \
  gc sling engineer do-b6p --on mol-do-work
```
Expected effects: wisp attached to do-b6p; do-b6p routed; within ~1 tick
`poolDesired: engineer = 1`; engineer session spawns (tmux transport on the
bare bead path is NOT needed — city runtime already routes ACP for the
production `engineer` — proven by do-1sm); claim; registry work + tests;
branch + PR; close with outcome metadata.
Monitoring: sessions/bead at t+90s/5min; PR + CI; NEO snapshot carries it.
Guarded recovery: un-route `bd update do-b6p --set-metadata gc.routed_to=`;
abandoned claim `bd reclaim do-b6p`; session stop never unclaims (verified).
At admission, mark hw-37d superseded (notes) to prevent duplicate execution.

## Remaining blockers

1. Sam: authorize do-b6p admission + the P1 bd.dog patch (independent).
2. Fault B (harness rig scale cwd) — blocks hw-* rig-pool execution only;
   city-pool path unaffected.
3. Restart/replay proof — pending a future authorized supervisor restart.
4. #7546 status-partial — reporting only; execution-proven unaffected.
