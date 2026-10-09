# FACET-03 v2 DOCKET — state after v1 closure (2026-10-09)

v1 is CLOSED/VERIFIED (see FACET03_V1_CLOSURE.md). This file tracks v2
preparation done autonomously; every promotion remains owner-gated.

## Item 1 — family→series hierarchy edges: PACKETS STAGED

15 proposed edges (each evidenced by the same byte-verified membership
quote carrying the printed designator), grouped per parent family,
admission-packet schema v1, in `facet03-packets/series-edges/`:

| Packet | SHA-256 | Edges |
|---|---|---|
| family-series-edges-coolmos-review-v1.json | `b8a3bc5c1417383fc35243d38dab9c1240c6f8bbac2dbdf6c866b343e2d46fb3` | 7 |
| family-series-edges-optimos-review-v1.json | `82958d8dc9ffda415fd297b211f06c5956ca9acf5972c4319e35dd4d1da59571` | 6 |
| family-series-edges-coolsic-review-v1.json | `ba3e9615cbe9ac9ea0c76b4de805e517cba52d8479e765bc2b35aafd338f23f6` | 1 |
| family-series-edges-strongirfet-review-v1.json | `ef19bfae72e31604529dca172d03caf9970ade9a2581ed3cbd186f243de87268` | 1 |

Ready for the same sign-off flow: owner review → reference →
`promote_packet` per packet (hash + population + drift bound). Note:
`promote_packet` verifies `parent_id`-rooted contains-relationships — these
packets' parent is the family, children are series identities; the
mechanics are identical.

## Item 2 — quote-unverified stems: INVESTIGATION CLOSED

See FACET03_V2_INVESTIGATIONS.md. Summary: RSH065N06 = correct document
(printed OPN confirmed in substrate), quotes unlinearizable (column-major
table flattening; substrate also shows a unit-misalignment defect worth an
extraction-lane look) → v2 re-key candidate under an explicit
printed-OPN-identity rule (owner one-liner, 2 claims). IPP034NE7N3 G =
scanned PDF, corrupt font layer (needs_ocr, alnum 0.211), claims were
vision-extracted → stays discovery-only until a vision/OCR substrate pass
(1 document, bounded).

## Item 3 — five BQ stems: AWAITING mac-mini

Mail delivered 2026-10-08 (queue-fix-six-missing-pdfs); no reply yet.
324 claims discovery-only. Nothing to do on this lane until re-acquisition
lands; resolver re-runs automatically pick up new vault/pairs entries.

## Item 4 — search-index cosmetics: no action

Self-heals on next catalog-driven rebuild (catalog now carries verified
shas post-re-key; `+prov1` units remain valid history).

## Candidate item 5 — functional_class (not started, needs owner scoping)

TI printed functional descriptors ("Battery Charger", "Power Path
Management", "Single-Cell Switched", ...) evidenced in FACET03_TI_ROHM_SCAN.md.
Would reuse: vocabulary curation → backfill → packets → promote_packet.
Blocked on: owner decision to open the dimension + vocabulary review.

## Proposed v2 rule additions (owner decisions, none applied)

1. Printed-OPN-identity as alternative content linkage for re-key (item 2).
2. OCR-substrate queue census for needs_ocr documents with vision-extracted
   claims (sizes the IPP034NE7N3 G class across the corpus).

## Docket state after owner decisions 2026-10-09 (executed)

- **Item 1 DONE**: 15/15 family→series edges promoted via hash-bound
  `promote_packet(child_kind="series")` — scoped variant added + tested
  (unscoped promotion drift-refuses edge packets; family memberships
  untouched: 626 verified before and after). Ref
  `FACET03-SERIES-EDGES-OWNER-APPROVAL-2026-10-09-a56e177d`. Registry:
  878 relationships, all verified, 0 proposed. Decision record:
  `ADMISSION_DECISION_SERIES_EDGES_2026-10-09.json`.
- **Item 2 split by owner ruling**:
  - `rohm-RSH065N06` **RE-KEYED** under the printed-OPN-identity rule
    (owner-approved one-liner): 2 claims + 1 part_source + 1 part onto
    fdfe0721…, quote integrity byte-identical, backup
    `catalog.db.bak-pre-rohm-rekey-20261009T191554Z`; provenance_map
    annotated `pairs_manifest+printed_opn_identity`, quote counts NOT
    falsified (still 0/2 — the rule is identity linkage, not quote
    verification). Search index aligned (3 units → real sha under
    `+prov2-opnid`; 1 page-graded, 2 honestly discovery-only).
    Remaining unhashed claims: 328.
  - `infineon-IPP034NE7N3 G` **NOT re-keyed** (owner: repair first).
    OCR/vision repair STAGED as
    `facet03-packets/REPAIR_SPEC_infineon-IPP034NE7N3-G.json` (bounded:
    1 document, 4 claims; needs vision-lane job submission approval).
    Hygiene finding: catalog already clean (`IPP034NE7N3`); the trailing
    " G" lives only in the filename-derived doc stem — no catalog mutation
    performed or needed.
- Modeling gap noted (tidiness, unactioned): OPN children exist as
  relationship child_ids without `identities` rows; snapshot/crosswalk
  unaffected. v2 candidate.
- Items 3 (BQ reacquisition) and M4 parity: unchanged, waiting external.
