# Truth-Verification Improvements Plan — v1 (2026-10-04)

Four upgrades from the architecture review, phased by dependency and leverage.
Each has an acceptance test. Nothing here changes the architecture — it deepens
the verification core.

---

## Phase 1 — Crop-don't-page (removes a measured wrongness source)

**Problem**: full-page vision reads substitute adjacent tables (M4 measured
this on abs-max rows). Higher DPI on full pages also blows the token budget.

**Build** (1 day):
1. `harness/pipeline/region_detect.py` — locate table regions on a page:
   text-layer table detection first (pymupdf `find_tables()` gives bboxes for
   text-native pages, free); fall back to layout heuristics (column gaps) for
   pages with partial text; pure-image pages keep full-page render.
2. `vision_worker.py` v2 — render table regions at 200 DPI (up from
   full-page 150), pad bbox by 20px, tag each extraction with its region
   bbox. Output carries `page`, `region_bbox`, `dpi`.
3. Router update: pillar map marks figure-only pages — those bypass region
   detection (nothing to detect), get full-page at 150 DPI as today.

**Acceptance**: pin-baseline rerun on the 144-page gold — recall must not
drop, name-exact must rise, hallucinated adjacent-table substitutions
(their 37-page class) must fall below 10.

---

## Phase 2 — Cell-level bboxes (image-truth vs text-truth, mechanically)

**Problem**: vision claims a value but can't prove WHERE on the image it
came from. Grounding was our legacy advantage (0.94 on pin tables) — it
needs to be structural.

**Build** (2 days, depends on Phase 1 regions):
1. Vision prompt v2: every extracted cell emits `{value, bbox}` where bbox
   is `[x1,y1,x2,y2]` in region coordinates. Structured-output contract;
   models that can't ground get one retry, then hold.
2. `harness/pipeline/crop_verify.py` — the deterministic closer: for each
   grounded cell, crop the bbox, OCR it (tesseract on asus4, CPU, free),
   string-match OCR text against the claimed value. Image-truth and
   text-truth now check each other mechanically.
3. Ledger classes: `cell_ocr_mismatch` (P0), `bbox_out_of_region` (P1).

**Acceptance**: plant corrupted bboxes and values in a 50-cell synthetic
set — 100% caught. Then 20 real pin pages: OCR-agreement rate reported
(that number becomes the new baseline metric).

---

## Phase 3 — Text-as-hint (vision becomes verifier, not open extractor)

**Problem**: open-ended vision extraction hallucinates tables (37 pages).
Verification is easier than extraction — give vision the claim to check.

**Build** (1 day):
1. For text-tier-confirmed claims, build a vision verification job:
   `{claim, value, quote}` + the region image → vision answers
   CONFIRMED / REFUTED / UNREADABLE with bbox of the evidence.
2. Only claims where text-tier was weak (low-quality text, needs_ocr pages,
   figure-printed tables) route here — the Renesas HM class.
3. Disagreement (text says yes, vision says no) = P0 hold, frontier pack
   auto-generated.

**Acceptance**: on the 20 substrate-missing Renesas HM pages (once M4 drops
the PDFs): text-hint verification recall ≥ 85% with hallucinations = 0.

---

## Phase 4 — Frontier arbitration packs (Sonnet as sworn witness)

**Problem**: frontier involvement must be rule-triggered, evidence-packed,
and auditable — never vibes.

**Build** (1.5 days):
1. `harness/pipeline/arbitrate.py` — triggered by: (a) tier disagreement,
   (b) geometry-validator conflict after one vision retry, (c) audit
   sampling (2% of verified rows per batch, seeded).
2. Pack format: region image (base64) + text layer excerpt + both tiers'
   claims/verdicts/quotes + the specific question. Sonnet must cite which
   evidence won, in the same `{verdict, reason_code, evidence_quote}`
   contract as everything else.
3. Budget: existing cap (50/day) enforced through budget_spend; every pack
   and verdict ledger-recorded.
4. Audit-sampling verdict: batch disagreement >2% → quarantine, ledger
   entry, council notified.

**Acceptance**: seeded disagreement set (20 known-conflicting claims from
the pin/ball gold) — frontier resolves ≥90% matching the human-adjudicated
answer, with citations.

---

## Standing checks (parallel, no dependencies)

- **Rerun-instability detector**: nightly job reruns a fixed 50-claim probe
  through each serving tier; any claim flipping across runs gets auto-held.
  Reuses the canon determinism probe pattern. (0.5 day)
- **Cross-family agreement metric**: cloud/local disagreement rate added to
  soak.csv as a daily line — drift in that number is an early warning, not
  a curiosity. (0.5 day)

---

## Sequence and math

| Phase | Days | Unblocks |
|---|---|---|
| 1 crop-don't-page | 1 | Phase 2 regions |
| 2 cell bboxes + OCR close-loop | 2 | the grounding moat |
| 3 text-as-hint | 1 | Renesas HM coverage (waiting on M4 PDF drop) |
| 4 frontier packs | 1.5 | audit gate for training data |
| standing checks | 1 | drift early-warning |

Total: ~6 build-days, phases 1→2 serial, 3 and 4 parallel after 1.
Every phase lands with its acceptance number in the ledger — same rule as
everything else: nothing ships unmeasured.
