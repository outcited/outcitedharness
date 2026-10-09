# FACET03 ARCHITECTURE — canonical family identity & provenance backfill

Branch `facet/03-family-provenance` (worktree `../Harnessv1-facet03`, off
FACET-02 @ 9b1bd6a5). Localhost/experimental only. No production catalog
mutations; no pushes without authorization.

## Baseline verification (§2 — re-measured 2026-10-08, all confirmed)

- 356,061 active evidence units; 356,061 staged vectors (fp 19b9f90628f8).
- catalog: 1,882 parts, family populated **0**, claims 86,736 —
  **100% `unhashed:` doc identities** (provenance-backfill population).
- 24 SpecAxis definitions; 24 canonical vendors; facets candidate-centric;
  evidence_refs resolve via /v1/units/{id}; FTS+sections default; semantic
  off. FACET-02 suite green at branch point.

## Recon findings (the material this PRD runs on)

1. **`exports/power-datasheet-pairs/<vendor>-20260908/pairs.jsonl`** — the
   burn-wave source manifest: 9,201 stems with **true `pdf_sha256`**,
   `pdf_bytes`, original `datasheet_url`, local `pdf/` files named
   `vendor-PART.pdf` (exactly the `unhashed:` stems). Byte-verification is
   possible: recompute sha256 from the local file.
2. **`vault/catalog/documents.jsonl`** (65,360 docs) + **`mpns.jsonl`**
   (32,553 MPN→sha, authority-tagged `how`) — the vault's canonical
   document/MPN registry with source-path provenance. Resolves stems the
   pairs miss (prefix-less variants like `unhashed:2N7002L`).
3. **Substrate results** (pipeline.db, 81,473 rows, real shas) hold the
   extracted page text for the exports docs — quote-level cross-check
   without reopening PDFs, plus front-matter family vocabulary for R2.
4. **Family evidence**: printed front matter ("600V CoolMOS C7 Power
   Device", "StrongIRFET 2 Power-Transistor", "750 V CoolSiC G1") — the
   only authority this branch uses. No census/matrix wave outputs exist on
   disk yet (family_census/family_device_matrix are code, not data); no
   published machine-readable family taxonomy in the repo.
5. **Admission/release authority**: pinout-claim admission machinery
   (electronics/admission.py, AdmissionStatus) and catalog writes belong to
   the extraction lane / owner. FACET-03 produces **proposed** records and
   admission-ready packets; it never writes verified identity or touches
   catalog.db.

## Identity model (R1)

New store `facet_identity.db` (experimental, derived, rebuildable):

```
identities(identity_id PK, kind, label, manufacturer_id, authority,
           status, created_at)
  kind: category|subcategory|functional_class|manufacturer|family|series|opn
  status: verified|proposed|ambiguous|unknown
relationships(parent_id, child_id, rel_type, authority, status,
              source_sha256, source_locator, norm_rule, confidence,
              conflict_status, created_at,
              UNIQUE(parent_id, child_id, rel_type))
provenance_map(stem PK, resolved_sha256, resolution, bytes_verified,
               bytes_match, quote_verified_claims, quote_total_claims,
               resolved_at)
releases(snapshot_id, built_at, counts_json, fingerprint)  -- R5 immutables
```

Laws encoded in the schema and tests:
- every relationship carries `authority` + `source_sha256` +
  `source_locator` (page + printed quote) — a relationship without source
  authority cannot be inserted (NOT NULL, tested);
- status transitions are one-way and explicit: `proposed → verified` only
  via an owner-approved admission packet applied by an authorized process;
  nothing in this branch ever writes `verified`;
- part-name similarity is NOT an authority: `norm_rule` values are
  restricted to evidence rules (`front_matter_vocab_v1`, `pairs_manifest`,
  `vault_documents`, `vault_mpns`); a bare string-similarity rule does not
  exist in the code;
- conflicting evidence (two families printed for one doc) sets
  `conflict_status='conflicting'` and admits neither — distinguishable,
  never silently merged;
- hierarchy is per-category: power grows family-under-manufacturer;
  nothing assumes a universal tree.

## Provenance backfill (R3) — resolution ladder, all byte-honest

For each `unhashed:<stem>` doc identity:
1. **pairs manifest**: exact stem → candidate sha; if the local PDF exists,
   recompute sha256 from the actual bytes (independent verification) and
   compare size; mismatch → next rung, never trusted on manifest alone.
2. **vault documents.jsonl**: mpn_guesses/filename match → candidate sha;
   verify the cas object exists AND recomputed hash matches its name.
3. **quote cross-check**: for the resolved sha, verify the burn claims'
   quotes appear in the substrate page text (pipeline.db results) — links
   the claims to the document through content, not filenames.
Output: `provenance_map` with per-rung flags + before/after coverage.
Unresolved or unverifiable → stays `unhashed:`/discovery-only. No prefix
swaps, no plausible-looking hashes. The **evidence index** (search lane,
derived store) is re-keyed for verified resolutions under a new
extraction_version (immutable supersede); **catalog.db is not touched** —
a re-key packet is produced for the extraction lane instead.

## Family backfill (R2) — printed-evidence only

Curated per-manufacturer family vocabulary (v1: Infineon — CoolMOS,
CoolSiC, StrongIRFET, OptiMOS; seeded from families already evidenced in
repo gold fixtures). Pipeline: for each provenance-resolved doc, scan
substrate page 1-2 text; a vocabulary hit produces a `proposed`
member_of(family→opn) relationship with {sha, page, quote, norm_rule
=front_matter_vocab_v1, confidence}. Zero or multiple hits → unknown /
conflicting. Coverage denominator = **distinct candidates**, per category.
TI/Rohm/ESP families: no printed-family authority in scope for v1 —
reported as unknown, not guessed from part prefixes.

## Facet integration (R4)

`cohort.build_cohort(identity_con=...)`: candidates gain
`family_id`/`family_status` ONLY from relationships (status surfaced).
New facet dimension `family` appears when relationships exist; returns
distinct family count, distinct OPN count, membership coverage, per-axis
coverage (unchanged, OPN-scoped), evidence-grade distribution, unknowns,
and explicit applicability limits. **No attribute propagation**: family
nodes carry no spec values; aggregation requires an explicit rule +
completeness statement (none established in v1 — documented, not faked).
FACET-02 responses unchanged when no identity db is supplied (backward
compat, regression-tested).

## M4 handoff (R5)

`GET /v1/identity/snapshot?category=power` → immutable, content-hashed
snapshot: per candidate {canonical_id `opn:<OPN>`, grain, parents
[{identity_id, kind, status, authority, evidence:[sha,page,quote]}],
manufacturer canonical id, coverage/unknown flags, snapshot_id, built_at}.
Snapshots append to `releases`; rebuild is deterministic (tested); M4
never parses manufacturer/part strings — it consumes ids + evidence refs.

## Bounded repair rule (execution §12)

Three failures on any resolution/rung → log blocker, mark unresolved,
continue. No infinite retry loops; every skip recorded in the coverage
report.
