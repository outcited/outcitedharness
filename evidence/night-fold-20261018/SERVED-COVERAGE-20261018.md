# Served-store coverage — the 2026-10-18 night fold

**Operator:** m5-opencode (m5/wave-orchestration) · **Front end:** dw-serve
`:8787` (ExportStore, export root `api-store-export/`) — picked up live, no
restart. **Board: 15,036 → 38,232 served parts (+23,196, +154%)** in one
night — the largest single movement since the board existed.

## Live served cube (GET /api/coverage/v1/cube — 178 cells)

| aisle | parts |
|---|---:|
| pinout-staged (verified pin tables, slug grain) | 13,768 |
| burn (production power lane) | 9,201 |
| deterministic-lane (tonight's thermal/mode/aec) | 9,050 |
| cr-core | 5,835 |
| pinout-gold (verified pin tables on served OPNs) | 378 |

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

107 → **178 cells**. Provenance lanes are never blended — the aisle
dimension keeps cr-core / burn / deterministic-lane / pinout-gold /
pinout-staged distinct.

### 3. `parts_z_pinout_staged.jsonl` — 13,768 slug parts / 202,710 pin claims (pinout-staged)

The rest of the verified pin batch: docs whose cleaned stems are valid
OPNs the served catalog simply does not carry (intake backlog — 11,195 in
bucket 6a) or ambiguous/family-derived identities. Served at SLUG grain
(the same law as the lane feed) with `join_status: unresolved` + the
bucket cause on every row — never a fabricated OPN. 10,903 resolve
first-match in `/api/extraction/v1/parts/{slug}`; 2,865 are shadowed by
earlier feeds carrying the same slug (their pins remain cube-visible and
in the feed). The proper fix is merge-across-feeds in dw-api's
`ExportStore::part` — proposed for review, NOT hot-pushed to the live
binary tonight. Walk-last by name (`z_`) so the 92MB feed is a miss-only
tax: measured OPN lookup 0.02s, slug lookup 0.7s, full miss 1.7s.

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

## The next unlocks (updated)

1. **dw-api merge-across-feeds** — `ExportStore::part` is first-match;
   merging claims across feeds makes the 2,865 shadowed slugs' pins (and
   any future overlap) reachable. Small Rust change + restart — morning
   review item, not a live-binary push at 1am.
2. **Catalog intake for bucket 6a** — 11,195 valid OPNs absent from the
   served catalog; each intake wave converts pinout-staged slug rows into
   pinout-gold OPN rows via the proven identity-join machinery.
3. **Staging wave** — land tonight's bank into staging so the board's
   per-category facets (MCU thermal 0.3%, power_modes 0.0%, aec 6.7%)
   move too (mac-mini owns staging writes).
4. **Capacitor PDFs** (mac-mini delivery) → next production lane.

## Reproduce

```
python3 scripts/enrich_lane_rows.py --rows <lane.jsonl> --out <enriched.jsonl>
python3 scripts/fold_lane_feed.py --bank <enriched-bank-dir> --root $EXPORT
python3 scripts/fold_pinout_feed.py --joins evidence/pin-batch-20261015/identity-joins.jsonl \
    --pins results/pin-tables-20261014/pin-rows-batch.jsonl --root $EXPORT
python3 scripts/extraction_board.py
```

Rollback: `parts_lane.jsonl` / `parts_a_pinout.jsonl` / `parts_z_pinout_staged.jsonl`
are whole-file feeds — delete + regen cube; per-file sha256 in `fold_manifest.json`.

