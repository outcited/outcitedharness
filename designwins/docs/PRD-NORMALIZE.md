# PRD — `dw-normalize`

**Status:** for review · **Owner:** port of `harness/electronics/{condition_model,rating_class,curve_atoms,mode_currents,thermal_metrics}.py` (1,283 LOC) · **Gates:** gold byte-parity + property tests · **Supersedes:** nothing; this is the first specification of the normalization layer.

---

## 1. Problem

A claim is a string a PDF printed. A design requirement is a number an
engineer needs. Between them sits normalization, and if it is wrong the whole
funnel is wrong.

Without normalization the funnel cannot do its only job:

```
WHAT ARE YOU BUILDING?  ->  typed constraints  ->  COHORT
                            ->  compare @ your point  ->  explain  ->  next question
```

"Compare at your point" is not string matching. It is: *does this claim's
evidence condition **cover** my operating condition, and is this printed value
the kind of quantity that can satisfy my requirement at all?* Two questions,
both answered here:

| question | answered by | failure if wrong |
|---|---|---|
| Can this value satisfy this requirement? | **rating class** | a 40 V absolute maximum satisfies a 36 V operating atom — the device dies in the field |
| Does this evidence cover my operating point? | **condition typing** | a claim measured at 25 °C is silently used at 125 °C |
| Are these two numbers the same physical quantity? | **canonical units** | 500 mV compared as 500 V |
| What kind of curve is this? | **curve atom class** | an SOA plot is read as a transfer characteristic |

**Normalization is why a cohort is defensible.** Everything downstream —
`dw-truth` admission, `dw-decide` elimination, `dw-knives` axes — consumes its
output. It is the single highest-leverage correctness surface in the workspace.

---

## 2. Ground truth already exists

This is a **port with a parity oracle**, not a design-from-scratch. The Python
module docstrings *are* the current specification and the gold fixtures are the
executable form of it.

| gold fixture | n | gates |
|---|---|---|
| `tests/fixtures/gold/condition_model_v1.jsonl` | 167 | `parse_condition` + `condition_covers` |
| `tests/fixtures/gold/rating_class_v1.jsonl` | 63 | `rating_class` projection |

Measured shape of the condition corpus (this is the real input distribution,
not a toy):

| outcome | n | share |
|---|---:|---:|
| typed (`keys` present) | 141 | 84.4% |
| `reject: no_anchors` | 21 | 12.6% |
| `reject: units_absent` | 2 | 1.2% |
| `reject: dash` | 2 | 1.2% |
| `reject: ambiguous_symbols` | 1 | 0.6% |

**15.6% of printed conditions must be refused — roughly one in six.** Any
implementation that types everything is inventing facts.

Rating corpus distribution (63 rows), and note how much of it is *not*
straightforward:

| class | n |
|---|---:|
| `unknown` (fail-closed) | 17 |
| `absolute_maximum` | 16 |
| `rated_limit` | 11 |
| `typical` | 9 |
| `transient` | 6 |
| `recommended` | 3 |
| `surge` | 1 |

`transient` fires *inside* absolute-maximum tables on symbol vocabulary, and
`unknown` is the second-largest class. A naive `table_kind` projection gets
both wrong.

**Week 1 gate (PLAN §12):** condition 167/167 · `rating_class_v1` · 5 power
axes port 1:1 · compile-fail: `Claim` unconstructible · compile-fail:
`Conflict` sticky · **87K claim diff = 0 unexplained**.

---

## 3. Goals

1. **Byte-parity with the Python reference** on both gold fixtures. Not
   "equivalent" — identical, field for field.
2. **Law-as-types.** The four laws below are enforced by construction, not by
   review.
3. **Verbatim is never destroyed.** `condition_verbatim` stays the provenance
   anchor (`C3`). Typed output is additive.
4. **Fail closed.** An unparseable condition is a finding (`reject`), never a
   guess.
5. **Deterministic.** Same input → same output bytes, independent of map
   iteration order (`PLAN` §10).

## 4. Non-goals

Explicitly out of scope for this crate. Doing any of these here would be the
"shit code" failure mode.

| non-goal | why | where it belongs |
|---|---|---|
| Reading the PDF | it is a different problem | `dw-pdf`, `dw-structure` |
| Deciding whether a claim is *true* | admission is a gate, not a transform | `dw-truth` |
| Deciding whether a candidate is eliminated | elimination is the funnel | `dw-decide` |
| Inducing new axes or symbols | discovery, not normalization | `dw-recon`, `dw-knives` |
| Calling a model | forbidden in this crate | `dw-model` |
| Fixing bad input | fill-only; never repair text | upstream extraction |
| Unit *discovery* (guessing mV vs MV) | ambiguity must reject | — |

---

## 5. Semantics — frozen

These are lifted verbatim from the reference's documented contract. **They are
not open for reinterpretation.** Changing one requires a `DECISIONS.md` entry
and a gold fixture change in the same commit.

### 5.1 Condition typing (`parse_condition`)

**Input:** `condition_verbatim: &str` — the string the document printed.
**Output:** either a typed key map, or a rejection.

```
Typed {
    keys:        SortedMap<ConditionKey, Value>,   // canonical SI anchors
    mode:        Option<Mode>,                     // static | ac | dynamic
    approx:      Set<ConditionKey>,                // ≈ ⋍ bound
    sym:         Set<ConditionKey>,                // ± bound
    bare:        Set<ConditionKey>,                // no symbol printed
    comparators: Vec<String>,                      // verbatim segments
    residue:     Vec<String>,                      // verbatim, unmatched
}

Rejection = Dash | NoAnchors | UnitsAbsent | AmbiguousSymbols
```

**Value** is `Scalar(f64)` or `Range(f64, f64)` — the latter for a printed
range or list (`0 to 10 V`, `0V to 480V`, `0/18 V`, `1.0V≦VOUT＜1.2V`).

**Keys** are the canonical anchor vocabulary. The symbol table is ordered and
**order is semantic** (first parsable occurrence of a symbol wins). It is
frozen and additive-only (`C6`). Current cardinality: **41 symbols** across 8
unit kinds (`v`, `a`, `hz`, `c`, `c_per_w`, `ohm`, `h`, `a_per_s`), listed in
Appendix A.

**Laws — each one exists because the corpus raised it:**

| # | law | example that produced it |
|---|---|---|
| L1 | **Never invent units.** A known symbol + bare number + no unit rejects as `UnitsAbsent`. It never becomes 0 V. | `VGS=0 ,VDS=80` |
| L2 | **Single-letter `V`/`I` are ambiguous** (`AmbiguousSymbols`) — subscript-lost, never typed. | `V =12 V`, `I =20 A` |
| L3 | **Bare number+V is `residue`** — cannot tell VGS from VDS from VIN. Bare number+°C *is* unambiguous in kind and types as `t_c`, flagged `bare`. | `4.5V` |
| L4 | **First occurrence wins.** Later duplicates stay in `residue` verbatim. | `diode: body diode at VGS = 0 V` |
| L5 | **`=` and comparators (≤ ≥ < > ≦ ≧ ＜ ＞) and approx (≈ ⋍) all bind a value.** Comparators record the verbatim segment; approx flags the key. | `AC (f>1 Hz)` |
| L6 | **`±` types the magnitude and flags the key `sym`.** | — |
| L7 | **Mode words {static, ac, dynamic} type to `mode`.** Everything else unrecognized stays in `residue` verbatim. | `static` |
| L8 | **Dash-only and punctuation-only reject** (`Dash`, `NoAnchors`). | `-`, `–` |
| L9 | **Reversed bounds are scanned before symbol anchors** so the bound value is not stolen. | `2.5 V≤VOUT`, `1.0V≦VOUT＜1.2V` |
| L10 | **Case collapse is upstream.** `mV`/`MV` ambiguity resolves to the milli reading (dominant in power tables). | `tc = 25 °c` |

### 5.2 Condition coverage (`condition_covers`)

The elimination contract (`C9.6`: *a condition must COVER the point*).

```
Exact     same key sets, values agree on every shared key, and no claim
          value is strictly wider than the requirement's
Covers    claim agrees on every required key AND is strictly broader:
          extra keys, or a range strictly containing the requirement
Partial   shared keys agree, but the claim is missing some required keys
Mismatch  a shared key disagrees
Absent    claim untyped, OR no shared keys
```

**Value agreement is range-overlap, not equality.** `_values_agree` treats two
values as agreeing when their ranges overlap within a relative epsilon
(`1e-9 · max(1, |bounds|)`). `0 to 10 V` and `5 to 15 V` agree; `0 to 4 V` and
`5 to 10 V` do not. **Strictly-wider** is containment plus at least one strict
inequality — that is what lifts a match from `Exact` to `Covers`.

**Two deliberate asymmetries at the empty boundary.** They are properties of
the elimination law, not bugs:

* `condition_covers(claim, {}) == Exact` — a requirement that pins no condition
  is satisfied by any typed evidence.
* `condition_covers({}, req) == Absent` — untyped evidence establishes nothing,
  so it never covers (`C9.1`).

**The law that matters:** `Partial`, `Mismatch`, and `Absent` all mean *we
could not establish coverage* — which under `C9.1` is `Unknown`, which
**never eliminates**. Only a `Covers`/`Exact` claim may satisfy an atom; only a
proven `Mismatch` on a hard atom eliminates.

### 5.3 Rating class (`rating_class`)

Closed set (`C6`), and **classes are different physical quantities**:

| class | meaning | may satisfy an operating atom? |
|---|---|---|
| `absolute_maximum` | stress rating | **never** (`C9.2`) |
| `recommended` | recommended-operating / operating range | yes |
| `rated_limit` | EC/summary/thermal min\|max\|rated limits | yes |
| `typical` | typ-column performance | yes, but **never becomes guaranteed** (`C9.5`) |
| `transient` | pulsed/avalanche (`IDM`, `ID,pulse`, `EAS`, `IAS`, `VPULSE`, `Zth`) — even inside absmax tables | no (different quantity) |
| `surge` | `ISM`, `IASM`, `IFSM`, `ITSM`, `VSURGE` | no (different quantity) |
| `unknown` | fail-closed: unknown role, diagram fragments, leaks | **never** |

Projection inputs: `table_kind`, `quantity_qualifier`, symbol vocabulary.
**Pulse/surge vocabulary fires on the SYMBOL; a printed "continuous" vetoes
it** — for glued composite symbols like `ID@TC=25°C ID@TC=100°C IDM` where the
value is the continuous row.

### 5.4 Margin and near-miss (`compare_with_margin`)

`PLAN` §43/§42, and `C9.3`:

1. **Margin is applied to the REQUIREMENT, never the claim.** 36 V max + 20%
   margin → the rating must be ≥ 43.2 V.
2. **Margin is never silent.** `margin_pct` is passed explicitly or is
   explicitly `None`. There is no default that hides it.
3. **`NearMiss` is a separate cohort, not a `Fail`.** A claim that fails the
   raw requirement but sits within `near_miss_pct` is `NearMiss`.

### 5.5 Canonical units

Multipliers are per-kind and **frozen**. They are applied at parse time so that
`500 mV` and `0.5 V` are the same `Quantity`.

| kind | multipliers accepted |
|---|---|
| `v` | mV 1e-3, V 1.0, kV 1e3 |
| `a` | nA 1e-9, µA 1e-6, mA 1e-3, kA 1e3, A 1.0 |
| `hz` | Hz 1.0, kHz 1e3, MHz 1e6, GHz 1e9 |
| `ohm` | mΩ 1e-3, Ω 1.0, kΩ 1e3 |
| `h` | nH 1e-9, µH 1e-6, mH 1e-3, H 1.0 |
| `a_per_s` | s 1.0, ms 1e3, µs 1e6, ns 1e9 |

`c` and `c_per_w` carry no multiplier (°C, °C/W, K/W).

**`f64` throughout. No `==` on floats** — every comparison goes through an
explicit, stored tolerance (`PLAN` §10, `clippy::float_cmp` denied).

### 5.6 Curve atom class (`curve_atom`)

Deterministic: axis label + unit → canonical quantity key (same symbol table as
`condition_model`), then `(x, y)` quantity pair + title → `curve_type`.

`curve_type` is **frozen, additive-only** (`C6`). Current vocabulary: 28 named
types plus `iv_generic` and `unclassified`. The fallbacks are the point — an
unclassifiable curve is `unclassified`, never a guess.

### 5.7 Thermal metrics

Fill-only. Printed evidence only: the metric symbol (or spelled form) with a
value and a `°C/W` or `K/W` unit **from the same printed row**. Condition text
(JEDEC reference, layer count, still air) stays verbatim; package scope is kept
when the row prints it.

**A dimensionless `RθJA` mention is not a value.** Rows without a thermal unit
never emit.

### 5.8 Mode currents

Mode-current extraction (`mode_currents`, 322 LOC) normalizes printed
mode-dependent current rows (e.g. FCCM vs diode-emulation) into typed
quantities with their mode preserved. It shares the symbol/unit tables and is
subject to the same fill-only law.

---

## 6. Rust design

### 6.1 Crate boundary

`dw-normalize` is **pure**: no I/O, no model calls, no `unsafe`. It takes
strings and structured inputs, returns typed structures. `forbid(unsafe_code)`.

It depends only on `dw-types` (for `Quantity`, `Unit`, `Rating`, `Observed<T>`)
and `serde`.

### 6.2 Type shape

The law is in the types. Some of these already exist in `dw-types`; the rest
are new here.

```rust
/// C11: absence is a fact. A condition key that was not printed is
/// NotObserved, not None.
pub struct TypedCondition {
    pub keys:        BTreeMap<ConditionKey, CondValue>,   // SortedMap
    pub mode:        Observed<Mode>,
    pub approx:      BTreeSet<ConditionKey>,
    pub sym:         BTreeSet<ConditionKey>,
    pub bare:        BTreeSet<ConditionKey>,
    pub comparators: Vec<String>,
    pub residue:     Vec<String>,
    pub verbatim:    Verbatim,        // C3: never destroyed, never replaced
}

pub enum CondValue { Scalar(Quantity), Range(Quantity, Quantity) }

pub enum ConditionKey { /* frozen, additive-only; see §5.1 */ }

pub enum Rejection { Dash, NoAnchors, UnitsAbsent, AmbiguousSymbols }

/// A parse is either typed or refused. There is no third outcome and no
/// `Option` — see C11.
pub enum ParseOutcome { Typed(TypedCondition), Rejected(Rejection) }

/// C9.6: coverage is the elimination contract.
pub enum Coverage { Exact, Covers, Partial, Mismatch, Absent }

/// PLAN §42: margin is applied to the requirement and is never silent.
pub struct Margin { pub pct: Option<f64> }   // None is explicit, not defaultable

pub enum CompareOutcome { Pass, NearMiss, Fail, Unknown }
```

### 6.3 Laws enforced by construction

| law | mechanism | proof |
|---|---|---|
| verbatim never destroyed | `TypedCondition.verbatim: Verbatim` is required and non-optional | compile-fail |
| fail closed | `ParseOutcome` has exactly two variants; no `Default`, no `From<&str> for TypedCondition` | compile-fail |
| no invented units | `Rejection::UnitsAbsent` exists and is reachable; `Quantity` requires a `Unit` | unit test + gold |
| margin never silent | `Margin.pct: Option<f64>` — there is no `Margin::default()` that implies 0% | compile-fail |
| `Unknown` never eliminates | `CompareOutcome::Unknown` maps to `Verdict::Unknown`, which cannot eliminate | existing `dw-types` test |
| determinism | `BTreeMap`/`BTreeSet` only; `HashMap` is not importable from this crate | lint + property test |
| float discipline | all comparison via `Quantity::approx_eq` with stored tolerance | `clippy::float_cmp` denied |

### 6.4 Module map

| module | replaces | gold gate |
|---|---|---|
| `condition` | `condition_model.parse_condition` | `condition_model_v1.jsonl` (167) |
| `coverage` | `condition_model.condition_covers` | same fixture, `condition_covers` column |
| `rating` | `rating_class.rating_class` + `compare_with_margin` | `rating_class_v1.jsonl` (96) |
| `units` | the multiplier tables | property tests (round-trip, monotonic) |
| `curve_atom` | `curve_atoms` | `atom_class` fields on extraction-v1.1 |
| `thermal` | `thermal_metrics` | fill-only unit tests |
| `mode` | `mode_currents` | mode-current gold |

---

## 7. Acceptance criteria

Nothing ships until all of these hold.

### Gate N1 — gold byte-parity (blocking)

```
condition_model_v1.jsonl   167/167 identical, field for field
rating_class_v1.jsonl       63/63 identical, field for field
```

Comparison is on serialised output with sorted keys, so map ordering cannot
mask a difference. The parity ledger (`dw-parity`, D2) must record **zero**
entries for these surfaces.

### Gate N2 — property tests (blocking)

| property | assertion |
|---|---|
| unit round-trip | `500 mV → 0.5 V` and back, within 1e-9 |
| unit monotonic | scaling preserves ordering |
| parse determinism | 10k random draws of the corpus, same bytes each time |
| coverage reflexivity | `condition_covers(x, x) == Exact` for all **non-empty** typed `x` |
| coverage symmetry of `Exact` | for non-empty `a`, `b` with identical key sets and no strictly-wider value: `Exact(a,b) <=> Exact(b,a)` |
| empty-requirement rule | `condition_covers(x, {}) == Exact`, and `condition_covers({}, y) == Absent` — the asymmetry is deliberate and must not be "fixed" |
| values-agree is **overlap** | two ranges that overlap agree; they need not be equal. `([0,10],[5,15])` agrees, `([0,4],[5,10])` does not |
| fail-closed | any string with no recognised anchor and no dash → `NoAnchors` |
| no invented units | a symbol + bare number never yields a `Quantity` |
| margin explicitness | `compare_with_margin` with `Margin::NONE` differs from `Margin::pct(20)` on the same inputs |

### Gate N3 — compile-fail (blocking)

| assertion | test |
|---|---|
| `TypedCondition` unconstructible without `Verbatim` | `compile_fail/typed_condition_no_verbatim.rs` |
| `ParseOutcome` has no silent fallback | `compile_fail/parse_outcome_default.rs` |
| `Margin` has no defaulting constructor | `compile_fail/margin_default.rs` |

### Gate N4 — the 87K claim diff (blocking, Week 1)

Running `dw-normalize` over the persisted quote-native claim corpus must
reproduce Python's output with **0 unexplained diffs** across 87K claims. This
is the gate that catches anything the gold fixtures do not cover.

### Gate N5 — lint discipline (blocking)

`cargo fmt --check` · `cargo clippy --workspace --all-targets -- -D warnings` ·
`cargo deny check`. No new allow attributes outside test modules.

---

## 8. Risks

| risk | impact | mitigation |
|---|---|---|
| Symbol table ordering is semantic and undocumented in detail | wrong key wins the first-occurrence rule | port the table **verbatim and in order**; gold 167/167 is the proof |
| `mV`/`MV` case ambiguity | 1000× error | law L10 is frozen (milli wins); property test on case folding |
| Regex engine differences (Python `re` vs Rust `regex`) | silent parse divergence | gate on 167/167, not on regex structure; any divergence is a ledger entry, not a rewrite |
| Gold under-covers thermal / mode currents | false confidence | gate N4 (87K) is the coverage backstop; thermal/mode get their own fixtures in the same PR |
| `f64` equality creeping in | nondeterministic output | `clippy::float_cmp` denied; every comparison via `Quantity::approx_eq` |
| Vocabularies drift from `C6` | additive-only law broken | `Denied` on unknown major `schema_version`; vocab enums are exhaustive and additive-only by review |

**The regex-engine risk is the real one.** The plan's answer is not "write the
same regexes" — it is "gate on the gold and let the ledger tell you where they
differ." A divergence is a `RustBug` or an `IntentionalImprovement`, never a
silent rewrite of the semantics.

---

## 9. Sequencing

| step | produces | gate |
|---|---|---|
| 0 | this PRD, reviewed | — |
| 1 | `dw-normalize` skeleton, `units` + `Quantity` wiring | N2 unit properties |
| 2 | `condition` parse, symbol table ported verbatim | N1 condition 167/167 |
| 3 | `coverage` (`condition_covers`) | N1 + N2 coverage properties |
| 4 | `rating` + `compare_with_margin` | N1 rating 63/63 |
| 5 | `curve_atom`, `thermal`, `mode` | fixtures added in-PR |
| 6 | compile-fail proofs | N3 |
| 7 | 87K claim diff run | N4 — **Week 1 gate** |

Steps 2 and 4 are the load-bearing ones. If a step cannot reach gate, it stops
and the diff goes in the parity ledger; it does not proceed on "close enough."

---

## 10. Review — resolved 2026-10-10

| # | question | resolution |
|---|---|---|
| 1 | §5 semantics match the reference docstrings exactly? | **Yes.** Checked line-by-line against `condition_model.py`, `rating_class.py`, `curve_atoms.py`, `thermal_metrics.md`, `mode_currents.py`. Three factual errors found and corrected in review: symbol cardinality 43 → **41**; rating gold 96 → **63** rows; typed-conditions 138 → **141** (84.4%). |
| 2 | Symbol table ordering accepted as frozen? | **Yes.** 41 rows verified against `_SYMBOLS`. Order is the first-occurrence rule (L4) and is not editorial. |
| 3 | Law → mechanism complete? | **Yes for L1–L10.** Gap found in review: the property tests did not name the empty-boundary asymmetries or the overlap-not-equality semantics of `_values_agree`. Both added to §5.2 and Gate N2. |
| 4 | Gate N4 (87K) blocking? | **Yes, blocking.** Gold covers 230 rows total; the corpus is the only test that catches regex-engine divergence in the tail. |
| 5 | Regex-engine risk accepted? | **Yes.** The answer is the ledger, not regex portability. A divergence is classified (D2), never silently re-specified. |
| 6 | Non-goals (§4) — anything wanted here? | **No additions.** Each non-goal has a named owner crate. |
| 7 | `mode_currents` — in scope here or in `dw-truth`? | **In scope here, as its own module**, because it shares the symbol/unit vocabulary and the fill-only law and produces the same `typed quantity + verbatim` shape. **But it has a cross-crate dependency:** the mode-word grammar (`_MODE_WORDS`, `_LP_MODE`) is owned by `dw-knives` (the phrase/vocabulary layer). `dw-normalize` consumes it; it must not own it. Recorded as the one permitted dependency outside `dw-types`. |

### Residual open items

None blocking. Two are named rather than decided, because they belong to later
PRDs:

* **`curve_type` vocabulary ownership** — 28 types, frozen and additive-only
  (`C6`). Whether the vocabulary lives in `dw-normalize` or `dw-knives` is a
  `dw-knives` PRD question. This crate consumes the enum either way.
* **87K corpus location** — still unresolved (see `DECISIONS.md` "not decided
  here"). Gate N4 cannot run until it is pinned.

---

## Appendix A — symbol table (frozen, order is semantic)

Ported verbatim from `harness/electronics/condition_model.py::_SYMBOLS`. **Do
not reorder. Do not reformat. The order is the first-occurrence rule.**

| # | regex | key | kind |
|---|---|---|---|
| 1 | `di F/dt` | `difdt_a_per_s` | `a_per_s` |
| 2 | `V_GS(on)` | `vgs_on_v` | `v` |
| 3 | `V_DS,peak` | `vds_peak_v` | `v` |
| 4 | `T_vj(start)` | `tvj_start_c` | `c` |
| 5 | `R_th|θ JA` | `rthja_c_per_w` | `c_per_w` |
| 6 | `R_G,ext` | `rg_ext_ohm` | `ohm` |
| 7 | `L σ` | `lsigma_h` | `h` |
| 8 | `STBY` | `stby_v` | `v` |
| 9 | `I_SINK` | `isink_a` | `a` |
| 10 | `I_SD` | `isd_a` | `a` |
| 11 | `I_REG` | `ireg_a` | `a` |
| 12 | `I_AS` | `i_as_a` | `a` |
| 13 | `I_OUT` | `iout_a` | `a` |
| 14 | `I_DS` | `id_a` | `a` |
| 15 | `I_O` | `iout_a` | `a` |
| 16 | `I_F` | `if_a` | `a` |
| 17 | `I_S` | `is_a` | `a` |
| 18 | `I_D` | `id_a` | `a` |
| 19 | `V_OUT` | `vout_v` | `v` |
| 20 | `V_IN` | `vin_v` | `v` |
| 21 | `V_CC` | `vcc_v` | `v` |
| 22 | `V_DD` | `vdd_v` | `v` |
| 23 | `V_EN` | `ven_v` | `v` |
| 24 | `V_SD` | `vsd_v` | `v` |
| 25 | `V_GS` | `vgs_v` | `v` |
| 26 | `V_DS` | `vds_v` | `v` |
| 27 | `VR` | `vr_v` | `v` |
| 28 | `VO` | `vout_v` | `v` |
| 29 | `T_STG` | `tstg_c` | `c` |
| 30 | `T_vj` | `tvj_c` | `c` |
| 31 | `T_A` | `ta_c` | `c` |
| 32 | `T_C` | `tc_c` | `c` |
| 33 | `T_J` | `tj_c` | `c` |
| 34 | `f_sw` | `f_hz` | `hz` |
| 35 | `f_osc` | `f_hz` | `hz` |
| 36 | `f_rr` | `frr_hz` | `hz` |
| 37 | `f` / `ƒ` | `f_hz` | `hz` |
| 38 | `R_GS` | `rgs_ohm` | `ohm` |
| 39 | `R_G` | `rg_ohm` | `ohm` |
| 40 | `L` | `l_h` | `h` |
| 41 | `R` | `r_ohm` | `ohm` |

## Appendix B — rejection classes

| class | fires on | never becomes |
|---|---|---|
| `Dash` | `^[\s\-–—―‒]+$` | a typed condition |
| `NoAnchors` | punctuation-only, or nothing recognised | a typed condition |
| `UnitsAbsent` | known symbol + bare number + no unit | a `Quantity` |
| `AmbiguousSymbols` | single-letter `V`/`I` + separator + number | a typed key |
