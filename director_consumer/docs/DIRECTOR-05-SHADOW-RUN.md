# DIRECTOR-05 — shadow-run report

**workspace:** /tmp/d05-shadow · read-only; no messages delivered, no
actions executed, production untouched.

## Live results

- **DesignWins (real feeds):** 553 events → 76 assessments (49 historical
  suppressed) → 5 proposals; 3 open incidents (supply-exhaustion,
  intake-contamination, watcher-failure) — identical continuity with 04.
- **Project B (same kernel, same workspace):** registered `proj-b` with
  its own objective/policy; `TASK-1` owned by `agent-b` while
  designwins' `TASK-1` stayed unassigned — no collision.
- **Reconciliation:** proj-b → `task-without-active-owner` (its work
  offered, not yet acknowledged); designwins → `starving-consumer`
  (admission gap, real state). Findings are independent per project.
- **Context compiler:** "adjudicator canon endpoint change" → 45-item
  packet (authority + verified-fact + assertion classes) with source
  references — and no project-B material.

## Failure-injection (§11) — 23 new tests

restart during reconciliation · legacy migration interrupt/idempotence ·
duplicate events · conflicting project task IDs · unauthorized delivery ·
forged/unattributable acks (hints never count) · expired authorization ·
stale ownership epochs · session disconnect ≠ completion · host
migration · stale telemetry · starvation · superseded PRD never
authority · total inference loss · metered refusal · incomplete recovery ·
cross-project isolation · repeated reconciliations without duplicates.

Hash chain verifies; transactional outbox unchanged from 01A.

## Unresolved risks

- Context retrieval is term-based: vocabulary mismatch can miss evidence
  (uncertainties say "no evidence matched" — never silently empty).
- Per-machine hardware telemetry still UNKNOWN (no source; named).
- The admission-gap repair and manufacturer telemetry producer remain
  external owners' work — the kernel proposes, never acts.
- Live delivery/spawning remain disabled by design.
