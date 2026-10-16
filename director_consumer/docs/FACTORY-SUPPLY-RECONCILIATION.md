# FACTORY-SUPPLY-RECONCILIATION — where usable work is held up

## The §6 discrepancy, resolved

DIRECTOR-03 observed: executable queue **0** docs while rescan inventory
holds **121,495** docs. `funnel.py` now distinguishes the stages:

| stage | value | source |
|---|---|---|
| acquired | 66,994 files (~9GB spool) | pdf-intake log |
| validated | **UNKNOWN** | missing: validation ledger (named) |
| eligible | 1,060 class-mapped (121,495 total inventory) | L0 join / path list |
| prioritized | **UNKNOWN** | missing: factory_targets.json mirror |
| queued | **0** | ra03 factory_chunks + pipeline jobs |
| leased | live-churning | ra03 claimed |
| completed/verified | 5,200 facet docs (+historical) | facet ledgers |

**Holdup verdict: `admission-gap`.** The rescan path list is ELIGIBLE work
that is not being admitted into any execution queue — the facet wave
orchestrators consume the list directly, and nothing feeds
`factory_chunks`/`jobs` from it. This is not missing supply (acquisition
is +666 docs/day) and not missing telemetry — it is a missing admission
step. The 04 shadow simulation proved the repair shape: an admission step
putting inventory into `factory_chunks` moves queued 0 → thousands.

## Worker-hours

9.64h was a PARTIAL floor over 1,060 class-mapped docs (class-costs.json,
bench-derived). Improvement path: per-doc measured processing times from
the facet lanes (each lane already timestamps runs — a per-doc timing
telemetry line is the missing measurement). Until then the model reports
PARTIAL with unmapped classes named, never a false total.

## L0 re-census / path-map proposal (read-only; NOT initiated)

Class coverage is 1,060/121,495 because the L0 census predates the vault
rescan root. Two options for approval (no rescan launched):
1. **path-map** (cheap): join census `source_path` suffixes to vault paths
   where stems match; estimates coverage, zero compute.
2. **L0 re-census** (compute): run `harness.electronics-l0` over the
   `/vault` root — deterministic, CPU-fan-out, the same engine as the
   198,240-doc census.

Recommendation: path-map first; re-census only for the unmapped remainder.

## Manufacturer supply (§5)

Feed unavailable from this host — consumer implemented against the
DIRECTOR-03 producer contract; handoff request to mac-mini remains
proposed (not delivered). No manufacturer data invented.
