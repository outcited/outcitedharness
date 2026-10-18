# Served-store coverage — the 2026-10-18 night fold

**Operator:** m5-opencode (m5/wave-orchestration) · **Front end:** dw-serve
`:8787` (ExportStore, export root `api-store-export/`) — picked up live, no
restart. **Board:** 15,036 → **24,464 served parts (+9,428, +63%)** — the
largest single movement since the board existed.

## What went live, with receipts

### 1. `parts_lane.jsonl` — 9,050 parts / 18,977 claims (deterministic-lane)

Source: the overnight deterministic lanes on this box + the MBP worker —
thermal (`deterministic_thermal_metrics_v1`), power-modes
(`deterministic_mode_currents_v1`), AEC grades (`deterministic_aec_grade_v1`)
— 27,700 rows, **100% sha→manifest join** (0 fallback slugs), verdict
`printed_row` only (holds never ship). One part per row, best claim per
field (typ > min/max), full chain per claim: verbatim quote + printed page
+ pdf sha + source url, tier T1_DATASHEET.

Category routing is recorded per row (`category_source`): 5,395 parts
matched staging aisles exactly; 3,655 got lane defaults (mode→power,
aec→mcu, thermal→unclassified) — never silently.

| category | parts | claims |
|---|---:|---:|
| mcu | 5,208 | 12,000+ |
| power/semiconductor | 816 | 1,100+ |
| refdesign | 187 | 244 |
| unclassified (thermal, unstaged) | 2,839 | 5,500+ |

### 2. `parts_a_pinout.jsonl` — 378 served OPNs / 8,281 claims (pinout-gold)

The IDENTITY-03 deterministic join (16 exact + 363 canonical-alias, no
fuzzy matching, no family fanout) applied to the verified pin batch
(210,193 staged rows; here: the 378 shas that canon to served OPNs).
Every pin claim carries its verified quote + doc sha. Admission tier rides
every row so completeness is never overstated: 54 COMPLETE/VERIFIED,
44 PARTIAL/VERIFIED, 281 UNCONFIRMED COMPLETENESS (pins present are
verified; absence of more is not).

Feed name sorts FIRST in the ExportStore walk (`a_` < `burn`) and its rows
UNION the claims earlier feeds carried for the same OPN (87/378 rows had
earlier claims; the rest of the intersected burn rows were empty husks) —
pin claims are reachable via `/api/extraction/v1/parts/{OPN}` without
shadowing anything. Verified live: `STM32WBA5MMG` → 126 claims incl. 76
verified pins.

### Cube (live at `GET /api/coverage/v1/cube`)

107 → **123 cells**: 68 `deterministic-lane` cells (9,050 parts), 16
`pinout-gold` cells (378 parts). Provenance lanes are never blended — the
aisle dimension keeps cr-core / burn / deterministic-lane / pinout-gold
distinct.

## Honesty ledger

- The 2,839 `unclassified` thermal parts are real extracted truth with no
  staging aisle yet — visible as their own cube category, not absorbed.
- Pinout tiers: only 54 of 378 are COMPLETE/VERIFIED. The feed says so on
  every row.
- No gold promotion happened here — this is the SERVED STORE fold (front
  end). Staging (the board's per-category percentages) is still owned by
  the mac-mini sync; tonight's rows land there on the next staging wave.
- Rollback: delete `parts_lane.jsonl` / `parts_a_pinout.jsonl`, regen cube
  (scripts committed), or restore per-feed sha256 from `fold_manifest.json`.

## The next unlocks (unchanged, now closer)

1. **Slug→OPN mapping at scale** — tonight's lane feed adds 9,050 slug
   parts; the identity-join machinery (proven at 378) is the template for
   the ~14K staged pinout unlock.
2. **Staging wave** — land tonight's bank into staging so the board's
   per-category facets (MCU thermal 0.3%, power_modes 0.0%, aec 6.7%)
   move too.
3. **Capacitor PDFs** (mac-mini delivery) → next production lane.

## Reproduce

```
python3 scripts/enrich_lane_rows.py --rows <lane.jsonl> --out <enriched.jsonl>
python3 scripts/fold_lane_feed.py --bank results/lane-bank-20261018 --root $EXPORT
python3 scripts/fold_pinout_feed.py --joins evidence/pin-batch-20261015/identity-joins.jsonl \
    --pins results/pin-tables-20261014/pin-rows-batch.jsonl --root $EXPORT
python3 scripts/extraction_board.py
```
