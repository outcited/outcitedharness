# FACET-03 v1 — CLOSED / VERIFIED

**Status:** CLOSED / VERIFIED (owner decision, 2026-10-09)
**Branch:** `facet/03-family-provenance` (final v1 state at this commit)
**Approval references:**
- Families: `FACET03-OWNER-APPROVAL-2026-10-09-961ef951` (626 memberships)
- Series: `FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d` (237 memberships)

## Carry-forward note (owner, verbatim)

> FACET-03 v1 closes with verified family and series memberships, completed
> catalog SHA re-key, preserved quote integrity, and stable downstream
> snapshots. Fifteen family→series hierarchy edges and seven unresolved
> source stems are intentionally deferred to FACET-03 v2; they do not
> invalidate or reopen verified v1 state.

## Closed v1 state (all measured, all auditable)

| Layer | State |
|---|---|
| Family identities | 4 verified (OptiMOS, CoolMOS, CoolSiC, StrongIRFET) |
| Family memberships | 626/626 verified, hash-bound promotion, 0 conflicts |
| Series identities | 15 verified |
| Series memberships | 237/237 verified (387 honest unknowns, 2 ambiguous excluded) |
| Provenance | 1,913/1,918 stems byte-verified (99.7%); 47,131 pages quote-recovered; evidence index re-keyed 1:1 with 100% locator integrity |
| Catalog re-key | 86,406 claims + 1,874 part_sources + 1,874 parts on verified shas; backup `catalog.db.bak-pre-rekey-20261009T181224Z` |
| Quote integrity | checksum PRE == POST over all 86,736 quotes (`3e4f3022ca86…`) — byte-identical |
| Downstream stability | M4-facing identity snapshot unchanged (`identity-snapshot-v1-7543bb485dee9426`) across series promotion |
| Independent review | families 77/77 supported (risk-stratified, source-PDF); series 42/42 supported |
| Audit chain | PDF bytes → locator (page+verbatim) → packet sha256 → owner ref in meta → verified snapshot |

## FACET-03 v2 docket (deferred, does not reopen v1)

1. **15 family→series hierarchy edges** (status `proposed`) — a new
   structural layer, not a defect in the verified 237 series→OPN
   memberships. v2: owner packet + promote_packet, same contract.
2. **2 quote-unverified stems** (`infineon-IPP034NE7N3 G` 0/4 quotes,
   `rohm-RSH065N06` 0/2; both sha-resolved + byte-verified, re-key
   correctly withheld) — bounded provenance investigations: substrate gap
   vs revision drift; note the malformed space in the first stem.
3. **5 unresolved BQ stems** (BQ2000T/BQ24003/BQ24008/BQ24071/BQ24401,
   324 claims, discovery-only) — re-acquisition work (mac-mini lane,
   already mailed), not an admission-machinery failure.
4. **Search-index cosmetics** — next catalog-driven rebuild keys claims
   directly off real shas; `+prov1` units remain valid history.
   Self-healing; no action.

Candidate v2 additions (evidence-backed, from the TI/Rohm scan):
`functional_class` backfill from TI printed functional descriptors
("Battery Charger", "Power Path Management") — same admission contract.

## Boundary statement

v1 proved identity and provenance admission works end-to-end under the
fundamental invariant (relationships are evidence-bearing identity claims;
promotion is byte-bound to owner-signed packets; unknowns stay unknown).
v2 handles hierarchy completion and residual cleanup.
