# FACET03 RELEASE REPORT — canonical family identity & provenance backfill

Branch `facet/03-family-provenance` (worktree `../Harnessv1-facet03`, off
FACET-02 @ 9b1bd6a5). **Committed locally; NOT pushed, NOT merged** per PRD
§12. Localhost/experimental only; no production catalog mutations.

## Changed files

- `harness/search/identity.py` (new) — identity/relationship/provenance store with evidence laws
- `harness/search/cohort.py` — family attachment + family facet dimension (backward compatible: no identity_con ⇒ FACET-02 behavior byte-identical)
- `harness/search/api.py` — `/v1/identity/snapshot`, facets wired to identity db, fail-closed handlers
- `scripts/facet03_resolve_provenance.py` (new) — R3 ladder: pairs manifest → vault registry → quote cross-check, byte-verified, bounded repair (3 attempts)
- `scripts/facet03_family_backfill.py` (new) — R2 printed-front-matter family resolution + admission packets
- `tests/test_facet03_identity.py` (new) — the 14 R6 validations
- `FACET03_ARCHITECTURE.md` (new) — R0 reconnaissance + design

## Test results

**1,458 passed** (1,445 FACET-02 regressions + 13 new R6 tests), 0 failed.
R6 mapping: (1) missing family → unknown ✓ (2) fabricated relationships
structurally impossible — authority/sha/norm-rule enforcement + verified
unreachable without promote(approval) ✓ (3) conflicts detected, both kept,
neither wins ✓ (4) alias→identity deterministic ✓ (5) family+children never
inflate same-grain counts ✓ (6) zero attribute propagation ✓ (7) tampered
manifest hash rejected — resolution requires re-hash of actual bytes ✓
(8) unresolved source stays discovery-only ✓ (9) rekey preserves
row/col/quote, recovers page with `page_source=quote_locate_v1` ✓
(10) revision conflicts distinguishable + dedup ✓ (11) snapshot rebuild
deterministic, content change moves id ✓ (12) rollback = drop identity
input; FACET-02 output byte-identical ✓ (13) full regression suite ✓
(14) invalid sources fail closed (builder raises; API returns 500, never an
empty-cohort masquerade) ✓.

## Provenance recovery — before/after (deliverable 7)

| Measure | Before | After |
|---|---|---|
| catalog docs with real sha | 0 / 1,918 (100% `unhashed:`) | **1,913 / 1,918 (99.7%)** |
| byte verification (re-hash of actual PDF) | — | 1,913 / 1,913, **0 mismatches** |
| quote cross-check vs substrate content | — | 37,247 / 43,007 (86.6%) |
| pages recovered (quote-locate) | 0 | **47,131** |
| evidence-grade active units (index) | 264,343 / 356,061 (74%*) | **315,318 / 346,919 (90.8%)** |
| discovery-only active units | all 86K claims | 36K→**31.6K** (5 unresolved docs + quote-miss cells) |
| benchmark locator precision | 0.988 | **0.988** (85/86; matched-hit mix shifted to re-keyed claims) |
| benchmark power recall@10 | 0.176 | **0.216** (grade boosts now apply to claims) |

*sections/curves were already graded; claims were 0% graded before.

Resolution ladder: pairs manifest (1,911) → vault documents registry (2);
5 stems unresolved (no manifest entry, no vault match — logged, left
`unhashed:`/discovery-only, never guessed). 0 provenance conflicts
(pairs vs vault agreed everywhere they overlapped). Re-key packet for the
extraction lane: `extract-results/facet03-packets/provenance-rekey-v1.jsonl`
(catalog.db untouched — re-keying the catalog itself needs lane-owner
authorization).

Evidence index re-key: 87,543 units re-issued under real shas at new
extraction versions (`…+prov1`, immutable supersede), 1,911 placeholder
docs retired (history preserved, visible via include_retired). Edge case
found and fixed in flight: byte-identical PDFs serving multiple stems
(2N7002/2N7002L) — resolved by sha-grouped writes + origin_stem in the
locator identity.

## Family coverage — before/after (deliverable 6)

Denominator = **distinct candidates** (1,882 OPNs), per PRD — not documents.

| Measure | Before | After |
|---|---|---|
| family identities (catalog) | 0 | 0 (catalog untouched) |
| family identities (identity store) | 0 | **4** (OptiMOS, CoolMOS, CoolSiC, StrongIRFET) |
| OPNs with family relationship | 0 | **626 (33.3%)** — all `proposed`, printed-evidence |
| verified relationships | 0 | **0 by design** (promotion requires owner approval of packets) |
| conflicting memberships | — | 0 |
| power-cohort membership coverage | 0 | **0.397** (626/1,577 cohort OPNs) |

Per family: OptiMOS 243 · CoolMOS 207 · CoolSiC 127 · StrongIRFET 49.
Every membership carries {source sha256 (byte-verified), page, verbatim
front-matter line, norm_rule=front_matter_vocab_v1, confidence}. Admission
packets: `extract-results/facet03-packets/family-packets-v1.jsonl`.

**Per-category honesty:** power = measured above. **MCU and connectors: no
data** — the catalog contains no mcu/connector candidates (power-only
population) and family vocabulary v1 covers Infineon only (the only vendor
with printed-family evidence in scope). Both report unknown; nothing
inferred from part-name prefixes (no such rule exists in the code).

## R4/R5 live behavior (measured)

- Family facet on the live pilot cohort: 4 values with distinct-candidate
  counts, membership_coverage 0.397, unknown 951 surfaced, status note
  attached; `c.family=CoolSiC` narrows 1,577 → 127.
- Snapshot: `identity-snapshot-v1-294d8141f57452aa` — deterministic across
  rebuilds; flags {proposed: 626, unknown: 951}; M4 consumes canonical ids
  + evidence refs, never parses names.

## Unresolved / remaining blockers

1. **5 unresolved stems** (no manifest/vault source) — stay discovery-only.
2. **Verified family coverage = 0** until owner admission of the packets
   (by design; promotion path tested).
3. **TI/Rohm/Espressif family vocabulary** out of v1 scope — coverage
   ceiling for power cohort is ~57% (898 Infineon / 1,577) until other
   vendors gain printed-family authority.
4. **Staged vectors orphaned by re-key** (they key pre-rekey unit ids;
   semantic is off-default so no serving impact). Re-attach requires a new
   embedding run — explicitly not initiated (directive §8).
5. **Catalog re-key** (claims_canonical.doc_sha256 unhashed→sha) needs
   extraction-lane authorization; packet is ready.
6. Cohort rebuild per request (~7s cold) — unchanged FACET-02 blocker.
7. Connector/MCU facet gate (403) unchanged until catalog coverage.

## M4 integration readiness

**Ready for M4 review.** Contract: `GET /v1/identity/snapshot?category=power`
(versioned, read-only, immutable, content-hashed; `?candidate=<OPN>` for
single fetch). Payload per candidate: canonical_id, grain, manufacturer_id
(canonical, resolution rule included), parents [{identity_id, label,
status, conflict_status, authority, norm_rule, evidence[{sha, page,
quote}]}], flags.family ∈ {proposed, ambiguous, conflicting, unknown}.
Stability: snapshot ids are reproducible; identity ids are stable across
rebuilds (deterministic normalization). M4 decision logic untouched.

## Acceptance criteria (§10) — status

- Family coverage measured honestly (distinct-candidate denominator; per
  category; unknowns first-class) ✓
- Every relationship has source authority (sha+page+quote enforced NOT NULL,
  placeholders rejected) ✓
- No family→OPN expansion (contains-relationships are membership only;
  zero attribute propagation, tested) ✓
- No fabricated hashes (byte re-verification at every rung; tamper test) ✓
- Unresolved evidence remains discovery-only ✓
- FACET-02 backward compatible (byte-identical without identity input) ✓
- Candidate identities stable across handoff (deterministic snapshot) ✓
- Reproducible snapshots (content-hash ids, rebuild-tested) ✓
- No unauthorized production changes (catalog.db untouched; search index =
  derived lane store; localhost only; nothing pushed) ✓

**Fundamental invariant honored:** every one of the 626 family
relationships is an evidence-bearing identity claim (byte-verified source
sha + printed page + verbatim line) — zero string-matching conveniences
exist in the code path, and the norm-rule set is closed.
