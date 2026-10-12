# DesignWins Rust Platform — Working Plan

**Revision 3 · final · supersedes all prior discussion**

---

## 0. North star

> **Understand what you are building. Constrain the cohort of parts you might choose.**

One funnel. Everything else is in service of it:

```
WHAT ARE YOU BUILDING?  ->  typed constraints  ->  COHORT
   (text · image · BOM · "like an X but…")
                         ->  compare @ your point  ->  explain  ->  next question
```

**Acceptance test for the entire rewrite** (Week 5 gate):

> A human types one sentence about what they are building. The system
> returns a cohort, shows which constraints were derived and confirmed,
> names every exclusion with its evidence, lists what it could not
> evaluate, and suggests the next question that would narrow it most.
> Every number opens to the page it came from.

If that passes, everything else was correct. If it fails, nothing else mattered.

---

## 0.1 The extraction board — the marker of forward movement
*(merged 2026-10-12: the migration is done when this is true)*

**The board is the plan.** Totals do not show progress; movement does. The
extraction board is the scoreboard — per category, on a part basis, what
fraction of addressable parts carry each facet we can sell the category
with, and what moved since the last snapshot.

> **GOLD (per category)** is the bar of minimum extracted **and
> provenanced** content needed to sell that category above-OPN. Each
> category judges it slightly differently — MCU is sold on family/series
> documents (one die, many parts: `FamilyDoc | RefManual | OrderingTable`),
> POWER on ratings + thermal + curves, CONNECTOR on the mechanical and
> electrical interface.

**Scoreboard** (`python3 scripts/extraction_board.py` →
`results/api-coverage/BOARD.md`, snapshot per run, Δ every run):

| category | addressable parts | the bar to sell it | where it stands (2026-10-12) |
|---|---:|---|---|
| **MCU** | 29,269 | overview · pinout · pillars · parametrics · power_modes | pillars 58.5% · pinout 9.8% · overview 7.4% · parametrics 3.9% |
| **POWER** | 36,636 | pillars · parametrics · thermal · curves | thermal 2.9% · curves 0.8% · rest 0% |
| **CONNECTOR** | 401 | connectors · parametrics · pinout | connectors 100% · rest 0% |
| **REFDESIGN** | 5,239 | (intake lane — feeds the others) | stubs only |

**Lane order:** MCU leads (already ahead), then POWER, CONNECTOR, and the
other active/passive categories. The PDF factory (mac-mini) feeds the
extraction lanes (this box); the API serves what they produce; the front end
(m4) renders it and reports gaps — never workarounds.

**Done means:** every category reaches its gold bar here, the Δ column is
moving, and every served number opens to its page (the provenance half of
"extracted and provenanced"). Until then the migration is not done —
regardless of how green the Rust gates are.

---

## 1. Ground truth — what actually exists

| Fact | Number |
|---|---|
| Product A (datasheet factory) | ~28,000 LOC |
| Product B (coding/training harness) | ~27,000 LOC — **out of scope** |
| Deterministic port surface | ~11,000 LOC (the money) |
| PDFs under management | 65,633 (125K+ corpus, 135K jobs) |
| Ingest rate | 770 docs/hr (tapes team, external Rust) |
| Substrate done | 81,473 jobs |
| Quote-native claims | 87K persisted |
| Catalog | 1,091 parts · 58,411 claims · 47,707 consensus · 169 conflicts |
| Discovery verified over | 1,881 parts · 86,736 claims |
| Tests | 157 files · ~1,071 test functions |
| Gold fixtures | 6.6 MB, 32 files — **the parity oracle already exists** |
| Scripts | 304 |
| Docs | 22 files · 4,472 lines -> collapse to 5 |
| `results/` | 10,699 entries (evidence volume) |
| Rust toolchain | **NOT INSTALLED — Day 0 prerequisite** |

**Key finding:** the funnel is already built. `harness/discovery/` is documented as the
*Intent-to-Knife laboratory: designs -> requirements -> discover -> ledger -> next-question*,
verified over 1,881 parts / 86,736 claims. It was planned as "one crate among many."
It is the product.

---

## 2. Product boundary

**Product A (this program):** `electronics/`, `pipeline/`, `discovery/`, `catalog/`,
`extraction_api.py`, `gci/` (search side), datasheet-relevant `training/`,
`evaluation/`, the 64 extraction/burn/vision/catalog/curve scripts.

**Product B (leaves the repo):** `gateway/` (1,720-line foreman/orch), `dispatch.py`,
`shadow/`, `greenfield/`, `sandbox/`, `task/`, `serial/`, `cases/`, `cli.py`,
`orch_loop.py`, `checkpoints.py`, `context_compiler.py`, `optimize.py`,
`economics.py`, `report.py`, `rescue.py`, `tournament.py`, `repo_contract.py`,
`training/code_curriculum.py`, `training/categoryrank.py`.

**Training stays** — it is the learning loop (escalation -> verified pairs -> local
capability), not Product B. Rust owns the loop; Python owns the gradient step.

---

## 3. The workspace — 17 crates, 3 binaries

```
designwins/
  Cargo.toml
  crates/
    dw-types/       THE LAW as types (Claim, Verbatim, SourceRef, Admitted,
                    ReleaseId, Observed<T>, Unit/Quantity, WeightFingerprint)
    dw-pdf/         parse · render · geometry (BSD-3 engine — see §11)
    dw-structure/   STAGE 1: class · page map · labels · regions · census
    dw-recon/       taxonomy induction · grain · TOC surface · OPN expanse
                    · vendor priors · denominators · gap ledger
    dw-knives/      axis registry (discovered-then-curated) · phrase layer
                    · facets · curve knives
    dw-extract/     STAGE 2: planner · tables · features · bom · jobs
    dw-truth/       THE GATE: quote · units · conditions · rating · admission
                    · ladder · ocr-close-loop · ball
    dw-normalize/   canonical units · condition typing · qualifiers · axes
    dw-graph/       category graph · family -> series -> OPN · topology
                    · revisions · grades
    dw-search/      BUILT not ported: FTS + sections · embed · vector
                    · image->semantic · category-neighborhood
    dw-decide/      THE FUNNEL: Build · Requirement · Atom · Cohort · Verdict
                    · Question · curves @ operating point · elimination
    dw-coverage/    the coverage cube · rollup · gap ledger · curve availability
    dw-release/     immutable releases · manifests · holdout · rollback
    dw-queue/       jobs: claim/ack · leases · bounded reclaim · dead-letter
    dw-model/       the ONLY crate that may call a model
    dw-learn/       learning loop: pair materialization · experiment metrics
                    · promotion gate vs frozen holdout
  bins/
    dw-worker/      ingest -> learn -> extract -> verify   (--role=…)
    dw-api/         one front door
    dw-controller/  publication · scheduling · self-improvement
  tests/
    parity/  gold/  contract/  holdout/  compile-fail/
  docs/
    ARCHITECTURE.md  CONTRACTS.md  RUNBOOK.md  MIGRATION.md  DECISIONS.md
```

**Three binaries, not eight.** Same binary on every box. Machines differ by `--role`
and capacity, never by which rules they enforce.

---

## 4. CONTRACTS.md — opens with `Build`, not with schemas

```
struct Build {                       // the top of the funnel
    id, release: ReleaseId,
    intent:   DesignIntent,          // text · image · block diagram · BOM · refs
    context:  DesignContext,         // industrial|automotive|consumer|medical
                                    // ta_range · isolation · rails · environment
    declared: Vec<Requirement>,      // typed, explicit
    derived:  Vec<Derived>,          // INFERRED — shown + confirmable, never silent
    constraints: Vec<Atom>,          // hard/soft, typed, with required conditions
    cohort:   Cohort,                // DISTINCT PARTS, not passage matches
    explain:  Vec<Verdict>,          // PASS|NEAR_MISS|FAIL|UNKNOWN + evidence
    unknowns: Vec<MissingAxis>,      // first-class: what we could not evaluate
    next:     Vec<Question>,         // ranked by expected cohort reduction
}
```

Three laws: `derived` is never silent · `cohort` is distinct parts (a type) ·
`unknowns` is a first-class field.

| # | Contract | Rule |
|---|---|---|
| C1 | **Vault (read-only)** | `/Volumes/M5_4TB/vault/{landing,cas}` · join on `sha256` · tapes team owns crawl/hash/store + arrival feed |
| C2 | **Substrate JSON** | `{sha256, filename, page_count, text_chars, text_quality, needs_ocr, producer, extractor, extractor_version, page_labels, pages[{page,label,text}]}` |
| C3 | **Claim** | `{value, quote, table}` + `{sha256, page, row_header, column_header}` + `condition_verbatim` + `conditions_typed` |
| C4 | **`catalog.db`** | `parts, part_sources, claims_canonical, consensus, doc_revisions` — **format stays**, Rust becomes writer at cutover |
| C5 | **`pipeline.db`** | `jobs, results, adjudication_ledger, budget_spend` — **format stays** |
| C6 | **Vocabularies** | `condition-model.v1` keys · `curve_type` (frozen, additive-only) · `atom_class: curve\|thermal\|errata` · rating `absmax\|recommended\|rated_limit\|typical\|transient\|surge\|unknown` |
| C7 | **API** | `GET /healthz` · `GET /api/extraction/v1/parts/{part_number}` · discovery `designs/…` · pipeline `jobs/…` · `POST /api/search/v1/query` · `GET /api/coverage/v1/cube` · auth `Bearer crk_…` · errors `application/problem+json` · `part_number` = 1:1 facade join · every response carries `ReleaseId` |
| C8 | **Training pairs** | quote-native `{value, quote, table, condition, sha256, page}` — v1 (2,713) and v2 (87K) shapes reproducible byte-compatible |
| C9 | **Elimination law** | `Unknown` never eliminates · `AbsMax` never satisfies an operating atom · margin never silent · no interpolation outside established domains · typical never becomes guaranteed · condition must **COVER** the point · cohort counts distinct candidates |
| C10 | **Embed law (C6 ruling)** | one embedder fleet-wide · `WeightFingerprint`-pinned vectors · ≤4 concurrent · never mix generations · a second embedder is unrepresentable |
| C11 | **Absence law** | `Observed<T> = Present(T) \| NotObserved \| NotApplicable` — `Option` banned for facts |
| C12 | **Conflict law** | `Conflict` is sticky; no promotion path to `Validated` |
| C13 | **Provenance law** | evidence snapshot = page render (content-addressed, 144 DPI) + row-band crop + `crop_sha256` (tamper-evident, leak-free) |

---

## 5. Crate-by-crate replacement map

### 5.1 `dw-truth` / `dw-normalize` — the gate
`quote_verify` (308) · `condition_model` (400) · `rating_class` (195) · `curve_atoms` (220) ·
`admission` (437) · `ladder` (63) · `crop_verify` (89) · `ball_validate` (109) ·
`mode_currents` (322) · `thermal_metrics` · `errata_deltas` (429)

### 5.2 `dw-decide` — the funnel
`discovery/evaluator` (345) · `discovery/service` (278) · `discovery/curves` (253) ·
`discovery/store` (141) · `curve_evidence` (1,118) · `typical_curves` (652)
Already verified over 1,881 parts / 86,736 claims. Port, do not redesign.

### 5.3 `dw-recon` — taxonomy induction
`family_census` (730) · `family_document` (1,408) · `family_device_matrix` (1,386) ·
`document_scope` (270) · `page_index` (324) · `toc_census.py` (becomes continuous) ·
`corpus` (734) · `incremental_corpus` (314)
**New:** category clustering · axis induction · OPN expanse · vendor priors · gap ledger

### 5.4 `dw-knives` — discovered-then-curated axes
`evaluator::AXES` (5 axes) · `power_axes_v2` (249) · `rating_class` · `curve_atoms` ·
`pillar_facts` (243, promoted from untracked) · `key_features_grid` (1,171, `below_grid` -> knives) ·
`aec_grades` · `connector_parametrics`

### 5.5 `dw-pdf` / `dw-structure`
`word_columns` (462) · `regions` (618) · `locator` (576) · `page_evidence` (264) ·
`l0` (~200) · `table_extractors` (616) · `census` (86) · `pillar_map` (110)

### 5.6 `dw-graph`
`ordering_codes` · `power_topology` (289) · `family_*` (graph portion) ·
`errata_deltas` (revision side)

### 5.7 `dw-search` — BUILT, not ported
`harness/search/` is **empty** (`__pycache__` only). Absorbs `embeddings` (229) + GCI client.
FTS + sections first; embed + vector when measurable; image->semantic; category-neighborhood.

### 5.8 `dw-coverage`
`coverage.py` (19-pillar ontology + 8-state machine + `build_coverage_manifest`) +
**new rollup cube** + **new gap ledger** + `coverage_report.py`

### 5.9 `dw-extract` / `dw-queue` / `dw-release`
`extraction` (442) · `key_features_grid` · `bom_extract` (193) ·
`pipeline/store` (214) · `catalog/store` (236) · `holdout` (286) · `ground_truth`

### 5.10 `dw-model` / `dw-learn`
`local_model` (631) · `cloud_vision` (191) · `frontier_batch` (1,223) ·
`providers/*` (364) · **new** pair materialization + promotion gate

### 5.11 `dw-api` / `dw-controller`
`extraction_api` (424) · `discovery/api` (117) · `pipeline/api` (210) + new search + coverage
routes · `factory_control` (1,566) + `burn_loop.sh` + `sentinel` + `soak` +
`catalog_fill` + the self-improving loop

---

## 6. Recon — how categories are learned

**Category is not an enum. It is a learned, evidence-backed graph.**

```
struct CategoryNode {
    id, name, aliases,
    parent:    Option<CategoryId>,     // Power > PMIC > Multi-rail
    contains:  Vec<CategoryId>,        // PMIC ⊃ {LDO, Buck, Boost, Sequencer}
    self_class: Vec<Vocab>,            // "buck converter", "PMIC", "LDO"
    toc_signature: Vec<SectionClass>,
    axes:      Vec<KnifeId>,           // INDUCED from device tables + EC symbols
    curves:    Vec<CurveType>,
    opn_expanse: ExpanseMethod,        // FamilyDoc | RefManual | OrderingTable | Unknown
    evidence:  Vec<DocRef>,
}
```

A part is multi-category. **A PMIC is a buck AND an LDO AND a sequencer.**

### Six induction signals

| Signal | Source | Teaches |
|---|---|---|
| Self-classification | `l0.py` | "this is a buck converter" on the cover |
| TOC signature | `page_index`, `toc_census` | category = a heading distribution |
| **Device-table attributes** | `family_census.device_tables` | **the attribute rows a comparison table addresses ARE the category's axes** |
| EC-table symbols | `power_axes_v2`, `mode_currents`, `rating_class` | printed symbol vocabulary = the axis set |
| Curve fingerprint | `curve_type` vocab | efficiency_vs_load => power; transfer_vgs_id => MOSFET; bode => converter |
| OPN decoder shape | `ordering_codes` | vendor x category ordering grammar |

Clustering on these six gives the categories. A human names the cluster; membership,
axes, and coverage are discovered. Every new document joins a cluster or **creates a gap**.

### OPN expanse — the coverage multiplier
One reference manual can stand in for 47 OPN datasheets' worth of expanse:
`family doc -> series tokens` · `ref manual -> device-compatibility + ordering +
per-variant memory maps` · `family_device_matrix -> series x attribute`.
Every expanse claim cites ref-manual pages. Where `opn_expanse == Unknown`, say so.

### Vendor priors (decision)
**Mechanism v1, population incremental.** `VendorPrior` type ships day one — always
overridable, printed evidence always wins. Populate only TI, ST, NXP, Renesas, Vishay.
Auto-induction = v2.

---

## 7. Coverage as a product (`dw-coverage`)

```
GET /api/coverage/v1/cube?category=power&aisle=buck&vendor=TI&release=<id>
-> {
    release_id, slice: {category, aisle, vendor},
    documents: {held, above_opn, single_opn, other},
    denominator: {parts, attributes_addressed},
    pillars: { P01..P19: {
        name, weight, state_counts: {not_applicable, expected_not_found,
          located, extracted, normalized, provenanced, validated, conflict},
        laned: bool,            // honest: some pillars have NO lane
        lane, conflates, target_pages, extracted_pages, validated_items
    }},
    curves: {vector, raster, none, unknown},
    knives: {axes_known, axes_expected, coverage_pct},
    top_gaps: [{kind: acquire|extract|lane|axis, what, why, impact_weight}]
  }

GET /api/coverage/v1/gaps?category=power&limit=20
```

**The trust equation:**

```
complete corpus  x  honest knowledge of gaps  =  a cohort you can defend
 (tapes+extract)     (coverage + Unknown-never-eliminates)
```

---

## 8. Curves — who has what (measured, now queryable)

| Manufacturer | Form | Status |
|---|---|---|
| TI | vector | extractable, precision **1.000** |
| Vishay (SiC46x) | vector | extractable, precision **1.000** |
| ADI (`ADP5003`, `LT8292`) | vector (LT8292 = first log-axis ref) | **not held -> acquisition request** |
| Microchip (14 docs) | raster | `no_vector_curves` -> vision lane |
| Murata (12) | raster / vector-text ticks | `axis_fit_failed` |
| onsemi, Infineon iPOL, Wurth | raster / vector-text ticks | `axis_fit_failed` |

Pilot: 13 pages / 40 plots / 81 series / 11 families · tick-fit residual <= **0.014% span** ·
decision probes **16/16** (false-comparability **0.00**, out-of-range rejection **1.00**) ·
**human signoff = 0** (machine refs are not gold).

**Demands:** `curve_availability` is a coverage axis · gap list drives acquisition
(`LT8292` -> tapes team) · **a vector-text tick reader is a real work item**
(unlocks Murata + Wurth) · human signoff stays a gate.

---

## 9. The learning loop — first-class

```
extraction (deterministic -> student -> vision -> canon -> frontier)
  |- verified claim       -> training pair (quote-native)
  |- failure/disagreement -> ledger + REASON CLASS
  v
dw-controller measures: unresolved claims · weak locators · ambiguous figures · missing axes
  v
targeted experiments vs LOCAL models (budgeted)  ->  same dw-truth gate
  v
verified -> pairs -> LoRA student (local boxes) -> GATE vs frozen holdout
  v
student becomes a cheaper tier in the ladder
  v
CAUGHT CLASSES GRADUATE TO DETERMINISTIC VALIDATORS (free, permanent)
  v
training data purged of caught classes each generation
```

Two laws: a caught class never becomes model-judgment again · no frontier answer
becomes a training pair without passing `dw-truth`. Rust owns the loop; Python owns
the gradient step. `dw-learn` must reproduce v1 (2,713) and v2 (87K) pair shapes
byte-compat and reproduce existing gate verdicts.

---

## 10. Rust best practice — CI from day one

| Area | Rule |
|---|---|
| Lint | `clippy::all -D warnings` · `cargo fmt --check` · `cargo-deny` |
| Licenses | `cargo-deny` rejects AGPL mechanically |
| Errors | `thiserror` in crates, `anyhow` in bins · `unwrap`/`expect` forbidden in libraries |
| Panics | `panic="abort"` release · no panic in request path |
| Determinism | `BTreeMap`/`IndexMap` for all serialized output · HashMap order must never reach JSON |
| Floats | `f64` · no `==` (`clippy::float_cmp`) · tolerance explicit and stored |
| Serde | `deny_unknown_fields` on every input · unknown field = hard error |
| Versioning | `schema_version` on every artifact · unknown major = reject |
| Time | UTC only, `time` crate · monotonic for leases |
| Provenance | `Claim` unconstructible without `Verbatim`+`SourceRef` — compile-fail test |
| State | `Conflict -> Validated` impossible — compile-fail test |
| Absence | `Observed<T>` — `Option` banned for facts |
| Unsafe | `forbid(unsafe_code)` everywhere except the FFI crate (`deny` + reviewed) |
| Tests | gold-byte exact · property tests (unit conversion, condition algebra) · compile-fail · parity harness as release gate |
| Storage | SQLite local only (WAL), never on the NAS share · replicate out |

Pain points 6-10 (RUNBOOK) are one class — **silent wrongness**. Make it structurally
impossible: typed `ProbeResult` (never `[]`-means-ok) · `WeightFingerprint` on every
service identity · alerts-required-by-construction · container logs never the truth.

---

## 11. PDF engine — corrected call

Pre-revenue + internal use = **no AGPL problem today.** AGPL triggers on distribution
or network service to third parties, not revenue. PyMuPDF keeps working.

**The call:**
1. 3-day spike on PDFium (BSD-3, Chrome's renderer, `pdfium-render`).
2. Matches on adversarial pages (rotated ST tables, two-column splines) -> use it.
3. Does not match -> keep PyMuPDF, ship the factory. Settle with Artifex at revenue.
4. Fallback: run the PDF engine **out-of-process over a pipe** — never linked.

Not a P0. A spike note.

---

## 12. Schedule

| Week | Work | Gate |
|---|---|---|
| **0** (3d) | `rustup install` · `dw-types` · PDFium spike -> engine verdict · parity harness · corpus freeze · `CONTRACTS.md` | `cargo-deny` green · PDF verdict recorded · frozen corpus sha-pinned |
| **1** | `dw-truth` · `dw-normalize` · `dw-knives` core | condition 167/167 · `rating_class_v1` · 5 power axes port 1:1 · compile-fail: `Claim` unconstructible · compile-fail: `Conflict` sticky · 87K claim diff = 0 unexplained |
| **2** | `dw-pdf` · `dw-structure` · `dw-recon` (grain + TOC surface + denominators + category induction v1) | page index 4,484 docs = 0 errors · pin gold 15/16 · `family_census` gold · toc_unlaned reproducible · **category clusters byte-stable** |
| **3** | `dw-extract` · `dw-graph` · `dw-queue` · `dw-model` | table/feature/BOM/OPN/topology gold · queue 6 tests (cap, sweep, FIFO) · prompts byte-identical |
| **4** | `dw-decide` (Build funnel) · `dw-knives` phrase layer · `dw-coverage` cube · `dw-release` · OPN expanse from ref manuals | curve pilot 1.0 / 0.0 / 1.0 · elimination law · **coverage cube answers category x aisle x vendor** · **expanse claims cite ref-manual pages** · knives coverage % reported |
| **5** | `dw-search` (BUILD) · `dw-learn` · `dw-api` · `dw-controller` | **end-to-end acceptance test (§0)** · search->cohort->eliminate · embed fingerprint enforced · pair format byte-compat · gate reproduces existing precision/recall · controller unattended |
| **6** | `dw-knives` extensions (MCU + connector axes) · vector-text tick reader · shadow -> cutover -> delete -> measure | MCU+connector axes with gold · full discrepancy ledger clean · cutover · measure LOC/throughput/latency/memory/failures/provenance |

**6-7 weeks.** Python produces throughout as the reference. Every boundary leaves a
working system. Nothing big-bang. No speedup claims before numbers.

---

## 13. Docs — 5, not 22

| Doc | Content | Absorbs |
|---|---|---|
| `ARCHITECTURE.md` | north star funnel · 4 stages · learning loop · taxonomy induction · workspace · law-as-types | `ARCHITECTURE.md`, `SYSTEM_SUMMARY.md`, `DATASHEET_FACTORY.md`, `LEARNING_FACTORY.md` |
| `CONTRACTS.md` | `Build` first, then C1-C13 verbatim | scattered docstrings |
| `RUNBOOK.md` | fleet · endpoints · failure modes · recovery | `RUNBOOK.md`, `NETWORK_FABRIC.md`, `MODEL_SETTINGS.md`, `QUEUE_RECOVERY.md` |
| `MIGRATION.md` | this plan · parity process · cutover/rollback | `TRUTH_PLAN.md`, `CONTEXT_HANDOFF.md` |
| `DECISIONS.md` | ratified laws, never re-litigate | `QUALIFICATION.md`, `EVIDENCE_SNAPSHOTS.md`, `CURVE_EVIDENCE.md`, `CURVE_DISCOVERY_EXPERIMENTS.md`, `DATASHEET_EXTRACTION_SERVICE.md` |

Archive: all 9 `HANDOFF-*.md` -> `docs/archive/`. Each crate gets `//!` naming the
Python file it replaces and the gold file that gates it.

---

## 14. Deletion list (at cutover)

`burn_loop.sh` · `burn_aisle.py` · `extract_dispatch.py` · `extract_worker.py` ·
`vision_worker*.py` · `catalog_fill.py` · `sentinel.py` · `soak_sample.py` ·
`toc_census.py` · `harness/pipeline/*` · `harness/electronics/*` ·
`harness/discovery/*` · `harness/catalog/*` · `harness/extraction_api.py` ·
`harness/search/` · and every script not touching training.

**Stays Python:** `training/` (queue, ledger, exporters, promotion, worker, adapters),
`evaluation/`, gold-fixture generators.

---

## 15. QA findings — status

| # | Sev | Finding | Status |
|---|---|---|---|
| F-01 | downgraded | AGPL on PDF engine | Spike PDFium; PyMuPDF stands if it fails. Not a blocker |
| F-02 | P0 | `harness/search/` empty | Week 5 builds it. +1 week is real work |
| F-03 | P0 | 5 axes for all of electronics | `dw-knives` + induction. Axes discovered from device tables, then gold-gated |
| F-04 | P0 | Coverage can't roll up | `dw-coverage` cube answers category x aisle x vendor x pillar x state |
| F-05 | P1 | 5 of 19 pillars laned | `laned: bool` exposed. `toc_census` becomes continuous |
| F-06 | P1 | Curve availability not queryable | Coverage axis + drives acquisition |
| F-07 | P1 | Recon scattered | `dw-recon` crate |
| F-08 | P1 | "Discovery" is 3 things | Rename: `dw-graph` · `dw-decide` · `dw-search`. Never "discovery" |
| F-09 | P2 | `pillar_facts` untracked | Promoted to `dw-knives::facets`, gold-gated |
| F-10 | P2 | Determinism traps | CI gates §10 |
| F-11 | P2 | Silent-wrongness class | Typed probes, fingerprinted identity, alerts-by-construction |
| F-12 | P2 | Conflict/absence laws in comments | `Observed<T>` + `Conflict` types, compile-fail tests |

---

## 16. Non-negotiables (enforced by types, not tests)

1. One implementation of each engineering rule -> crate boundaries
2. No duplicate truth across machines -> one `ReleaseId` per answer
3. No unverified promotion -> `Admitted` newtype
4. No lost lineage -> append-only releases
5. No unsupported exclusion -> every `Ineligible` carries reason + evidence ref
6. Every publish immutable, reproducible, reversible
7. Rust owns plumbing; Python only where model/training deps force it
8. SQLite never on the NAS — local transactional DB per box
9. `derived` constraints are never silent
10. `Unknown` never eliminates
11. Absence is a fact (`NotObserved` != `NotApplicable` != `None`)

---

## 17. Day 1 — execution order

0. `rustup install stable + clippy + rustfmt + cargo-deny`   (unblocks everything)
1. `git`: branch clean off `main` (34 uncommitted changes currently on `curve/07e-evidence-supply`)
2. `CONTRACTS.md`          (Build first, then C1-C13)
3. `crates/dw-types/src/lib.rs`  (law-as-types + compile-fail tests)
4. PDFium spike kickoff  (3-day adversarial set)
5. parity harness skeleton
6. corpus freeze script
7. `MIGRATION.md` (this document)

**Week 0 gate:** the ledger runs empty against Python's `quote_verify` on the frozen set.

---

## 18. The three things to defend hardest in review

1. **`Build` is the top-level contract** — every crate's job statement is written in
   terms of serving the funnel, or it does not belong in the workspace.
2. **Categories are induced, not enumerated** — six signals, evidence-backed graph,
   axes born from device tables. No hardcoded aisle list survives contact with 135K PDFs.
3. **Coverage is what makes the cohort defensible** — complete corpus x honest gaps =
   an answer an engineer will stake a design on. That is the product.

---

## 19. Corrections — 2026-10-10, dated, not edited into the body

The body above is Revision 3 and is kept as written. It is now partly wrong.
Corrections are recorded here so the history stays readable and the wrong
parts stay visible. Where a `designwins/docs/DECISIONS.md` entry supersedes
this plan, **the decision wins**.

| § | what Revision 3 says | measured reality | superseded by |
|---|---|---|---|
| 5.7 | `dw-search` is "FTS + sections first; embed + vector when measurable" | wrong. The funnel's front door is the **colloquial → knife binding**, not text retrieval. Engineers type *"outdoor weather thermostat bluetooth"*, not `has_wireless_ble == true`. FTS is the residue path. | **D4** |
| 11 | PDF spike passes if it "matches on adversarial pages" | not reproducible, no threshold. Measured: PDFium is viable (page geometry exact, glyph centres within 0.5 pt, path segments present). The real bar is glyph **centres** ≤ 2px @ 144 DPI and word **multiset** identity — edge-for-edge bbox would fail both engines, because MuPDF reports the font em-box and PDFium the ink extent. | **D1** |
| 6 | vendor priors: "populate TI, ST, NXP, Renesas, Vishay" | a hand-authored prior is an invented fact. Priors are a **projection of `dw-coverage`** and may rank questions or forecast gaps; they may never emit a Claim, satisfy an Atom, or eliminate. | **D6** |
| 12 | Week 1 gate: "87K claim diff = 0 unexplained" | **blocked.** The corpus is not locatable. Candidates are `exports/power-datasheet-pairs/*/pairs.jsonl` (9,201 rows) and `results/lora-v1-pairs/`; neither matches "2,713 v1 / 87K v2". | §7 of `PROVENANCE.md` |
| 13 | docs collapse to 5 | 5 + `HANDOFF.md` + `PRD-NORMALIZE.md`. Both earned their place: one is the context-reset survival doc, the other is a spec that caught three factual errors in its own review. | — |
| 4 | C7 is a REST surface | it is a **rendering substrate**. Every response carries, inline: `ReleaseId`, evidence refs resolving without a second call, `unknowns`, per-exclusion reason + evidence, ranked `next`. | **D7** |
| 0 | the acceptance test is a paragraph | it is a **test**: `dw-funnel::accept`, asserted clause by clause. | `dw-funnel` |
| — | (absent) | a third gate exists and our use case depends on it: **trust**. Every number must resolve to a content-addressed page render and row-band crop; unresolvable ⇒ `Unknown`. | `PROVENANCE.md` §9 |

### What the plan did not anticipate

- **`PLAN.md` §1 ground truth is a snapshot and drifted**: 32 gold files (12
  top-level entries), 35 uncommitted changes (not 34), PDFs 65,633. Treat §1 as
  a 2026-10-09 measurement, not live state.
- **The NAS unmounts.** `/Volumes/M5_4TB` vanished twice mid-session. Gates
  must be self-contained, which is why `tests/fixtures/gold/` carries its own
  PDFs and the corpus freeze is in-repo.
- **`harness/search/` being empty was not the only "build not port" item.**
  `quote_verify`, `ladder`, `crop_verify`, `ball_validate` live in
  `harness/pipeline/`, not `harness/electronics/` as §5.1 implies. Location
  assumptions in §5 need checking file by file.

### Still open at the end of Revision 3

See `designwins/docs/PROVENANCE.md` §7 and `designwins/docs/HANDOFF.md` §2.
The single highest-value item is evidence resolution: today a `SourceRef` is a
hash and a page number that nothing renders, which is a provenance field that
*looks* like accountability and is not.
