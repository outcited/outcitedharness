# FACET-03 v2 — investigation record: the two quote-unverified stems

Closed investigation (v2 docket item 2). Read-only; both stems remain
discovery-only; the conservative re-key bar (quote_verified > 0) correctly
withheld both. Neither is an identity failure — two distinct root causes,
diagnosed from source:

## 1. `rohm-RSH065N06` (2 claims, sha fdfe0721…, byte-verified)

**Correct document, unlinearizable table quotes.**
- Printed OPN identity CONFIRMED in the substrate itself: page 1 reads
  "4V Drive Nch MOSFET RSH065N06" — identity established independently of
  the quotes.
- Substrate quality healthy (quality_ok=True, alnum 0.735, Distiller PDF).
- Quote values present but reordered: burn quote `VDSS V 60` (row-wise
  read) vs substrate linearization `VDSS\nSymbol\n60\nV` (column-major
  flattening). L1 normalization + despacing still fails — word ORDER
  differs, not characters.
- Bonus finding for the extraction lane: the same flattened table shows
  symbol/unit/value triples misaligned by one row (IS→1.6 A, ISP→26 W,
  PD→2.0 °C, Tch→150) — a substrate table-geometry defect; the burn lane's
  structured extraction was more faithful than the text substrate here.

**Disposition:** eligible for v2 re-key under an explicit new rule —
"printed OPN identity on the byte-verified document" as content linkage
when quote verification is structurally impossible (table linearization).
Owner one-liner; 2 claims.

## 2. `infineon-IPP034NE7N3 G` (4 claims, sha ae2379b0…, byte-verified)

**Scanned PDF with a corrupt font layer — text verification impossible.**
- Substrate metadata: `needs_ocr=True`, `quality_ok=False`, alnum_ratio
  0.211, producer "ScanSoft PDF Create! 6"; page text is control-character
  garbage (`\x1c# # \x1e...`).
- The burn claims ('30V','20V','175°C','1µA') were extracted through the
  vision path (cloud v41, image-capable) — they are claims about the same
  byte-verified PDF, but the TEXT substrate can never corroborate them.
- Stem carries a malformed trailing " G" (likely a package-variant suffix
  fused into the OPN during burn registration) — a catalog-hygiene note.

**Disposition:** stays discovery-only until a vision/OCR substrate pass
covers this document (vision lane exists; 1 document, bounded). No
identity claim either way.

## Rule additions proposed for v2 (owner decisions, not applied)

1. Re-key eligibility alternative: printed-OPN-identity linkage (case 1).
2. OCR-substrate queue for needs_ocr documents whose claims were
   vision-extracted (case 2 class; population unknown beyond this stem —
   a v2 census would size it).
