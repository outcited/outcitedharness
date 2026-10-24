# CONTEXT-FIRST TEST — deep dive + A/B vision evidence — 2026-10-18

**PRD:** `designwins/docs/PRD-CONTEXT-FIRST-EXTRACTION.md` (5db97b70)
**Test:** TP4 vision (deepseek-v4.1-flash, free, temperature 0) against
**vector-extracted ground truth** (deterministic curve fixtures — the
reference digitizations themselves).

## 1. Deep dive — what the free layer already captures

| envelope field | captured today by | reaches vision? |
|---|---|---|
| **WHERE — plot/table bboxes** | `plot_regions_for_page` (vector frames + caption-seeded clusters), `regions.py` structural regions | as **crop pixels** (vision path) — the load-bearing field |
| doc class + TOC→lane map | `l0.py` (20 classes, `vision_candidate_pages`) | indirectly (lane selection) |
| word-column geometry + headers | `word_columns.py` | structural path only |
| pin/ball identity (pin count, physical IDs, JEDEC) | `locator.py`, `table_extractors.py` | structural path only |
| axis labels/units/ticks + scale | `extract_vector_curve_references` (word+frame fit) | **NOT** |
| legend/series names (swatch binding) | same | **NOT** |
| printed conditions verbatim (VIN/L/fsw rows) | same (`conditions_plot/page`) | **NOT** |
| caption + plot-type classification | `_CAPTION` regex + keywords | **NOT** |
| pre-digitized polylines (vector plots) | the whole vector pipeline | **NOT** |
| table row/col binding | `focused_page_context` (pin-projected tables) | structural path only |

**The gap:** the vision digitizer (`cloud_vision_direct.py`) receives the
crop + a bare prompt ("Return ONLY a JSON object..."). The envelope exists
in the structural path but never reaches vision. Free fields with no cost.

## 2. What else we can capture free (gap list, buildable from the same stack)

1. **Caption + plot-type** per figure (already regexed) — names the task.
2. **Legend inventory** (swatch↔word) — stops series-name guessing.
3. **Axis geometry** (ticks, ranges, log/linear) — anchors digitization.
4. **Printed conditions** (VIN/L/fsw rows) — disambiguates multi-series.
5. **Identity hints** (part, family, figure number↔datasheet figure).
6. **Cross-references** ("see Fig. 23", "Table 7-1") — binds figure↔claim.
7. **Pre-digitized polyline** where vector geometry exists — vision then
   only confirms (ultimate unfair advantage; free).
8. **Page role** (title / ordering / EC table / typical chars / package).
9. **Text-layer presence flag** (raster vs digital) — tells the lane
   whether vision is even needed.
10. **Neighboring text** (caption + surrounding sentences).

## 3. The A/B test (tight crops — WHERE already delivered as pixels)

| case (GT = vector fixture) | arm | y-span-norm RMSE | series names | pts | latency |
|---|---|---|---|---|---|
| TPS54360 p35 Fig45 (5 series) | A blind | 0.168 | 5/5 | 50 | 53s |
| | B envelope | 0.170 | 5/5 | 60 | 30s |
| LM5005 p25 Fig7-9 (6 series) | A blind | 0.063 | 6/6 | 144 | 79s |
| | B envelope | 0.085 | 6/6 | 102 | 59s |
| SiC46x p16 (1 series) | A blind | 0.026 | 1/1 | 9 | 12s |
| | B envelope | **0.019** | 1/1 | 9 | 5s |
| **weighted** | A blind | **0.087** | 12/12 | 203 | |
| | B envelope | **0.111** | 12/12 | 171 | |

**Honest read: with a tight crop, the text envelope did NOT improve
digitization accuracy** (mixed per-case; slightly worse weighted) — names
were 100% either way — **but it cut latency ~35%.** The crop itself is the
map; on clean vector-printed plots the model is already "just reading."

## 4. The decisive test — full pages (WHERE must be TOLD, not shown)

| case | arm | result |
|---|---|---|
| TPS54360 p35 | C full-page blind | **0 series returned** ("no series returned") |
| TPS54360 p35 | D full-page + envelope | **5/5 series matched, 25 points digitized** |
| LM5005 p25 | C full-page blind | **0 series returned** |
| LM5005 p25 | D full-page + envelope | 0 series (dense 6-series page; partial win only) |

**Blind full-page: 0/2 pages. With the map: 1/2 pages fully (5/5 series).
The map is the difference between nothing and working.**

## 5. Conclusions — the doctrine, refined by measurement

1. **WHERE is the load-bearing context.** Deliver it as pixels (crop) or
   as text (envelope); either rescues the task. Without either, vision
   fails outright on full pages.
2. **Text envelope's value = latency + disambiguation**, not raw accuracy,
   when the crop is already good. Its accuracy value should grow on raster
   scans, multi-figure pages, occluded legends, and identity-heavy tasks
   (pin/ball, table attribution) — untested here.
3. **Operational rule (added to the PRD):** every vision call must carry
   WHERE — as a crop OR as bbox+map. Text envelope is the fallback/richer
   form and is mandatory where a crop cannot be trusted (scan quality).
4. **Free wins stack:** vector digitization (no vision at all) > vision at
   crop > vision at full page + envelope >> vision at full page blind (0).

## 6. Reproduction

- `scripts/vision_envelope.py` — envelope builder (free, text/geometry)
- `scripts/vision_ab_test.py` — tight-crop A/B (results: `ab-results.json`)
- `scripts/vision_ab_fullpage.py` — full-page C/D (`ab-fullpage-results.json`)
- GT: `tests/fixtures/gold/curve_evidence_power_data_01/*` +
  `curve_evidence_pilot/vishay_sic46x_p16.json`
- Cost: $0 (TP4). 10 vision calls total.
