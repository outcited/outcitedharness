# DISCOVERY-01 IDENTITY AUTHORITY

Operator-facing record of where verified identity lives, how the serving
path reads it, and what authorized it. Generated from the live stores and
from git by `scripts/discovery01_identity_authority.py`; machine-readable
copy: `results/discovery01-remediation/b1_identity_authority.json`.
Locked by `tests/test_discovery01_b1_identity_authority.py`.

Regenerate after any admission change:

```
.venv/bin/python scripts/discovery01_identity_authority.py
```

## 1. Where verified family lives

`/Volumes/M5_4TB/extract-results/facet_identity.db` — the identity store,
which is the **single source of truth** for family and series identity.

| Table | Holds |
|---|---|
| `identities` | family/series nodes: `identity_id`, `kind`, `label`, `manufacturer_id`, `authority`, `status` |
| `relationships` | membership edges `parent_id → child_id` (`rel_type='contains'`) with `status`, `authority`, `source_sha256`, `source_locator`, `norm_rule`, `confidence`, `conflict_status` |
| `meta` | `approval:<identity_id>` rows — the owner approval reference that authorized each verified node |

Live state at the time of writing:

| Measure | Value |
|---|---|
| relationships | **878, all `verified`** (0 not verified) |
| OPN → family | **626** |
| OPN → series | **237** |
| series → family | **15** |
| verified family nodes | **4** — CoolMOS, CoolSiC, OptiMOS, StrongIRFET |
| verified series nodes | **15** |
| approval rows in `meta` | **23** (19 node approvals + 4 edge-approval records) |

## 2. Where verified series lives

The same two tables. Series identities are `identities.kind='series'`
(15 verified nodes) and their memberships are `relationships` rows with
`parent_id='series:mfr:infineon:<designator>'` and `child_id='opn:<OPN>'`
(237 verified). The 15 `series → family` edges are `relationships` rows
whose `child_id` is a `series:` id and whose parent is a family node.

## 3. How the serving path reads them

| Consumer | Reads |
|---|---|
| `harness/search/cohort.py::_attach_families` | `relationships JOIN identities WHERE rel_type='contains' AND i.kind='family'`; matches `child_id = 'opn:<OPN>'`; grants `family_id`/`family_label` **only** for a single non-conflicting membership; `status`, `authority`, `source_sha256`, `source_locator` travel with every membership and are **never upgraded** |
| `harness/search/identity.py::build_identity_snapshot` | the M4 candidate-identity contract (canonical ids, grain, parents with authority and membership evidence); recorded per session as `releases.m5_identity_snapshot`, content-addressed and idempotent |
| `harness/search/cohort.py::facets_for_cohort` | the family facet dimension uses the identity-derived `family_label` |

Guarantees that hold in code and are locked by tests:

- `verified` is reachable **only** through `identity.promote(con, id,
  approval=...)`, which refuses an empty approval reference and records it
  in `meta`. `ensure_identity(status='verified')` raises.
- `identity.add_relationship(status='verified')` raises: this branch cannot
  fabricate verified membership evidence. Relationships also require a real
  artifact sha (never `unhashed:`).
- Promoting an identity **node** does not silently upgrade its
  relationships; the served membership status is whatever the relationship
  row records. (In the live store every relationship row is `verified`, so
  serving reports `verified`.)
- Nothing propagates attributes from a family node to an OPN candidate, and
  family-grain nodes are never mapped to OPN candidates.

## 4. Does the catalog carry a materialized copy?

**No — and deliberately so.**

| Store | Family values | Note |
|---|---|---|
| `catalog.db parts.family` | **0 / 1,882** | selected by `cohort.build_cohort` but never used to construct a candidate; not the discovery authority |
| `search_index.db units.family` | **0 / 356,061** active units | `indexer.family_units()` builds **1,882 family-grain units** from `parts.family`, so those units carry empty family labels — a materialization gap on the **search** surface, not the discovery surface |
| `facet_identity.db` | **878 verified relationships** | authoritative |

`parts.family` was **not** populated to close this blocker: doing so would
duplicate identity and create a second source of truth. Materializing the
search index's family column would change index content and therefore the
search release fingerprint, so it requires explicit owner authorization
followed by a re-prime of the policy release cache
(`scripts/prime_discovery_release.py`).

The discovery response discloses the catalog gap itself: cohort notes state
*"family/mpn_base empty in catalog (0/1882) — candidate grain is OPN;
family-grain not fabricated"* plus membership counts with statuses.

## 5. What commit admitted them

| Commit | Role | In this branch's ancestry? |
|---|---|---|
| `6ba8c66e` | ADMISSION EXECUTED — all four family packets approved (626 OPN→family verified) | **No** |
| `b76f14be` | series-grain v2 — designators extracted from the same verified membership quotes (237 OPN→series) | **No** |
| `4e4398aa` | v2 owner decisions executed — 15/15 family→series edges promoted; adds `promote_packet` with child-kind scoping and drift refusal | **No** |
| `0c23a34d` | series signoff package — 15 packets / 237 memberships pinned by sha256 | **No** |
| `961ef951` | family owner signoff package (stop-before-admission) | Yes |
| `48afc342` | FACET-03 base — identity store with evidence laws | Yes |

These commits live on the `facet/03-family-provenance` and
`discovery/01-facet-decision-integration` lanes.

**Reproducibility consequence (recorded, not papered over):** this branch's
`harness/search/identity.py` contains no relationship-level promotion
(`UPDATE relationships SET status='verified'`), so it can neither fabricate
nor regenerate the live verified state. The live store remains
authoritative and the serving path reads it faithfully, but production
promotion must run from a branch that contains the admission commits — or
they must be merged with owner authorization.

Also on those lanes and absent here: `49557b74` (release-keyed cohort
cache — the R11.3 lever) and `4c1f45a6` (grain-weighting experiment,
measured negative, reverted to neutral).

## 6. What approval reference authorized them

| Reference | Scope | Recorded on |
|---|---|---|
| `FACET03-OWNER-APPROVAL-2026-10-09-961ef951` | the 626 OPN→family memberships in the four pinned packets (OptiMOS 243, CoolMOS 207, CoolSiC 127, StrongIRFET 49) | 4 `meta` approval rows (family nodes) |
| `FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d` | the 237 OPN→series memberships in the 15 pinned series packets | 15 `meta` approval rows (series nodes) |
| `FACET03-SERIES-EDGES-OWNER-APPROVAL-2026-10-09-a56e177d` | the 15 family→series edges | 4 `meta` rows keyed `family:mfr:infineon:<x>:edges:series` |

Approval scope is bounded by the pinned packet hashes; the recorded decision
text states that approval covers exactly those memberships and no others.

## 7. Documents corrected by this remediation

The following carried pre-admission statements that contradicted the live
store. Each now carries a dated STATUS CORRECTION pointing at this record;
the original text is preserved unaltered beneath it:

- `FACET03_ADMISSION_REVIEW.md` ("Nothing here is approved")
- `FACET03_OWNER_SIGNOFF.md` ("no `promote()` called", §7 decision block)
- `DISCOVERY01_LIVE_PARITY_HANDOFF.md` §7 ("all 626 … remain `proposed`")
- `DISCOVERY01_RELEASE_REPORT.md` item 4 ("626 memberships remain `proposed`")
- `DISCOVERY01_FINAL_PARITY.md` ("626 proposed family memberships")
- `DISCOVERY01_PRODUCTION_READINESS.md` B1 (assessment quoted the above and
  mis-reported 15 relationships as `proposed` from a mis-ordered query)

`tests/test_discovery01_b1_identity_authority.py` fails if any `*.md` in
this branch again asserts the pre-admission state without a correction that
names this record.
