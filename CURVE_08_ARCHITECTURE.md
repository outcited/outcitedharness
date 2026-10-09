# CURVE-08 Architecture — Evidence-Grounded Engineering Decision Engine

Branch `curve/08-decision-engine`, worktree `/Users/samkim/Harnessv1-curve08`,
base `curve/07e-supply@f08d365b` (verified: bundle v3
`curve-evidence-bundle-v3-5875789dcf3f`, 123 rows, SHA-verified; 100/104
legend-bound, 4 `legend_ambiguity` refusals; 2 cross-manufacturer cells;
0 human approvals; probes 16/16; suite 1416 at base).

## R0 reconnaissance findings

- **Worktrees/branches:** six active worktrees; FACET-02
  (`facet/cohort-narrowing-v1@60280ac6`, unmerged, another agent) already
  built a candidate-grain cohort model in `harness/search/{cohort,axes,
  vendors}.py`; search lineage (`search/evidence-retrieval-v0`) carries
  the evidence-unit store. Neither is merged into the curve base.
- **Decision:** CURVE-08 does NOT merge foreign lineages (that would drag
  the in-flight search stack into the evidence lane). It implements the
  candidate model in `harness/electronics/candidates.py`
  **contract-compatible** with FACET-02: same grain honesty, same typed
  value kinds (`point|interval|inequality|unknown|text`), same
  candidate≠evidence law, plus `Candidate.to_facet_candidate()` mapping
  1:1 onto their dataclass for the merge.
- **M4/M5 boundary:** M5 (this repo) owns evidence correctness; M4
  (`outcited.com`, CURVE-06 `curve/06-decision-engine@23838f1`) owns
  discovery/commercial decisions. CURVE-08 stays upstream: it produces
  decision-grade answers over frozen bundles; M4's engine can consume
  them unchanged.
- **Ownership:** catalog admission = M4 catalog owner (packets prepared,
  never applied); human adjudication = authorized reviewers (packets
  pending, never simulated); search index = search-lane owner (untouched).

## Reusable components (no duplicates)

| component | reused from |
| --- | --- |
| condition law, bounded interpolation, significance | `curve_evidence.py` (CURVE-02/05B/07E) |
| five-level coverage classification | CURVE-07E coverage matrix semantics |
| frozen evidence rows + provenance | bundle v3 (SHA-verified load) |
| device ratings + admission packets | `_device_ratings.json`, v3 manifest |
| catalog parametrics | frozen provenance-stamped slice of M4's power catalog (`_catalog_slice.json`) |
| candidate vocabulary | FACET-02 cohort/axes contracts (mirrored) |

## New contracts

- `harness/electronics/candidates.py` — `Candidate` identity
  (manufacturer/category/subcategory/family/series/device/opn/grain/
  source_authority/membership/parametrics/evidence_refs), builder over
  catalog+bundle+packets+ratings, seven mandated counters.
- `harness/electronics/eligibility.py` — rule registry
  (`vin_min/vin_max/iout_min/vout/temp_max`), verdicts
  `eligible|ineligible|unknown`, every ineligibility carries rule,
  required, rated, scope, evidence; thermal note never "confirmed".
- `harness/electronics/decision_engine.py` — layered answer: identity →
  hard eligibility → evidence availability (comparable / non-comparable /
  missing / verification-pending) → condition-matched comparison with
  interpolation disclosure (supporting points, method, conditions, source
  series, uncertainty, limitations) → five-level classification of the
  produced comparison → approximate-scenario list (separate, labeled).

## Identity and evidence flow

catalog OPN/device ─┐
datasheet ratings ──┼→ Candidate (grain honest) ←─ bundle rows attach
admission packets ──┘        │                    (device rows → device
                             │                    candidate; part-less
                             ▼                    rows → family candidate)
                    hard eligibility (ratings only)
                             │
                             ▼
                    evidence availability per candidate
                             │
                             ▼
              condition-matched comparison (comparator)
                             │
              five-level grain disclosure + unknowns + questions

A candidate with 50 curves counts once (`candidate_counts` keeps
`evidence_rows_attached` separate from `total_candidates`).

## Hard invariants (all regression-tested)

1. Hard eligibility precedes every curve comparison; curves never
   eliminate and never rescue.
2. Missing ratings ⇒ `unknown`, retained and reported.
3. Typical values never guarantee; `guarantee: false` on every entry.
4. Interpolation only inside the digitized domain; supporting points
   disclosed; extrapolation refused with reason.
5. Five comparison levels reported, never merged.
6. Approximate/scenario comparisons labeled separately.
7. Candidate identity independent of document count.
8. Family-scoped evidence never expands to device/OPN applicability.
9. Bundle loads SHA-verified; v1/v2 immutable.
10. No gold promotion, no catalog writes, no production flips.

## Acceptance tests

`tests/test_curve08.py` (16): candidate collapse + facet mapping +
identity stability; LM5161/SiC464 3 A ineligibility with evidence;
unknown-never-failure; thermal-never-confirmed; eligibility
curve-independence; the four PRD verification values regenerated from
immutable evidence (77.95/93.94/92.96/92.95 at 48 V→5 V, 0.5 A);
interpolation/adjudication/provenance disclosure; cross-manufacturer vs
cross-family level correctness; 3 A gates; no extrapolation; approximate
labeling; counter separation.

## Known gaps carried forward

- Family-grain candidates exist only where evidence is family-scoped
  (SiC448); M4 catalog family columns remain empty (FACET-02 finding).
- SiC46x family candidate (p18/19 family-scoped rows) stays `unknown`
  on ratings by design.
- Approximate mode surfaces mismatches but does not compute values for
  them (values require condition-matched evidence or an explicitly
  approved approximation model — none exists yet).
