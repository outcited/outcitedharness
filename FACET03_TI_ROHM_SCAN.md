# TI/Rohm family-vocabulary scan — evidence-driven null result (roadmap item 4, 2026-10-09)

**Question:** can the family dimension extend beyond Infineon using the
same printed-evidence standard?

**Method:** every provenance-resolved TI (14) and Rohm (676) document's
page-1 substrate text scanned for recurring capitalized product-line tokens
(≥3 occurrences, digits/part-numbers/boilerplate excluded). Deterministic,
read-only, reproducible.

**Result — TI (14 docs):** zero marketing-family tokens. Recurring hits are
cover boilerplate ("Software Support", "PRODUCTION DATA", "Buy Technical
Documents") and FUNCTIONAL descriptors ("Battery Charger", "Power Path
Management", "Single-Cell Switched", "Dual-Input", "Input Current
Optimizer").

**Result — Rohm (676 docs):** zero marketing-family tokens. Only the vendor
name ("ROHM Co" ×676), table-header fragments ("Parameter Symbol Value",
"V Continuous", "A Pulsed"), and generic class text ("Power MOSFET
Datasheet" ×249, "Key Specifications" ×193, "Over Current Protection" ×184).

## Verdict

1. **Family coverage ceiling at the printed-front-matter layer is
   Infineon-only for this corpus.** Any TI/Rohm "family" would require
   part-number-prefix inference (TPS/LM/RQ…→family), which is banned by the
   FACET-03 invariant ("a family relationship is an engineering identity
   claim requiring evidence — not a string-matching convenience"). TI/Rohm
   family coverage stays **unknown — honestly, by measurement, not by
   neglect.**
2. **Discovered opportunity (not built):** TI's recurring FUNCTIONAL
   descriptors are legitimate printed evidence for the
   `functional_class` identity kind (already in the identity model's KINDS,
   currently unused). "Battery Charger" / "Power Path Management" printed on
   a part's own cover is a manufacturer-sourced functional attribution —
   same evidence standard as family, different dimension. Candidate v2 item
   requiring vocabulary curation + owner review before any backfill.
3. Rohm's "Power MOSFET Datasheet" (×249) evidences CATEGORY, which the
   taxonomy resolver already handles — no new work needed.

## Paths to real TI/Rohm family coverage (owner decisions, not mine)

- Vendor-published product-family pages (acquisition lane: mac-mini) as a
  new evidence source with its own norm rule; or
- functional_class backfill from TI cover descriptors (above); or
- accept unknown and let series/OPN grains carry the narrowing (series v2
  is Infineon-only for the same evidence reason).

Reproduction: scan script pattern in this commit's history; sources =
provenance_map resolved shas → pipeline.db substrate pages.
