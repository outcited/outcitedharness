# ACCEPTANCE-LEDGER — 05C findings, fixes, proofs (base f0796490)

## Baseline

Branch `director/05c-runtime-acceptance` from f0796490 (05B). 12 active
worktrees preserved; the "unrelated extraction commits" in prior
comparisons are `c3ad1cf8`/`5e5e729a` (agent/power-data-01 lane) plus
uncommitted ops-session scripts — not touched.

## Defect reproduction vs f0796490 (all 7 attempted)

| # | reported defect | reproduced? | correction | verification |
|---|---|---|---|---|
| 1 | consumer store has no separate operator identity | YES (default path could never authorize) | production pins service uid to the process; operator uid from protected config; unset = fail closed; test doubles labeled `test_identities` | 05c + rehearsal A |
| 2 | nonce commits before delivery intent | YES (crash stranded the grant) | ONE transaction owns validate+reserve+envelope+intent+audit; crash-before-commit consumes nothing; same approval retries | `test_crash_boundary_after_nonce_before_intent` (rewritten to the new invariant), rehearsal E/F/G |
| 3 | concurrency test serialized by test lock | YES (deliver() inside `with lock`) | lock removed; 6 independent processes via spawn; CAS intent transition; per-caller outcomes asserted | rehearsal I, `test_concurrent_delivery_single_message` |
| 4 | UID-only transport can't split same-UID sessions | YES | ack ENVELOPE + registered session binding (project/task/epoch→session) + transport evidence; caller flags are not trust | `test_forged_ack...`, rehearsal J/K/L |
| 5 | resolve() promoted suspected→verified | YES (COALESCE) | verified_cause only from full receipts (source/scope/observed_at/method/result); recovery evidence occurrence-bound | `test_suspected_cause_never_promoted...`, rehearsal N |
| 6 | migrations assign rows to default | PARTIAL — correct for genuinely unattributable rows (audited); missing fixture/interrupt proofs | COALESCE(project,'default') wherever the legacy table has the column; migration_log audit; interrupt-rollback tests for pre-05/05B shapes | `test_pre05_fixture...`, `test_migration_interrupt...` |
| 7 | context+model not one durable path | YES | immutable decision artifacts (`decisions/<finding>-<digest>.json`) with compiler version + source digests; model receives the packet; decision record carries packet_digest + advisory-only | `test_decision_packet_persisted...` |

## Measurement corrections (§8)

- **AppleDouble LIKE-wildcard bug fixed**: `%._%` matched ordinary
  `file._v2.pdf`-style names; now literal basename prefix via GLOB
  (`*/._*`, `pdf:._*`) — case T proves 5/5 junk and 0/3 false positives.
- All measurements carry source/locator/time/lane/unit/population/
  completeness (`measurement_meta`); chunk counts ≠ documents; facet
  passes ≠ unique docs; missing telemetry ≠ empty queue; queued ≠ leased;
  class-mapped ≠ executable; "measured" is a basis not a verdict;
  worker-hours ≠ CPU-core-hours ≠ elapsed (units named).
- **Admission gap stays a hypothesis**: empty inspected queue + eligible
  inventory elsewhere now reports `insufficient-evidence` (admission
  failure vs different execution lane), never proven starvation (case S).

## Paths changed

`authorization.py` (envelope, reserve-in-txn, identity model),
`messaging.py` (single-txn deliver, envelope persistence, integrity
conflict, NAS guard, mode boundary, envelope acks + quarantine),
`incidents.py` (receipts, occurrence), `continuity.py` (GLOB, meta,
insufficient-evidence), `scope.py` (COALESCE+audit), `context.py`
(private-by-default visibility), `consumer.py` (decision artifacts,
process identity), `projects.py` (shared_sources), `rehearse.py` + `cli
rehearse` (one-command rehearsal), `tests/test_director_05c.py` (15),
updated 04/05/05a/05b fixtures to the real contract.

## Remaining gaps (honest)

- Host-only gates: real OS uid separation, systemd, NAS transport
  identity, OpenCode version binding — NOT RUN (no Ryzen; no privileged
  changes to the working M5).
- Receiver-side dedup is demonstrated by the simulated receiver's
  acceptance ledger; the production receiver contract is the existing
  orgmail uuid custody (documented; not reimplemented).
- H (ack lost after acceptance) and O-lite cases are covered by
  quarantine/ledger semantics but not by a full network-loss simulation.
