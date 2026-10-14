# PLAN — the docs we haven't seen yet (unseen-type preparedness)

date: 2026-10-12 · from: m5-opencode · status: planning only — extractors held off per Sam

**The mandate:** a lot of documents are waiting and more come from the PDF
factory — some we have never mapped. Hold the extractor fan-out; use this
window to plan for the unseen types before the flood.

## 1. The measured unknown pile (83,235 = 42% of the 198K L0 census)

By batch (the batch names carry truth the classifier missed):

| batch | unknowns | what the name says |
|---|---:|---|
| `datasheet-tail-20261007` | **60,834** | datasheets |
| `refdesigns-eval-20261007` | 10,625 | ref designs / eval bundles |
| `unassigned-l0-20261007` | 6,510 | unlabeled arrivals |
| `pcn-lifecycle-20261007` | 1,252 | PCN lifecycle |
| `incremental-*` | ~2,000 | mixed arrivals |
| named stragglers (e.g. `FDBL0065N40-D.pdf`) | ~200 | real datasheet filenames that kept their names |

By extension — **this is the unseen-type inventory**:

| ext | count | what it actually is |
|---|---:|---|
| .pdf | 79,360 | documents (most reclassifiable, see §2) |
| **.zip** | **1,879** | containers — ref-design bundles (PDFs+BOM+gerbers inside) |
| **.png/.gif/.jpg** | **1,698** | images, not documents — drawings, diagrams, page scans |
| **.txt** | 277 | sidecar notes / readmes |
| **.xlsx** | 6 | spreadsheets — tabular claims with no PDF |
| **.s2p/.s4p/.sp4/.lib/.sin/.bin** | **15** | EDA models — Touchstone s-params, SPICE — a claim grain nobody has |

## 2. The classification sweep comes first (cheapest, unlocks all routing)

73% of the unknown pile is `datasheet-tail`. `l0.py` already has the
`manifest_prior` path (`_PRIOR_CLASS_MAP`, 18 classes) — it fires only when
the caller passes a prior. The sweep: derive the prior from **batch name +
delivery manifest + extension**, feed it through the existing path, and
re-census. No new classifier needed for the bulk; the classifier stays
content-based where names lie.

Extension-level classes L0 lacks (add to the taxonomy, additive):
`container` (zip) · `image_only` (png/gif/jpg) · `sidecar_text` (txt) ·
`tabular_sheet` (xlsx) · `eda_model` (s2p/s4p/sp4/lib/sin/bin).
These never enter prose extraction — wrong tool, guaranteed garbage.

## 3. The type matrix (how each flows through the ladder)

The ladder law holds for every type: **L0 characterize → text → vision only
on flagged pages → frontier last.** What changes per type is the claim
grain and the payoff column.

| type | L0 class | claim grain | extraction lane | provenance | payoff |
|---|---|---|---|---|---|
| datasheet (single part) | datasheet | part → attribute | text tables (student/v41) | quote+row+col (+page/sha in catalog lane) | P1 compare + P2 knives |
| family/grid datasheet | datasheet | doc → part × attribute | grid reader (10/42 gold) | same | both |
| BOM | bom | doc → refdes → part | text tables | quote+cell | **P2 cross-provenance** (parts vouch) |
| errata | errata | doc → delta → base-doc-rev → part | text, delta extract | quote+page against base | P2 elimination (errata kills specs) |
| PCN | pcn | event → affected part → change | text, event extract | quote+date | P2 lifecycle elimination |
| reference design | reference_design | doc → circuit role → part | text + figures | quote+figure | P2 (BOM-in-context) |
| app note / manual | application_note / reference_manual | doc → claim (weaker rating class) | text | quote+page | P2 support evidence only (`typical` never guaranteed) |
| package drawing | package_drawing | part → dimension | vision-on-flagged (it's a drawing) | crop (C13 CropRef) | P1 fit checks |
| customer drawing / conformance | customer_drawing / conformance_report | part → cert claim | text | quote+page | trust/qualification |
| simulation model (s2p/sp4/lib) | eda_model | part → model param | **parse, not LLM** — typed parsers | file+line | P2 param evidence (new!) |
| spreadsheet | tabular_sheet | row → attribute | deterministic table read | sheet+cell | both |
| container (.zip) | container | **not claims** — unpacks to child docs | unzip → children re-enter L0 | parent-child lineage | both (lineage is the value) |
| image_only | image_only | none until classified | vision **at file level** (it IS the visual) | crop | case-by-case |
| sidecar_text | sidecar_text | metadata only | text, attach to sibling doc | file | provenance enrichment |
| brochure / white paper | brochure / white_paper | **NOT a claims source** | none | — | naming/discovery only; specs from marketing are forbidden |

## 4. New claim grains the current schema doesn't carry

1. **Delta claims (errata):** `doc → base-rev → part → attribute → new value`.
   The unit is a *change*, not a value. Elimination reads deltas as overrides.
2. **Event claims (PCN):** `event → part(s) → effective date → change`.
   A part with a pending discontinuation is a Path-2 recommendation negative.
3. **Position claims (BOM):** `doc → refdes → part` — exists (bom gold 7),
   needs the cross-provenance join at the same attribute grain.
4. **Model-param claims (EDA):** `part → param → value` with file+line
   provenance and a parser gate (never LLM over binary).
5. **Container lineage:** `container → child doc` with sha of both —
   provenance must survive unpacking.

## 5. What never changes (the laws that bind every type)

- L0 text-scan first; vision only on flagged pages; frontier last.
- Every claim resolves to verbatim quote + page (+sha) or the absence is named.
- Rating-class law: typical never guarantees; marketing never claims.
- Unknown types fail closed — an unrecognized shape yields nothing, not guesses.
- The normalization spine (L1–L4) is shared; only the grain adapter differs.

## 6. Sequencing (when extractors come back on)

1. **S1 classification sweep — DONE 2026-10-12.** `scripts/classify_sweep.py`
   + `_EXTENSION_CLASSES` in `l0.py` (7 tests, negatives included).
   **unknown 83,235 → 10,209 (88% resolved).** datasheet 45,738 → 103,944 ·
   reference_design 19,002 → 28,622 · pcn 1,010 → 2,262 · new classes:
   container 1,879 · image_only 1,698 · eda_model 8 · tabular_sheet 6 ·
   sidecar_text 277. Decided by: content 96,266 · manifest_prior 75,201 ·
   extension 3,868 · unresolved 10,209 (fail-closed residue: unassigned +
   incremental batches + `.bin` — the true never-seen pile). Output:
   `results/l0-reclass-20261012/` (originals untouched, `class_source`
   records every decision path).
2. **S2 container unpack + lineage — DONE 2026-10-12.**
   `scripts/unpack_containers.py` (7 tests: zip-slip absolute/`..`/drive,
   bomb caps, fail-closed classes). **1,879 top containers + 163 nested →
   2,042 processed → 24,519 children sha256'd** with parent lineage
   (`results/containers-20261012/lineage.jsonl`; children staged under
   `results/containers-20261012/children/` — M5_4TB is the read-only charley
   lane, transport law). Child classes: gerber 9,409 · unknown 7,609
   (extensionless, fail closed) · design_file 3,205 · sidecar_text 3,234 ·
   eda_model 363 · **document_pdf 303** (re-entry backlog) · container 163 ·
   image_only 128 · tabular_sheet 105. **22 quarantined whole** (fail
   closed): nested `.7z`/`.tgz` archives Python's zipfile can't read (ODB++
   fab bundles) — a follow-on handler type. **2 unreadable entries: mojibake
   Japanese filenames** (Shift-JIS mangling — the multilingual filename gap,
   real corpus evidence). Zip-slip guard: 0 hits in this corpus, tested
   against crafted attacks. Max depth 2, entry cap 2,000, 2 GiB/container.
3. **S3 datasheet-tail burn** — 60,834 docs through the proven lane
   (student first, cloud approved hop with L0 context).
4. **S4 errata + PCN grains** — schema work (delta/event), then their lanes.
   This is where Path 2 gets dangerous.
5. **S5 EDA parsers + tabular sheets** — deterministic parsers, gold-gated.
6. **S6 image_only triage** — vision at file level on the flagged set only.

Each stage ships with its negative control; nothing promotes without gold.
