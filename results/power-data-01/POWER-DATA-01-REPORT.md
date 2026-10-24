# POWER-DATA-01 — completion report

**Agent:** power-data-01 · **Branch:** `agent/power-data-01` (outcitedharness
`origin`) · **Date:** 2026-10-14 · **Inputs:** m4opencode findings at
outcited.com `15853d5` (`docs/audits/qa/2026-10-14/power-decision-08c/`).

## Task A — Vishay SiC46x admission preparation (Ask 1) — PACKET READY

Deliverable: `results/power-data-01/sic46x-admission-packet-v1.json`.

- Held doc verified by sha this session:
  `0cef2db3d1a1b022d13de380a3038cf9f3a2c5155bc12cbd44daae708eaba175`
  (29 pp, 1,205,324 B, CAS bytes present at `/Volumes/M5_4TB/vault/cas/0c/`).
- 4 base devices with per-part EC headers re-verified verbatim against the
  text layer (p14–p17): SiC461 10 A · SiC462 6 A · SiC463 4 A · SiC464 2 A
  (same quotes as the frozen `_device_ratings.json` rows on `curve/08c`).
- Orderable MPNs from p3 ORDERING INFORMATION (verbatim):
  `SiC461ED-T1-GE3`, `SiC462ED-T1-GE3`, `SiC463ED-T1-GE3`,
  `SiC464ED-T1-GE3` (PowerPAK® MLP55-27L) — proposed `mpns_served` payload
  for the binding write (doc currently `mpns_served = []`).
- Gate 1 spellings: vishay verbatim rows for recommended operating
  conditions (VIN 4.5–60 V, p4), abs max (VIN −0.3…66 V, TJ −40…+150 °C,
  p3), and per-part valley OCP limits (p5) — for the registry's per-vendor
  `source_fields` aliasing.
- Gate 2 grain: 4 base devices, one family datasheet, MPNs as
  `asserted_members`; family quote p6 ("…synchronous buck regulator family
  capable of delivering up to 10 A…").
- Gate 3 placement question and the binding-write ownership question sent
  to cr-core (mail `power-data-01-sic46x-admission-requirements-20261014`,
  response requested by 2026-10-16T23:00Z). **Admission NOT executed by me
  — that is CR authority.**

## Task B — TI efficiency-curve extraction (Ask 2) — ROWS DELIVERED

Deliverables:

- Pilot fixture golds (schema `harness.electronics-typical-curves-gold.v1`,
  consumable by `harness.discovery.curves.load_reference_curves`):
  `tests/fixtures/gold/curve_evidence_power_data_01/{tps54360_p35,lm5005_p25}.json`
- Bundle-v3-shaped rows + 1.5 A probe:
  `results/power-data-01/ti-efficiency-rows-v1.json`
- Human-review packets (300-DPI render + reference + checklist, unsigned):
  `results/power-data-01/curve-review/`

Extracted (deterministic vector geometry, zero model calls):

| row | doc sha | page | figure | series | conditions keys | η @1.5 A (interp) |
|---|---|---|---|---|---|---|
| `curve-a5fc772e…` | `c2743e51…` | 35 | Fig 45 "Efficiency vs Load Current" | `48Vin` | vout 5 V · vin 48 V · fsw 600 kHz (l_h not printed — never defaulted) | **81.52 %** |
| `curve-86b5d009…` | `7999d4c9…` | 25 | Fig 7-9 "Typical Efficiency vs Input Voltage and Output Current, 5V Output" | `VIN = 48 V` | vout 5 V · vin 48 V · fsw 300 kHz · L 33 µH | **77.88 %** |

Every condition key carries a verbatim page quote (TPS54360: plot-level
"VOUT = 5V, fsw = 600 kHz" + legend "48Vin"; LM5005: legend "VIN = 48 V",
p18 "VOUT = 5V"/"FSW = 300kHz", Table 7-1 LF "INDUCTOR, 33 µH…"). Both rows
ship `guarantee: false`, `evidence_class: typical`, `human_review_pending` —
bundle-v3 admission contract shape, zero engine changes needed.

Physics checks (numbers, not vibes): both parts use an external catch diode
(non-synchronous bucks), and every extracted page shows efficiency strictly
decreasing in VIN at matched load — 8/12/24/36/48/60 V orderings are
physically consistent on both documents (TPS54360 8 V peak 94.4 % → 60 V
79.6 %; LM5005 12 V 88.7 % → 75 V 74.4 %). Absolute levels sit far below
SiC463's 93.93 % — consistent with diode conduction loss, not an axis-map
error.

## Tooling (why the extractions needed two additive extensions)

Both pages were refused by `scripts/extract_vector_curve_references.py`
(`axis_fit_failed`, fail-closed). Root causes measured:

1. **Tick-label jitter**: TPS54360 rev G prints tick labels with ~1 pt
   hand-placement jitter → word-center fits at 0.46–0.61 % of span vs the
   strict 0.1 % bar. Fix: gridline-anchored axis fit — snap each already-
   paired tick word to its nearest vector gridline (exact geometry),
   least-squares on anchors, relaxed bar 0.5 % **only** on this anchored
   path, anchor + residuals recorded per plot (`_axis_anchor`,
   `_numeric_quality`). Anchored residuals on Fig 45/46: 0.05–0.15 % of
   span.
2. **Unstroked container frames**: LM5005 rev F draws a white background
   rect behind each figure row; `_frames` admitted it, the containment
   dedupe swallowed the true stroked plot frame, and every tick band
   missed. Fix: only STROKED rects are frame candidates.

Negative controls shipped (`tests/test_extract_vector_curve_references_anchors.py`,
10 tests): anchor-vote conflicts refuse, non-monotone values refuse, far
words refuse, the default 1e-3 bar is unchanged on the word-center path, and
the frozen pilot corpus re-extracts **byte-identically** (TPS548C26 p10: all
6 plots, 24 series, residuals unchanged). New fixtures live in their own
directory — the frozen pilot probe counts in `_probes.json` are untouched.

## Verification status

- 40/40 curve tests green (pilot gates + evidence gates + new controls).
- Frozen pilot gates: unchanged, green.
- Human sign-off: 0 (rows stay `human_review_pending` — the one human
  adjudication pass M4 named is still the step to "we'll put our name
  behind it").

## Blockers / open items

1. cr-core has not yet answered the packet-shape/placement/binding-write
   questions (due 2026-10-16T23:00Z). Packet is ready either way.
2. m5-opencode has not yet answered the duplicate-work question (due
   2026-10-15T23:00Z); no conflicting work was found in any inbox.
3. TPS54360 Fig 45 sixth series color unbound (legend swatch missing) and
   Fig 46 one color unbound — same known `legend_unresolved` class already
   documented in the pilot; the 48Vin target series IS bound. Not blocking.
4. Housekeeping (reported to mac-mini by mail): line 1 of
   `Electronicdataassets/plans/HANDOFF-m5-20261012.md` contains a stray
   credential-like string; removal belongs to its owner.

## Not done by design

No admission execution, no gold promotion, no release flip, no production
deployment, no writes to shared stores outside my branch + mailbox.
