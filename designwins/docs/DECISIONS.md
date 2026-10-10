# DECISIONS

**Ratified laws. Never re-litigate.**

Each entry states the ruling, the reason, the evidence it rests on, and what it
makes structurally impossible. Amending a decision requires a new numbered entry
that explicitly supersedes the old one — never an edit in place.

Ratified 2026-10-10 against `PLAN.md` Revision 3. These close the four gaps that
blocked unattended execution (§5.7 search, §11 PDF engine, §9 training boundary,
§12 cutover).

---

## D1 — PDF engine: PDFium, decided by measurement

> **Revision 2026-10-10 (same day, measured).** The bar below supersedes the
> bar drafted earlier today, which was not yet evidence-backed. The original
> bar demanded byte-identical text and edge-for-edge bbox agreement at 1px @
> 144 DPI. **Measured evidence shows that bar is wrong** — it would reject both
> engines for a difference with zero effect on extraction. Recorded here rather
> than edited away, so the error is visible.

**Ruling. `dw-pdf` is written against PDFium** (`pdfium-render`, BSD-3). PyMuPDF
is the Python reference throughout the port and is deleted at cutover. If PDFium
fails the path gate below, PyMuPDF continues **out-of-process over a pipe and is
never linked** — that containment line is not negotiable at revenue.

### The gate (corrected, measured)

Run the Python reference and PDFium over the frozen page set and require:

| # | Test | Bar | Rationale |
|---|---|---|---|
| G1 | Page geometry | exact (≤ 0.001 pt) | measured 0.00000 |
| G2 | Word token multiset | identical | vocabulary parity, order-independent |
| G3 | Glyph **centers** | ≤ **2 px @ 144 DPI (1.0 pt)** | placement fidelity — the thing that actually matters for `word_columns` / `regions` / `locator` |
| G4 | Word **width** | ≤ 2 px (1.0 pt) | column/band geometry |
| G5 | Word **height** | **excluded from the gate** | MuPDF reports font em-box, PDFium reports ink extent. A semantic difference, not a placement error |
| G6 | Body-text reading order | identical | figure-label z-order is excluded — see below |
| G7 | **Path segments** | reproduce gold curve points | **the §8 precision 1.000 gate.** This is the load-bearing test |

**G7 is decisive.** §8's precision 1.000 on vector curves depends on reading
path geometry, not on text. MuPDF `get_drawings()` returns structured segments
(`('l', Point, Point)`, `closePath`, `color`, `width`). `pypdfium2` exposes only
`get_bounds()` on paths — **no segment geometry**. That is a binding limit, not
an engine limit: PDFium has the data (Chrome renders these curves), and
`pdfium-render` exposes `PdfPageObjectPath::iter_segments()`. Because `dw-pdf`
is a Rust crate, G7 must be verified on the Rust binding against the gold curve
pages before any other `dw-pdf` work proceeds.

### Measured spike — LMR33610 page 8 (gold curve page)

`document_sha256 777b3966aff2772cd6680193c7ba7cd7a0b79778b418aafbcf7ce2c147e39611`

| test | result | verdict |
|---|---|---|
| G1 page geometry | 612.000 × 792.000 pt both · delta 0.00000 | **PASS** |
| G2 word tokens | 264 = 264 · multiset identical | **PASS** |
| G3 centers | X p50 0.060 pt · Y p50 0.478 pt · max 0.968 pt | **PASS** |
| G4 width | p50 0.310 pt · p90 0.567 pt | **PASS** |
| G5 height | p50 0.860 pt · max 4.050 pt | excluded — em-box vs ink |
| G6 reading order | one divergence: `UP DN` / `DN UP` on overlapping figure labels | body text clean |
| G7 path segments | not testable via `pypdfium2` (bounds only) | **see G7 spike below** |

Height disagreement is fully explained: MuPDF `7.8` box y-span 12.28 pt,
PDFium 8.16 pt, centers agree to 0.02 pt. The font em-box carries
ascender/descender padding the ink box does not.

### G7 spike — path segments on `pdfium-render` (2026-10-10)

**Verdict: PDFium is viable. Segment parity is not yet achieved.** The engine
question is answered; the porting question is not. `dw-pdf` remains gated until
parity is demonstrated, and the D1 fallback (PyMuPDF out-of-process, never
linked) stays available.

Corpus: `tests/fixtures/gold/typical_curves/pdfs/` — two in-repo MSP430
datasheets with matching gold curve pages. Self-contained, no vault dependency.
Reference fixture emitted by `scripts/emit_pdf_engine_reference.py` into
`tests/fixtures/gold/pdf_engine_segments_v1.json` (321 drawings / 1208
segments). Gate: `crates/dw-pdf/tests/g7_path_segments.rs`.

| test | result | verdict |
|---|---|---|
| G1 page geometry | 612.000 × 792.000 pt both pages · delta 0.00000 | **PASS** |
| Path segment API | `segments()` yields `MoveTo`/`LineTo`/`BezierTo`/`Unknown` + points | **PASS** — capability closed |
| Coordinate alignment | pdfium is y-up, MuPDF y-down. Verified: `792 − 677.6 = 114.4` | **PASS** |
| Drawing count, `msp430fr6005` p29 | **208 = 208** | **PASS** |
| Vertex parity, `msp430fr6005` p29 | 57.8% within 1 pt · y aligned (ref 29.1/50.9/743.6 vs eng 29.1/51.8/768.9) | **OPEN** |
| Vertex parity, `msp430g2744` p15 | 19.4% · y misaligned (ref median 36.6 vs eng 775.7) | **OPEN** |
| Decomposition | MuPDF `re`/`c` items vs PDFium expanded `Move`/`Line`/`BezierTo` | normalization required |

**Three findings, in order of importance:**

1. **The engine is not the problem.** Geometry is present and coordinates
   align exactly once y is flipped. G7's real question — *can PDFium feed curve
   extraction?* — is answered yes.
2. **`dw-pdf` needs a normalization layer**, not a different engine: `re` → 4
   corners, Bézier triples regrouped, y-flip applied in the engine so downstream
   is engine-portable (already implemented).
3. **One open transform case.** PDFium bakes a parent's transform into
   enumerated Form XObject children, so composing the form matrix
   double-applies it. Recursing with the *parent* matrix takes `msp430fr6005`
   p29 to exact drawing count (208 = 208). `msp430g2744` p15 still shows a
   y-distribution mismatch, so at least one more transform case is unhandled.

**Residual work to close G7 (bounded, roughly one day):** resolve the remaining
Form XObject transform case on `msp430g2744` p15, then bring vertex parity to
≥ 99% by normalizing decomposition. Until that lands, `dw-pdf` work is gated
per D1.

### Adversarial set (named, not "whatever looks hard")

rotated ST tables · two-column splines · `LT8292` log-axis · a Microchip
raster-only page · a Murata `axis_fit_failed` page. The last three come from the
§8 curve table, so the spike is gated on pages that already failed or were never
held.

### Engine rule

`dw-pdf` exposes a `PdfEngine` trait; the concrete engine is a **build-time**
choice, never a call-site choice.

**Why this decision.** License is the tiebreaker among engines that all clear
G1–G6. PDFium is BSD-3; MuPDF is AGPL. The plan's §11 reasoning (pre-revenue ⇒
no AGPL *problem today*) is true but time-boxed — it converts to a distribution
problem at revenue with no clean exit. Writing `dw-pdf` against a BSD-3 engine
removes that future entirely. §11's "settle with Artifex at revenue" fallback
becomes unnecessary.

**Makes impossible:** linking AGPL code; a geometry regression silently
corrupting `crop_sha256`; shipping an engine on a subjective call; starting
`dw-pdf` before the curve path gate is proven.

---

## D2 — Cutover agreement and rollback

**Ruling.** Cutover requires **zero unexplained diffs** on the frozen corpus.

Every Rust-vs-Python difference is classified in a discrepancy ledger as exactly
one of:

| class | blocks cutover |
|---|---|
| `rust_bug` | **yes** |
| `python_bug` | no (fix or document; Python is the reference until cutover) |
| `intentional_improvement` | no — but requires a signed entry here |
| `unexplained` | **yes** |

Cutover is permitted when `unexplained = 0` and `rust_bug = 0`.

**Rollback triggers (post-cutover, any one is sufficient):**

1. Any claim-level regression on the frozen corpus.
2. Any provenance failure — missing page render, or `crop_sha256` mismatch.
3. Throughput or p99 latency worse than **2x the Python baseline**.

**Performance is measured and reported, never a speedup gate.** §12 already
forbids speedup claims before numbers; making throughput a gate would contradict
that law. The 2x line is a harm floor, not a target.

**Why.** "Shadow → cutover → delete → measure" named no bar. A wrong datasheet
number kills a design, so statistical parity (99.9%) is the silent-wrongness
class F-11 names. The Week 1 gate already sets this bar for claims ("87K claim
diff = 0 unexplained"); cutover is the same bar applied to the whole surface.

**Makes impossible:** cutting over on a residual diff rate; an unattended
rollback with no trigger; a speedup claim without a number.

---

## D3 — Rust ↔ Python training boundary

**Ruling.** The gradient step is invoked by **subprocess with content-addressed
JSONL on local disk**, through the `HandlerSpec` rules the codebase already
enforces: absolute argv, no shell, environment allowlist, explicit timeout.

```
dw-learn  --writes-->  <local>/train_request.json  +  pairs/*.jsonl  (content-addressed)
          --spawns-->  python -m harness.training.worker  (HandlerSpec)
          --reads--->  train_result.json + metrics + student artifact
          --gates--->  promotion vs frozen holdout   (dw-truth, Rust)
```

Contract artifacts carry `schema_version`. Pair JSONL reproduces v1 (2,713) and
v2 (87K) shapes byte-compat. Storage is local-only — never on the NAS share
(§10). Rust owns the loop and the promotion gate; Python owns only the gradient
step.

**Why.** `harness/training/worker.py` already has this exact security contract —
`HandlerSpec` rejects shell wrappers, pins absolute executables, allowlists
environment. Replacing it with HTTP adds a long-lived process and a failure mode
for zero gain (training is batch, not request-path). PyO3 drags the Python ABI
into the Rust build and breaks `forbid(unsafe_code)` plus `cargo-deny`.

**Makes impossible:** a shell-injection path into training; Python dependencies
leaking into the Rust workspace; a training run with no pinned artifact lineage.

---

## D4 — `dw-search` is the colloquial funnel, not a retriever

**Ruling. This supersedes the §5.7 reading of "FTS + sections first."**

`dw-search` resolves **colloquial design intent into technical knives, then
eliminates a cohort.** It is the front door of the §0 funnel. Text retrieval is
the residue path, not the product.

```
"outdoor weather thermostat bluetooth"
   |-- outdoor    -> temp_max_c + qualification_or_vertical_signals (+ moisture: not in index)
   |-- weather    -> n_adc / adc_resolution_bits
   |-- thermostat -> occasion: home_building  +  drive_a_motor
   |-- bluetooth  -> has_wireless_ble
   v
Vec<Atom> + Vec<Binding> + residue  ->  dw-decide  ->  COHORT + eliminations + next Question
```

### The binding type

The colloquial → knife map lives in **`dw-knives::phrase`**, beside the axis
registry, because it is the same kind of object: discovered-then-curated, gold
gated, evidence-backed.

```rust
struct ColloquialBinding {
    phrase:      PhraseId,          // "does it talk to a car"
    knives:      Vec<AtomTemplate>, // n_can >= 1
    confidence:  f64,
    evidence:    Vec<DocRef>,       // where the binding was learned
    stamp:       Stamp,             // see below
}

enum Stamp {
    ProvenFit,                      // may eliminate
    PositiveOnly,                   // may include, NEVER eliminates
    Blocked(&'static str),          // substrate below floor; reason required
}
```

### Three laws

1. **A binding's stamp is its license to eliminate.** `ProvenFit` eliminates.
   `PositiveOnly` may only include. `Blocked` may do neither. This is the
   Infineon ruling already made on 2026-08-14 — *positive-fit only, they never
   eliminate* — promoted from a one-off ruling to a type.
2. **The stamp is measured, not declared.** `activation_floor = 0.5` of the
   decision universe classified. Below the floor the binding is `Blocked` with
   the reason. `has_wireless_ble` is `Blocked` today (Nordic 1/31 true) despite
   being 18.6% classified.
3. **`Unknown` never eliminates.** `C9`, unchanged. An unclassified substrate
   contributes no elimination, ever.

### The four drawers (ratified 2026-08-14, recorded here so it is never reopened)

These are four distinct axes and must never be conflated:

| drawer | cardinality | grain | example |
|---|---|---|---|
| `applications[]` | 0..N overlapping | **occasion** — a physical end product you can put on a bench | washing machine, video doorbell |
| `catalog_subcategory` | exactly 1, exclusive | **partition** — vendor catalog bucket | "Automotive body and lighting MCUs" |
| `target_markets_or_industries` | 0..N | **sector** | Automotive, Industrial |
| `qualification_or_vertical_signals` | 0..N | **qualification tier** | AEC-Q100, -40/+125, functional safety |

**Rule:** a string naming a bench-able end product goes in `applications[]`. A
string naming a sector, catalog bucket, or qualification tier goes in the other
three. Never `applications[]`.

**Unknown stays Unknown.** Do not derive `target_markets` from `subcategory` —
"Automotive body and lighting MCUs" → "Automotive" is invention.

The motivating case: an AEC-Q100, -40/+125 automotive-rated part is genuinely
not specced into a home thermostat. That constraint lives in **qualification /
temp / rating**, not in the subcategory, and not in the occasion.

### Eval

**The query telemetry is the gold set:**

`/Users/samkim/Downloads/microcontroller_engineering_intent_analysis_with_complex_searches (1).xlsx`

| sheet | rows | searches | role |
|---|---|---|---|
| Executive Themes | 15 | — | theme taxonomy (labeled categories) |
| Representative Searches | 860 | 5,213 | weighted traffic distribution |
| Complex Searches | 100 | 1,066 | multi-constraint + boolean/quoted |
| Design Intent Searches | 525 | 2,374 | colloquial tier — **this is D4's target** |

Shape of the 860 representative queries: 614 prose · 137 numeric-parametric ·
81 OPN-ish · 28 boolean/quoted. **Prose is the majority**, which is why FTS-first
was wrong.

Score by **search-count-weighted** recall of knife bindings and of theme
classification. Unweighted accuracy would let the 709-hit query `"32b 24 MHz
ARM MCU"` be drowned by 600 one-hit typos.

**Makes impossible:** a search endpoint that returns passages and calls it a
cohort; a colloquial phrase silently binding to the wrong drawer; stamping a
knife on 18% coverage; an eval that scores by raw query count.

---

## D5 — Unmapped phrase policy

**Ruling.** A colloquial phrase that binds to no knife and no axis emits a
first-class **`MissingAxis`** and a ranked **`Question`**. It is never a silent
miss and never a bare error.

```rust
// Build, §4 — these fields already exist and are load-bearing
unknowns: Vec<MissingAxis>,   // "moisture / ingress protection — not in index"
next:     Vec<Question>,      // ranked by expected cohort reduction
```

The question asked is the **technical** one that would make the colloquial one
evaluable: the phrase "outdoor" cannot be evaluated on moisture, so the system
asks for an operating-temperature range and names the ingress axis as a gap.

**Why.** §4 already makes `unknowns` a first-class field on `Build` and `C9`
already forbids `Unknown` from eliminating. Falling through to FTS5 would hide
that the phrase meant nothing to the cohort — silent wrongness again. Rejecting
the query is hostile: 614 of 860 real queries are prose.

**Makes impossible:** a colloquial miss that looks like a result; an engineer
believing a cohort was narrowed by a word the system did not understand.

---

## D6 — Vendor priors are measured, never authored

**Ruling. A `VendorPrior` is a projection of `dw-coverage`, not a curated
table.** There is no hand-authored prior anywhere in the workspace.

```rust
struct VendorPrior {
    vendor:            VendorId,
    category:          CategoryId,
    axis:              KnifeId,
    observed_fraction: f64,          // from the coverage rollup
    classified:        u32,
    denominator:       u32,
    source_release:    ReleaseId,    // which rollup this came from
    method:            Observed<Method>,  // selector | datasheet | vision | NotObserved
}
```

**The type ships day one. The population is derived.** §6's "populate TI, ST,
NXP, Renesas, Vishay" becomes: those five have enough measured coverage in
`dw-coverage` to produce a prior with a stated confidence. Every other vendor
yields `Unknown` until measured. A prior is never written down; it is recomputed
from the cube on each release.

### What a prior may do

| use | allowed |
|---|---|
| forecast a gap (`expected_not_found` is likely here) | **yes** |
| rank next questions by expected cohort reduction | **yes** |
| prioritize acquisition (this vendor's axis is dark) | **yes** |
| emit a `Claim` | **never** |
| satisfy an `Atom` / a `Requirement` | **never** |
| eliminate a candidate from a cohort | **never** |

This is the same object as D4's `PositiveOnly` stamp: **it may influence
attention, never truth.** Printed evidence always wins, and `C9` is unchanged —
`Unknown` never eliminates, and a prior is a form of `Unknown`.

**Why.** A hand-authored "TI parts usually have USB" is an invented fact. The
evidence already shows why that is dangerous: `20260814_yellow_scorecard` has
`has_usb` classified on only 3,489 of 18,789 MCUs, and the coverage is an
**ST/TI island** — `do_not_stamp` explicitly forbids treating vendor `has_usb`
as the USB knife. Meanwhile Renesas is 49/5,059 on that island but 4,989/5,059
on `lifecycle`. A prior keyed only on vendor would be wrong in both directions.

**Makes impossible:** an invented fact reaching a cohort; a prior silently
becoming a claim; a vendor reputation standing in for printed evidence.

---

## D7 — "Working awesome" is front-end feed completeness

**Ruling. The product is done when every front end can render the experience
from a single response, with no second call and no guess.** The §0 acceptance
test is the *content* gate. This is the *delivery* gate. Both must pass.

> **Definition.** We are feeding front ends the data they need to create the
> experience: type what you are building in your own words → see the constraints
> that were derived and which you confirmed → watch a cohort of distinct parts
> narrow → open every exclusion to the page it came from → see what could not be
> evaluated → take the next question that narrows most.

A response that is logically correct but omits evidence links, hides what it
could not evaluate, or requires a second call to explain itself **fails**, even
if the §0 test passes.

### The surfaces, and what each must be served

| surface | renders | must receive |
|---|---|---|
| **Intent composer** | "what are you building?" | available `ColloquialBinding`s + live preview of which knives a phrase will bind · what is **not** evaluable (`MissingAxis`) |
| **Constraint ledger** | declared vs derived vs unknown | `declared` · `derived` (never silent) · `constraints` · `unknowns`, each with confirm/reject action and provenance |
| **Cohort explorer** | the narrowing part list | distinct parts (`Cohort`, a type — not passage matches) · per-part `Verdict` · per-exclusion reason + evidence ref |
| **Evidence viewer** | every number opens to its page | `SourceRef` + content-addressed page render + row-band crop + `crop_sha256` (`C13`) |
| **Next-question rail** | what to ask next | ranked `Question` with expected cohort reduction + `AXIS_HELP` prose |
| **Coverage / gap view** | what we know we don't know | coverage cube slice · gap ledger · `laned: bool` honesty · curve availability |
| **Release / lineage** | which truth am I looking at | `ReleaseId` on **every** response · append-only lineage · rollback affordance |

### Contract consequence for C7

`GET /api/extraction/v1/parts/{part_number}` and the discovery `designs/…`
routes are **not** resource endpoints that a UI assembles into an experience.
They are rendering substrates. Every response carries, inline:

1. `ReleaseId` (already required by C7 — now required to be *renderable*, not
   just present)
2. evidence refs resolvable to a page render + crop without another call
3. an explicit `unknowns` block — the UI must be able to show what was not
   evaluated, so it must be in the payload, never inferred from absence
4. per-exclusion reason + evidence — `Ineligible` is unconstructible without
   them (§16.5), so the payload must carry them
5. ranked `next` questions with expected reduction

`application/problem+json` errors must also be renderable: a failed request
still has to tell a human what happened and what to do.

**Why.** §0's acceptance test reads like a CLI transcript. The product is a
front end. Without this rule the workspace can pass every gold fixture and
still deliver something nobody can build the experience on — which is the only
failure that matters.

**Makes impossible:** shipping a correct engine with an unshippable interface;
a UI inferring `unknowns` from a missing field; an exclusion the engineer
cannot click open.

---

## Superseded / not decided here

| item | status |
|---|---|
| PDF engine choice (PDFium vs PyMuPDF) | **decided — D1, PDFium.** G7 path-segment gate remains open and blocks `dw-pdf` work |
| Vendor priors content (TI, ST, NXP, Renesas, Vishay) | **decided — D6, derived from `dw-coverage`, never authored** |
| Pair-file location for the 2,713 / 87K shapes | **open** — nearest candidates are `/Volumes/M5_4TB/exports/power-datasheet-pairs/{ti,rohm,infineon}-20260908/` (9,201 lines today) and `results/lora-v1-pairs/`; reconcile before Week 1 |
| `dw-queue` lease / reclaim semantics | **open** — needs durations and policy |
| C7 request/response schemas beyond the coverage cube | **open — now gated by D7.** Schemas are specified per-surface, not per-endpoint |

---

## Evidence index

| ref | artifact |
|---|---|
| E1 | `tests/fixtures/gold/` — 6.6 MB parity oracle (D1) |
| E2 | `harness/electronics/embeddings.py` — immutable content-addressed BGE sidecar (D4) |
| E3 | `harness/training/worker.py` — `HandlerSpec` argv/no-shell/env-allowlist/timeout (D3) |
| E4 | `/Volumes/M5_4TB/exports/cr_drops/20260813_mcu_yellow_occasion_holes/holes.json` — `chips_discovered`, `unknown_never_eliminates`, `activation_floor 0.5`, `do_not_stamp` (D4, D5) |
| E5 | `/Volumes/M5_4TB/exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json` — 10 knives × 10 vendors × 18,789 MCUs, classified fractions (D4 stamps) |
| E6 | `/Volumes/M5_4TB/agent-inbox/cursor-cr/20260814T174540Z_…_ti-subcategory-vs-application-ruling-needed.md` and the 2026-08-14 ruling — the four drawers (D4) |
| E7 | query telemetry xlsx — 1,485 query rows / 703 unique strings / 8,653 searches (D4 eval) |
| E8 | `harness/discovery/evaluator.py` `AXES` (5 power axes) + `service.next_question` expected-reduction ranking (D4, D5) |
| E9 | **PDF spike 2026-10-10** — LMR33610 p.8, `document_sha256 777b3966…e39611`, PyMuPDF 1.28.2 vs `pypdfium2`. G1–G6 measured, G7 untestable via the Python binding (D1) |
| E10 | `harness/electronics/*` PDF surface — 8,406 LOC across `word_columns` `regions` `locator` `page_evidence` `extraction` `family_*` `power_datasheet`; API used: `get_text(text/words/dict/blocks)`, `get_drawings()`, `get_pixmap`, `get_images`, `fitz.Rect/Matrix`, `page.rect` (D1 port scope) |
