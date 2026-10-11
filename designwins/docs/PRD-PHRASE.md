# PRD — the phrase layer and the axis registry

**Status:** for review · **Owner:** `dw-knives` + `dw-decide` · **Gates:** Gate D4 telemetry, plus per-axis gold · **Companion:** `DECISIONS.md` **D4/D5** (the laws), `PLAN.md` §6 (taxonomy induction)

---

## 1. Problem

Gate D4 measured the phrase layer against real search traffic and found:

```
bound        19 / 7423 weighted searches   0.26%
  ProvenFit   0                            0.00%   <- may eliminate
unmapped   7404                            99.74%
```

The instinctive response is "write more bindings." **That will not work**, and
the reason is worth stating precisely:

> **A phrase cannot bind to a knife that does not exist.**

Of the 7,423 weighted searches, here is what the traffic actually asks for
versus what the workspace can express:

| vocabulary | weighted searches | share | axis exists? | substrate measured? |
|---|---:|---:|---|---|
| **core family** (Cortex, AVR, PIC, MSP430, ESP32, RISC-V) | **3,514** | **47.3%** | **NO** | **NO** |
| frequency | 2,281 | 30.7% | `FreqMhz` | **yes**, 75.6% classified |
| USB | 964 | 13.0% | `HasUsb` | yes, 18.6% |
| **HMI / display** (LCD, TFT, touch, GUI, LVGL) | **740** | **10.0%** | **NO** | **NO** |
| flash | 475 | 6.4% | `FlashKb` | yes, 69.8% |
| **motor** (BLDC, stepper, FOC, PWM) | **293** | **3.9%** | `HasMotorControl` (flag only) | partial |
| **ADC / DAC / analog** | **256** | **3.4%** | **NO** | **NO** |
| CAN | 180 | 2.4% | `HasCan` | yes, 18.6% |
| package | 156 | 2.1% | **NO** | **yes, 98.7%** |
| **ethernet** | **69** | **0.9%** | **NO** | **NO** |
| RAM | 63 | 0.8% | **NO** | **yes, 78.5%** |
| temperature / automotive | 57 | 0.8% | `TempRangeC` | yes, 72.1% |
| BLE / wireless | 28 | 0.4% | `HasWirelessBle` | yes, 18.6% |
| nothing recognisable | 1,040 | 14.0% | — | — |

**Two findings, in order of size:**

1. **~67% of traffic asks for something with no knife to bind to.** The single
   largest class, core family, is 47.3% and has neither an axis nor a measured
   substrate.
2. **Three axes that are measured are not typed.** `package` (98.7% classified
   — the best-covered field in the corpus), `ram_kb` (78.5%), and `lifecycle`
   (80%) all have substrate and none is an `Axis` variant.

Growing the phrase registry alone moves 0.26% very slowly. **The axis registry
has to grow first, or in lockstep.**

---

## 2. Two registries, and they are coupled

```
   documents ──▶ AXIS REGISTRY ──▶ KnifeId ──┐
                   (discovered,               │
                    measured,                 ├──▶ Atoms ──▶ dw-decide ──▶ COHORT
                    curated)                  │
   human text ──▶ PHRASE REGISTRY ──▶ AtomTemplate ──┘
                   (colloquial →
                    knife binding)
```

They are the same kind of object — discovered, evidence-backed, stamped — and
neither works without the other:

* a phrase with no knife produces `MissingAxis` (D5) and narrows nothing
* a knife with no phrase is unreachable from a human sentence
* a knife with no substrate is `Stamp::Blocked` and may not eliminate

**So the sequencing constraint is real**: an axis must exist *and* have measured
coverage before any phrase binding to it may eliminate. This PRD therefore
specifies both, and orders the work accordingly.

---

## 3. Ground truth

Do not re-derive these.

| what | where | what it gives |
|---|---|---|
| **measured substrate** | `exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json` | 10 knives × 10 vendors × **18,789 MCUs**, classified fractions per knife and per vendor |
| **the chips** | `exports/cr_drops/20260813_mcu_yellow_occasion_holes/holes.json` | 5 colloquial chips with measured stamps, `activation_floor 0.5`, `do_not_stamp` list |
| **the four drawers** | `agent-inbox/cursor-cr/20260814T174540Z_*ti-subcategory-vs-application*.md` | occasion / subcategory / market / qualification — never conflated |
| **query traffic** | `tests/fixtures/gold/search_telemetry_v1.jsonl` | 1,342 rows, 7,423 searches, 15 themes, count-weighted |
| **taxonomy induction** | `PLAN.md` §6 | the six signals; `harness/electronics/{family_census,family_document,family_device_matrix}.py` |
| **existing axes** | `harness/discovery/evaluator.py::AXES` | 5 power axes, verified over 1,881 parts / 86,736 claims |

### The measured substrate, exactly

| knife | classified | fraction | per-vendor shape |
|---|---:|---:|---|
| `package` | 18,538 | **98.7%** | near-complete |
| `lifecycle` | 15,030 | 80.0% | |
| `ram_kb` | 14,746 | 78.5% | |
| `freq_mhz` | 14,208 | 75.6% | |
| `temp_max_c` | 13,541 | 72.1% | |
| `flash_kb` | 13,118 | 69.8% | Microchip 95%, NXP 24%, Renesas 0% |
| `has_can` | 3,489 | 18.6% | **ST/TI island** |
| `has_can_fd` | 3,489 | 18.6% | same island |
| `has_usb` | 3,489 | 18.6% | same island |
| `has_wireless_ble` | 3,489 | 18.6% | Nordic 1/31 true |

**The four `has_*` knives share one 3,489-row island** and `do_not_stamp`
explicitly forbids treating vendor `has_usb`/`has_can` as the knife. That is
why all four are `Blocked` or `PositiveOnly` in the seed. Coverage is not the
same as correctness.

---

## 4. Goals

1. **Every high-weight traffic class has a knife**, or an honest `MissingAxis`
   naming why not.
2. **Every knife has measured coverage** before any binding to it may eliminate.
3. **Nothing is invented.** An axis with no evidence-backed definition is not
   an axis.
4. **Progress is measured**, not asserted — Gate D4's floor moves deliberately.

## 5. Non-goals

| non-goal | why | owner |
|---|---|---|
| inferring facts from vendor reputation | `DECISIONS.md` **D6** — priors are a projection of `dw-coverage`, never authored | `dw-coverage` |
| hardcoding an aisle list | `PLAN.md` §18.2 — "no hardcoded aisle list survives contact with 135K PDFs" | — |
| per-vendor special cases in the phrase layer | the stamp is measured; vendor shape belongs in the coverage cube | `dw-coverage` |
| matching free text to document passages | that is retrieval, not constraint binding | `dw-search` |
| choosing which candidate to buy | the funnel narrows; it does not recommend | — |
| fixing `holes.json`'s substrates | they are measurements; disagreement is a finding | — |

---

## 6. What an axis is

An axis is **discovered, then curated, then measured**. In that order, and the
order matters:

| stage | what happens | gate |
|---|---|---|
| **discover** | cluster documents on the six induction signals (`PLAN` §6) | clusters byte-stable |
| **curate** | a human names the cluster and writes the definition | `DECISIONS.md` entry |
| **measure** | compute the classified fraction over the decision universe | ≥ [`ACTIVATION_FLOOR`] before any binding may eliminate |

### The six induction signals (`PLAN.md` §6)

| signal | source | teaches |
|---|---|---|
| self-classification | `l0.py` | "this is a buck converter" on the cover |
| TOC signature | `page_index`, `toc_census` | category = a heading distribution |
| **device-table attributes** | `family_census.device_tables` | **the attribute rows a comparison table addresses ARE the category's axes** |
| EC-table symbols | `power_axes_v2`, `mode_currents` | printed symbol vocabulary = the axis set |
| curve fingerprint | `curve_type` | efficiency_vs_load ⇒ power; bode ⇒ converter |
| OPN decoder shape | `ordering_codes` | vendor × category ordering grammar |

**Device-table attributes are the load-bearing signal for this PRD.** The
attribute rows a comparison table addresses *are* the axes — which is how
`package`, `ram_kb`, and `lifecycle` are already measured at 98.7%, 78.5% and
80% without ever being typed. They were discovered; they were never curated.

### The `core_family` axis — the 47%

This is the largest single gap and the clearest case of the induction signal:

* **source**: `family_census` / `family_document` already model
  `family → series → OPN`; `l0.py` self-classification reads the cover
* **shape**: not a boolean. A part belongs to a core family, and families nest
  (Cortex-M4F ⊂ Cortex-M ⊂ ARM). It is a **graph node**, not a scalar
* **drawer**: `CatalogSubcategory` or `TargetMarkets` — **not** `Applications`.
  "ARM Cortex-M4" is not a thing you build; it is a partition. The
  four-drawers ruling applies directly
* **known trap**: `holes.json` records that `applications_text` is a
  Renesas-only 12-value enum and must not be used as a filter. Core family is
  a different drawer and a different substrate — do not merge them

---

## 7. What a binding is

Already ratified: `DECISIONS.md` **D4** and **D5**. Summary, not re-litigation:

```rust
ColloquialBinding {
    phrase:     PhraseId,           // "does it talk to a car"
    knives:     Vec<AtomTemplate>,  // axis + op + value + drawer
    confidence: f64,
    evidence:   Vec<DocRef>,        // required; unsourced = invention
    stamp:      Stamp,              // ProvenFit | PositiveOnly | Blocked(reason)
}
```

* [`Stamp::ProvenFit`] eliminates · [`PositiveOnly`] may include, never
  eliminates · [`Blocked`] may do neither
* the stamp is **measured against [`ACTIVATION_FLOOR`] (0.5)**, never declared
* non-`ProvenFit` bindings have their atoms **forced soft at construction**
* unmapped residue ⇒ [`MissingAxis`] + [`Question`], never silence

### The four drawers — never conflated

| drawer | cardinality | grain | example |
|---|---|---|---|
| `Applications` | 0..N | **occasion** — a bench-able end product | washing machine, video doorbell |
| `CatalogSubcategory` | exactly 1 | **partition** | "Automotive body and lighting MCUs" |
| `TargetMarkets` | 0..N | **sector** | Automotive, Industrial |
| `Qualification` | 0..N | **tier** | AEC-Q100, −40/+125 |

A string naming a bench-able end product goes in `Applications`. Anything else
goes in the other three. **Unknown stays Unknown** — never derive
`target_markets` from `subcategory`.

---

## 8. Rust design

### 8.1 Axis representation

The axis is no longer a flat enum. `PLAN.md` §6's `CategoryNode` is a graph, and
core family is a graph node. Two changes:

```rust
/// A knife. Either a scalar/boolean axis, or a node in the category graph.
pub enum KnifeId {
    /// Named scalar axis: flash_kb, freq_mhz, temp_max_c, …
    Axis(Axis),
    /// A node in the category graph (core family, product category).
    /// Values are compared by graph distance, not by equality —
    /// Cortex-M4F satisfies a "Cortex-M" requirement; Cortex-M0 does not
    /// satisfy a "Cortex-M4" one.
    Category(CategoryId),
}

/// Which drawer a knife writes to. See §7.
pub struct Knife {
    pub id: KnifeId,
    pub drawer: Drawer,
    /// Measured classified fraction over the decision universe.
    pub coverage: CoverageFraction,
    /// Where the definition came from. Required.
    pub definition: Vec<DocRef>,
}
```

**`Category` is not `Axis`.** A core family is a containment relation
(`Cortex-M4F ⊂ Cortex-M ⊂ ARM`), and `Op::MinRating` on it is meaningless.
It gets its own `Op` family:

| op | meaning |
|---|---|
| `Is` | exact node |
| `Within` | the candidate is in the named subtree |
| `Excludes` | outside the named subtree |

### 8.2 Law enforcement

| law | mechanism |
|---|---|
| no knife without evidence | `Knife.definition` is required and non-empty |
| no elimination below the floor | `Stamp::from_coverage`; a `Knife` below `ACTIVATION_FLOOR` can only produce `Blocked` |
| no invention of drawer | `Knife.drawer` is typed; `Applications` requires `is_occasion` semantics |
| `Unknown` never eliminates | `dw-types::Verdict`, unchanged |
| category ≠ scalar | `KnifeId::Category` has no `MinRating`/`MaxRating` constructors |

### 8.3 Module map

| module | replaces / serves | gate |
|---|---|---|
| `axis` | `evaluator::AXES` (5 power axes) + the three untyped measured knives | per-axis gold |
| `category` | `PLAN.md` §6 `CategoryNode`, `family_census` graph portion | clusters byte-stable |
| `phrase` | already landed | Gate D4 |
| `stamp` | `holes.json` measurement rules | `Stamp::from_coverage` tests |

---

## 9. Acceptance criteria

| gate | bar | currently |
|---|---|---|
| **D4 coverage floor** | bound weighted searches ≥ a floor that moves only upward | **19** (0.26%) — this PRD exists to raise it |
| **D4 law 1** | no `Blocked` binding contributes an atom; no non-`ProvenFit` produces a hard atom | green |
| **D5** | residue always ⇒ `MissingAxis` + `Question` | green |
| **axis coverage** | every `KnifeId` has a measured fraction and a `DECISIONS.md` definition | 12 of ~20 needed |
| **category clusters** | family clusters byte-stable across a run (`PLAN` Week 2) | not started |
| **no-invention** | every binding carries evidence; every axis carries a definition | enforced by type |
| **the 47% test** | `core_family` traffic binds to something | **0% today** |

### The bar for "done"

Gate D4's `ProvenFit` share must be **non-zero on each of the five
highest-weight themes**, and the floor raised accordingly. A phrase layer that
covers 100% of traffic with `Blocked` bindings is not progress.

---

## 10. Risks

| risk | impact | mitigation |
|---|---|---|
| **inventing axes to hit the coverage number** | the whole point of the stamps is that they are measured. An axis invented to raise `bound` is worse than no axis | every `Knife` needs a definition *and* a measured fraction. No definition ⇒ no axis |
| `applications_text` leaking into core family | `holes.json` forbids it — the enum is Renesas-only | drawer is typed; `core_family` is `CatalogSubcategory` |
| core family as a scalar | `ARM` vs `Cortex-M4` vs `Cortex-M4F` are containment, not equality | `KnifeId::Category` with `Is`/`Within`/`Excludes`; no min/max ops |
| the 3,489-row `has_*` island | four "measured" knives all live on one vendor island and `do_not_stamp` forbids trusting them | stamps stay `Blocked`/`PositiveOnly` until the substrate broadens. Do not raise the floor on these |
| raising the floor without raising the *useful* floor | `bound` can go up while `ProvenFit` stays at zero | the gate reports both. **`ProvenFit` is the number that matters** |
| regressing on 14% unrecognisable traffic | a chunk of queries are `"32b 24 MHz ARM MCU"`-style token soup | do not force them to bind. `MissingAxis` is the honest answer |

---

## 11. Sequencing

| step | produces | unlocks | gate |
|---|---|---|---|
| 1 | **type the three already-measured knives** (`package`, `ram_kb`, `lifecycle`) | the cheapest coverage available — substrate exists at 98.7 / 78.5 / 80% | axis coverage |
| 2 | **`core_family` as `KnifeId::Category`** | the 47% | category clusters |
| 3 | **bind the phrases for the typed axes** (frequency, flash, USB, CAN, package) | raise `bound` on real traffic | D4 floor |
| 4 | `hmi_display`, `motor`, `adc_dac`, `ethernet` axes | the next ~17% | per-axis gold |
| 5 | stamps from measured coverage; raise the floor | `ProvenFit` > 0 | D4 |

**Step 1 first.** It is typing work with the measurement already done, and it
covers three of the top six vocabularies. Step 2 is the 47% and is the real
work. Do not start step 5 until step 3 has produced `ProvenFit` bindings —
raising the floor on `Blocked` coverage is not progress.

---

## 12. Review checklist

Before approving:

- [ ] §1 numbers verified against `search_telemetry_v1.jsonl` and `yellow_scorecard`
- [ ] §6 the six induction signals match `PLAN.md` §6 verbatim — no reinterpretation
- [ ] §7 four drawers match the 2026-08-14 ruling — no drift
- [ ] §8 `Category` vs `Axis` split accepted: is `Within`/`Excludes` the right op set?
- [ ] §9 the "done" bar — is `ProvenFit > 0` on the five heaviest themes the right test?
- [ ] §10 is the no-invention risk mitigated enough by "definition + measured fraction"?
- [ ] §11 step 1 (type the three already-measured) accepted as first — or is `core_family` urgent enough to jump?
- [ ] **Is there a substrate for `core_family` that I have not found?** `family_census` models it; is there a measured family/series table anywhere?

---

## Appendix A — traffic → vocabulary, full table

Measured 2026-10-10 against `search_telemetry_v1.jsonl` (7,423 weighted). Probe
patterns are a first cut for scoping the PRD, not a parser.

| probe | weighted | share | axis | substrate |
|---|---:|---:|---|---|
| core family | 3514 | 47.3% | — | — |
| frequency | 2281 | 30.7% | `FreqMhz` | 75.6% |
| USB | 964 | 13.0% | `HasUsb` | 18.6% |
| HMI / display | 740 | 10.0% | — | — |
| flash | 475 | 6.4% | `FlashKb` | 69.8% |
| motor | 293 | 3.9% | `HasMotorControl` | partial |
| ADC / DAC | 256 | 3.4% | — | — |
| CAN | 180 | 2.4% | `HasCan` | 18.6% |
| package | 156 | 2.1% | — | **98.7%** |
| ethernet | 69 | 0.9% | — | — |
| RAM | 63 | 0.8% | — | **78.5%** |
| temperature | 57 | 0.8% | `TempRangeC` | 72.1% |
| BLE / wireless | 28 | 0.4% | `HasWirelessBle` | 18.6% |
| power mgmt | 39 | 0.5% | — | — |
| dev platform | 34 | 0.5% | — | — |
| security | 18 | 0.2% | — | 0% (`holes.json` `do_not_stamp`) |
| lifecycle | 0 | 0.0% | — | **80.0%** |
| nothing recognisable | 1040 | 14.0% | — | — |

Note `lifecycle`: 80% substrate, **zero traffic**. Substrate without demand is
not a priority. Demand without substrate is the problem this PRD exists to
solve — and there, `core_family` is 47% with neither.
