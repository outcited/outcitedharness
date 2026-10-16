# RECONCILIATION-E2E — real CLI, no-action decisions, recovery (§6/§11)

## Integration (no hand-assembled fixtures)

`cli run` builds DesiredState from the **project registry + real
TaskStore rows** (state/updated_at/verified progress/host/objective
links) and ObservedState from the **real stores** (continuity funnel,
ownership reconciler output, project incidents, messages, fleet
snapshot). Service tests launch the actual CLI subprocess.

## Live results (real feeds, two projects)

designwins: `objective-without-task` ×2 (its two approved objectives have
no linked tasks — missing work detected with NO error reported by anyone)
+ `starving-consumer` (admission gap). proj-b: `objective-without-task`
only — the critical-path leak was caught in shadow and fixed (declared
critical paths only). Restart: 0 new findings, all suppressed with
reasons; `cli verify` chain intact.

## No-action decisions

`NO_ACTION_REASONS` vocabulary (existing-owner-repairing, evidence-stale,
handoff-pending, work-productive, outside-delegated-authority,
insufficient-capacity, duplicate-incident, awaiting-verified-dependency,
no-safe-action) — every suppression records a reason; cooldown prevents
per-poll proposal spam.

## Recovery semantics

Findings resolve only with evidence; incident recovery requires
root-cause-fixed/verified-fresh-run (05); symptom disappearance never
resolves anything. Interrupt-and-resume and backup/restore proven via
the CLI (kill -9 → identical state, zero duplicates).
