# HANDOFF — Engineering Director platform, full background

**date:** 2026-10-17 · **from:** m5-opencode (director session) ·
**branch:** `director/05c-runtime-acceptance` @ `2a5a12cf` (pushed) ·
**status:** 05C closure complete; stopped for independent review and the
Ryzen's arrival. DIRECTOR-06 not begun.

---

## 1. THE MISSION (read this first)

We are building a **permanent, autonomous Engineering Director** — a
general-purpose engineering orchestration platform. DesignWins (datasheet
drill-down/elimination) is the FIRST PROJECT, not the platform's identity.
The director must:

1. Know the approved objective. 2. Know current reality. 3. Remember the
institution. 4. Detect the gap. 5. Determine the responsible action.
6. Enforce authority. 7. Verify the result. 8. Continue without waiting
for the human.

**Operating principle:** never wait for Sam to discover a problem the
system could have detected, investigated, and resolved itself. Optimize
for verified valuable work with minimum human intervention — not agent
count, tokens, or activity. Reasoning is abundant; human attention,
verified evidence, and critical-path capacity are scarce.

**The end experience:** Sam works on M4/M5/mac-mini; the director manages
organizational state continuously. When Sam returns: what changed, what
was accomplished, what's blocked, which decisions need his judgment.

---

## 2. WHERE WE ARE — the DIRECTOR chain

All on `director/*` branches of `outcited/outcitedharness`
(origin + cursor mirrors). Each stage is reviewable history:

| stage | commit | what it built |
|---|---|---|
| 01 | `aadad37e` | event ingestion, hash-chained journal, transactional outbox, 12 deterministic rules |
| 01A | `07f9d824` | 9 review blockers: rollback-safe head-cache, atomic outbox, pagination, structured canon matching, aliases, backfill suppression, watcher-health rule |
| 01B | `a74e7188` | byte-exact fleet cursor, test-tree discipline (clean-worktree verification), project-boundary doc |
| 02 | `47ff2343` | fleet awareness (online≠healthy), factory continuity with UNKNOWN discipline, model governance (metered refusal) |
| 03 | `6f097cd1` | incident correlation, ownership reconciliation (EVIDENCE-SUPPLY-04 lesson), OpenCode session discovery, supply funnel (admission-gap diagnosis) |
| 04 | `c5d792a9` | task-ownership contract (epochs/fencing), gated delivery, recovery verification, failover-tested ops brain |
| 05 | `17f26394` | project registry, context compiler, desired-state reconciler, scoped authorization |
| 05A | `01166e9a` | real multi-project service integration; operator trust boundary; crash-safe delivery; receipts-only verification |
| 05B | `f0796490` | legacy bypass removed; file-backed approvals; crash matrix; transport-verified acks |
| 05C | `322cdd95` → `d3310620` → `2a5a12cf` | end-to-end rehearsal (21→22 cases), corrective pass, cross-path contract closure + Ryzen candidate |

**Verification discipline used throughout (keep it):** negative tests
FIRST, demonstrate intended failure against the reviewed commit, fix the
shared production path, rerun green. Final verification always from a
**clean worktree at the submitted commit** (the 01B lesson: an earlier
claim came from a modified dev tree — never again).

---

## 3. CURRENT VERDICTS (keep them separate — never merge into a "GO")

- **SOFTWARE CONTRACTS: PASS** — invariants A/B/C demonstrated across
  normal, retry, reconciliation, and recovery paths (clean worktree
  `2a5a12cf`: 187/187 director tests; 1,650 repo tests with 1 known
  pre-existing failure `test_discovery_api::test_design_lifecycle`
  (baseline `d09d7d6e`); rehearsal 22/22).
- **HOST COMMISSIONING: NOT RUN** — Ryzen host checks prepared, executable,
  unrun (no Ryzen yet; no privileged M5 changes allowed).
- **LIVE COMMUNICATION: DISABLED** — first live message waits on: review +
  host checks + owner re-resolution + Sam's one approval artifact.

---

## 4. ARCHITECTURE — the three invariants and their validators

| invariant | validator | coverage |
|---|---|---|
| A. only the exact approved action executes | `AuthorizationStore.reserve()` (atomic grant→action binding) + persisted action envelope + `still_eligible()` pre-effect gate | one grant never funds a second action id; retries execute the persisted envelope, not mutable rows; destination changes can't redirect; revoked/expired stop visibly |
| B. only the authenticated agent/session accepts | `acceptance.validate_acceptance()` (sole path to acked) | scoped receipts required; bare ids rejected; origins established by trusted transport (never caller text/From/UID alone) |
| C. only successful, source-supported verification closes | `IncidentStore.validate_receipt()` + `establish_cause()` | failure results rejected; sources must resolve; outcome ≠ cause; old-occurrence evidence can't close recurrences; investigation completion ≠ production recovery |

**Modules** (`director_consumer/`): schema, state (journal/outbox/cursors),
adapters (5 read-only feeds), impact (rules engine), incidents
(correlation + lifecycle), tasks (ownership + fencing), messaging
(delivery + acks), authorization (operator-file approvals), acceptance,
continuity (factory measurements), funnel, fleet (registry), sessions,
agent_runtime, ownership, context (decision packets), reconciler
(desired-vs-observed), resources (placement, recommendation-only),
critical_path, policy/llm (model governance), briefing, rehearse.

**Config vs engine:** engine is project-agnostic; `projects/designwins/`
holds policy.yaml (authority + model governance), fleet.yaml (roles,
M5→Ryzen migration facts), project.yaml (objectives, shared_sources),
class-costs.json, machine-aliases.yaml. Test bans project nouns in kernel
modules. Private-by-default source visibility.

**Key data stores** (workspace-local sqlite `director.db`): journal
(hash-chained, append-only), proposals (project-scoped, transactional
outbox), tasks (+events, epochs), messages (+action_envelopes,
invalid_receipts), incidents (+recovery, occurrence-bound), authorizations
(+grant_bindings, nonces), reconciler_findings, model_usage, migration_log.

---

## 5. MEASUREMENT LAWS (learned the hard way — keep them)

- AppleDouble = literal basename prefix `._` (GLOB, never LIKE `%._%`).
- Chunk counts ≠ job counts ≠ documents. Never sum across lanes; conversions
  must be operator-declared (`population_aliases`), else UNKNOWN.
- Starvation needs lane-compatible populations; missing telemetry ≠ empty
  queue; "workers will idle" is only claimed on proven backlog-zero.
- Receipts, not vocabulary: "verified/PASS" words in text prove nothing.
- Historical backfill never pages; date-only stamps compare by day.
- The admission gap is a HYPOTHESIS until the lane's execution contract
  proves it; "insufficient evidence" is a legitimate answer.

---

## 6. LIVE EVIDENCE (what the director found on its own)

- Factory supply: executable queues empty while 121k rescan inventory sits
  outside the queue → `insufficient-evidence` (admission vs other lane) —
  the honest answer, not a fake starvation claim.
- 176,709 AppleDouble dead jobs in the substrate queue (real count, literal
  prefix), factory-side fix owned by mac-mini.
- Sentinel watchers failing since Oct 9 (dir owned `_lpoperator:1026`;
  hourly opens the retired `pipeline.db`) — watcher-health rule pages on it.
- Canon adjudicator FP8 = 8.3 tok/s, verdict-deterministic 5/5. Ops brain
  (asus2 NVFP4+NEXTN) = 24.7 tok/s, 3×, tool calls 5/5 clean. dgx1 =
  overflow/replica (3/3 verdict parity). Failover drill: 3.1s.
- Rehearsal (22 cases A–T): one-command `python -m director_consumer.cli
  rehearse --workspace …` runs the whole coordination story incl. real
  process-kill failpoints (E/F/G), 6-way concurrent delivery, spoofed
  sessions, forged receipts, crash/resume without re-approval.

---

## 7. OPERATIONAL WORLD OUTSIDE THE DIRECTOR (do not break it)

- **Power lane** (power-data-01 agent): production Rust lane live
  (`d09d7d6e`, factory.sqlite3), power-lane commits `c3ad1cf8`, `5e5e729a`,
  `52524ceb` in this lineage — PRESERVE, don't touch.
- **Extraction ops session uncommitted files** (not director code):
  `config/models.yaml`, `scripts/extract_dispatch.py`,
  `scripts/mbp_wave_orchestrator.sh`, `scripts/vision_batch_submit.py` —
  ops work in progress; leave them alone unless that session asks.
- **Fleet:** M5 (control plane + migrating), MBP Intel (facet worker),
  asus2 (ops brain), asus4 (canon/embedder/GCI), dgx1 (overflow, fragile),
  mac-mini (PDF factory), M4 (frontend/OpenCode), TP4 shard (dgx2/3, asus1/3).
  Facet waves run via `scripts/facet_wave_orchestrator.sh` (per-facet
  ledgers in `results/mbp-worker-20261014/`).
- **12 active worktrees** (other agents): curve/discovery/facet/queue/
  search/powerdata01 branches — DO NOT touch, reset, or rebase.

---

## 8. OPEN ITEMS (next session)

**Software gaps (named, not hidden):**
1. OpenCode signed session-origin receipts — `opencode.db` has no signed
   tokens; `OpenCodeSessionTransport` is an interim binding-file adapter.
   Real proof needs an OpenCode-side authenticated receipt.
2. Production receiver must emit scoped acceptance receipts (contract in
   `docs/CROSS-PATH-AUDIT.md`).

**Host checks (NOT RUN — Ryzen):** uid separation + approvals perms,
systemd kill -9, NAS mount identity, OpenCode version binding, model
policy smoke, backup/restore + receiver reconciliation, M5-disconnection
freshness. Executable steps in `docs/RYZEN-COMMISSIONING.md`.

**DIRECTOR-06 (do not begin without review):** one live coordination
thread — detect real supply/admission issue, compile context, resolve the
accountable owner (may no longer be on M5), generate the investigation
request, validate Sam's scoped approval artifact, deliver exactly one
message, obtain an attributable ack, monitor repair, verify recovery from
source evidence, store in memory. Success gate before widening: one
delivered+acked+verified thread, zero duplicates, zero unauthorized
attempts. See `docs/DIRECTOR-06-GATE.md` (grants no authority).

---

## 9. HOW TO VERIFY ANYTHING (cheat sheet)

```
# director suite (12 files, 187 tests)
.venv/bin/pytest tests/test_director_*.py -q
# full repo (expect exactly 1 known failure: test_discovery_api)
.venv/bin/pytest -q
# one-command rehearsal (22 cases, machine-readable report)
.venv/bin/python -m director_consumer.cli rehearse --workspace /tmp/rehearse
# clean-worktree verification (the only kind that counts)
git worktree add /tmp/verify <SHA> && cd /tmp/verify && <pytest> && git worktree remove /tmp/verify
# journal chain
.venv/bin/python -m director_consumer.cli verify --workspace <ws>
```

## 10. HARD BOUNDARIES (still in force)

No deployment, no merge to main, no live agentmail, no spawning, no real
task reassignment, no production queue/database edits, no releases, no
metered inference without explicit task-scoped authorization, no
disruption to the M5→Ryzen migration or other agents' worktrees. The
director may read everything; it may write only its own workspace.
