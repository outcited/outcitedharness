# HANDOFF — the Rust port

**Read this first if you are picking up the work cold.**

Everything below is measured, not assumed. If a claim here is wrong, the tests
will say so; if a claim here is right, you do not need to rediscover it.

---

## 0. Definition of done

Two gates, both must pass. `PLAN.md` §0 is the content gate; `DECISIONS.md` D7
is the delivery gate. Nothing else counts as done.

> **Content gate.** A human types one sentence about what they are building.
> The system returns a cohort, shows which constraints were derived and
> confirmed, names every exclusion with its evidence, lists what it could not
> evaluate, and suggests the next question that would narrow it most. **Every
> number opens to the page it came from.**
>
> **Delivery gate.** Every front end renders the experience from a single
> response — no second call, no guess. Seven surfaces: intent composer,
> constraint ledger, cohort explorer, evidence viewer, next-question rail,
> coverage/gap view, release lineage.

Not done: "the crates compile", "the tests pass", "the port is faithful". Those
are necessary and nowhere near sufficient.

---

## 1. Where things stand

```
designwins/
  Cargo.toml        workspace: forbid(unsafe_code), panic=abort, clippy pedantic -D
  deny.toml         allowlist-only; AGPL denied by omission (D1, PLAN §10)
  docs/CONTRACTS.md  Build first, then C1-C13, type-level enforcement
  docs/DECISIONS.md  D1-D7 ratified, plus the measured G7 spike
  docs/PRD-NORMALIZE.md  the normalization spec, reviewed
  docs/MIGRATION.md  port map, parity process, cutover/rollback
  docs/HANDOFF.md    this file
  crates/dw-types      law as types + 4 compile-fail proofs
  crates/dw-normalize  condition / coverage / rating / thermal / units
  crates/dw-truth      quote verification (the free adjudication layer)
  crates/dw-decide     Build / Atom / Cohort / evaluator
  crates/dw-knives     axes + phrase layer (D4/D5) — colloquial -> knives
  crates/dw-funnel     the composition root: accept(sentence) -> Build
  crates/dw-parity     D2 discrepancy ledger + corpus manifest
  crates/dw-pdf        PDFium engine + D1 G7 gate
scripts/
  freeze_corpus.py               content-addressed freeze, drift-detecting
  emit_pdf_engine_reference.py   MuPDF reference fixture for G7
  fetch_pdfium.sh                pinned PDFium 7881, checksum-verified
```

**33 Rust files, ~8,000 lines, 72 tests passing.** G7 is the one intentional
failure — it is a gate that blocks `dw-pdf`, not a regression.

Dependency graph is acyclic and each layer has one job:

```
dw-types -> dw-normalize -> dw-decide -> dw-knives -> dw-funnel
                |              |            |
            comparability   the funnel   colloquial -> knives
```

`dw-funnel::accept` is **the** entry point. The PLAN §0 sentence is now a
test (`acceptance_test_sentence_to_cohort`), not a paragraph.

### Gates

| gate | what | status |
|---|---|---|
| build / fmt / clippy pedantic / cargo-deny | whole workspace | **green** |
| N1a `condition_model_v1.jsonl` | 167/167 | **green** |
| N1b `rating_class_v1.jsonl` | 63/63 | **green** |
| N3 compile-fail | 4 laws | **green** |
| Week 0 parity | ledger runs empty, `cutover_permitted` | **green** |
| D1 G7 path segments | PDFium vs MuPDF | **OPEN** |

Run everything:

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd designwins
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo deny check
```

`~/.cargo/bin` is **not** on the shell PATH (brew's rustup left the shims
unlinked; the session created them). Export it or add it to `~/.zshrc`.

---

## 2. Definition of done is not yet met. Here is exactly what is missing.

Ranked by how much of the §0 sentence each one unlocks.

| # | missing | unlocks | notes |
|---|---|---|---|
| ~~1~~ | ~~`dw-knives` phrase layer (D4)~~ | ~~"types one sentence … returns a cohort"~~ | **done.** `phrase::bind` + `unknowns_from_residue`, seeded from `holes.json` with measured stamps |
| 1 | **question ranking by measured reduction** | "suggests the next question that would narrow it most" | currently ranks by `affected` count as an honest proxy. The real ranking is `harness/discovery/service.py::next_question` (1,881 parts / 86,736 claims verified) |
| 2 | **evidence deep links** | "every number opens to the page it came from" | `SourceRef`/`CropRef` exist (C13). Missing: content-addressed page render at 144 DPI + row-band crop |
| 3 | **evidence deep links** | "every number opens to the page it came from" | `SourceRef`/`CropRef` exist (C13). Missing: content-addressed page render at 144 DPI + row-band crop |
| 4 | **`dw-search` (D4)** | the front door | FTS5 + the binding layer. Eval is the query telemetry xlsx |
| 5 | **`dw-coverage`** | "lists what it could not evaluate" at corpus scale | coverage cube + gap ledger. `20260814_yellow_scorecard` is the measured substrate |
| 6 | **`dw-knives` axis registry** | generalising past 5 power axes | `evaluator::AXES` is 5 axes for all of electronics (QA finding F-03) |
| 7 | **Gate N4: 87K claim diff** | proof the port is faithful at corpus scale | **blocked**: corpus location unresolved. See §5 |
| 8 | **G7 closure** | `dw-pdf` unblocks | one Form XObject transform case + decomposition normalization |
| 9 | `dw-structure` / `dw-recon` / `dw-graph` | ingest side | Week 2-3 |
| 10 | `dw-extract` / `dw-queue` / `dw-release` / `dw-model` / `dw-learn` | plumbing | Week 3-5 |
| 11 | `dw-api` / `dw-controller` | D7 delivery gate | Week 5-6 |

**Do #1 first.** It is the difference between a text matcher and the product.

---

## 3. Ground truth — where the answers already are

Do not re-derive these. They are measured.

| thing | location |
|---|---|
| the plan | `PLAN.md` (Revision 3) |
| ratified laws | `designwins/docs/DECISIONS.md` — D1–D7 |
| contracts | `designwins/docs/CONTRACTS.md` — `Build` first, then C1–C13 |
| normalization spec | `designwins/docs/PRD-NORMALIZE.md` |
| Python reference, extraction side | `harness/electronics/*.py` (49 files) |
| Python reference, gate side | `harness/pipeline/{quote_verify,admission,ladder,crop_verify,ball_validate}.py` |
| **the funnel, verified** | `harness/discovery/` — 1,881 parts / 86,736 claims |
| condition gold (167) | `tests/fixtures/gold/condition_model_v1.jsonl` |
| rating gold (63) | `tests/fixtures/gold/rating_class_v1.jsonl` |
| curve gold | `tests/fixtures/gold/{curve_evidence_pilot,typical_curves}/` |
| corpus freeze | `tests/fixtures/gold/corpus_freeze.json` (32 members, sha-pinned) |
| PDF engine reference | `tests/fixtures/gold/pdf_engine_segments_v1.json` |
| **colloquial chips (D4)** | `/Volumes/M5_4TB/exports/cr_drops/20260813_mcu_yellow_occasion_holes/holes.json` |
| **four drawers ruling** | `/Volumes/M5_4TB/agent-inbox/cursor-cr/20260814T174540Z_*ti-subcategory-vs-application*.md` |
| knife coverage substrate | `/Volumes/M5_4TB/exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json` |
| query telemetry (search eval) | `/Users/samkim/Downloads/microcontroller_engineering_intent_analysis_with_complex_searches (1).xlsx` |
| catalog / pipeline DBs | `/Volumes/M5_4TB/extract-results/{catalog,pipeline}.db` |
| quote-native claims | `/Volumes/M5_4TB/exports/power-datasheet-pairs/*/pairs.jsonl` (9,201 rows today) |

**The NAS (`/Volumes/M5_4TB`) unmounts.** If the vault is missing, the G7 gate
and anything corpus-wide is blocked. The in-repo gold under
`tests/fixtures/gold/` is deliberately self-contained precisely so the gates
run without it.

---

## 4. What the gold caught — do not re-learn these

Each of these was a real bug that a gate caught. They are the reason the gates
exist.

### Porting gotchas

1. **Python's `Pattern.match(s, pos)` anchors at `pos`.** Rust's `regex`/`fancy_regex`
   `find()` searches anywhere. Separator and value scans silently produced zero
   matches until this was fixed. Anchor with `^`.
2. **Rust raw strings have no `\`-newline continuation.** Python does. The
   patterns contained literal backslash-newlines and did not compile.
3. **`fancy-regex` is required**, not optional: `_TEMP_UNIT` uses `(?![A-Za-z])`
   and `_BARE_TEMP` uses `(?<![\w.])`. The `regex` crate has no lookaround.
4. **`fancy_regex` API:** `find` not `search`, `captures` for named groups,
   `is_match` returns `Result`, `Match` has no `.name()`.
5. **A tokenizer must discard punctuation.** Python's `\d+|[a-z]+` never yields
   `-source` from `drain-source`. Mine did, and header binding silently failed.
6. **`#[serde(skip)]` needs `Default`** to fill the field on deserialize. An
   empty `Verbatim` is illegal, so the answer is to **not implement
   `Deserialize`** — the laws live in the constructor, and a JSON round-trip
   would route around them.
7. **Gold fixtures are not bit-exact on floats.** `15 * 1e-9` is
   `1.5000000000000002e-08` in *both* Python and Rust; the gold stores
   `1.5e-08`. Gate N1 compares structure exactly and numerics with a tight
   relative tolerance. Bit-exactness would test the serialiser, not the port.

### Semantics the gold corrected

8. **`table_kind` is NOT consulted for rating class.** The class is decided by
   the printed qualifier + symbol vocabulary + the word "operating". `None`
   qualifier ⇒ fail-closed `unknown`. My table-driven rules were invention;
   `unknown` went 0/17 → 17/17 once the rule was read correctly.
9. **Pulse/surge vocabulary fires wherever the symbol text is printed**, not
   only in the symbol cell. Gold row 39 carries `E*3 AS` in `parameter` with an
   empty symbol. `transient` went 5/6 → 6/6.
10. **`_residue` splits chunks on `[,;]+` and drops punctuation-only parts.**
    Separators are not evidence — which is why `"VGS = 0 V, VDS = 25 V"` yields
    *no* residue.
11. **`condition_covers` value agreement is range-overlap, not equality.**
    And there are two deliberate asymmetries at the empty boundary:
    `covers(claim, {}) == Exact`, `covers({}, req) == Absent`.
12. **Dash placeholders defeat the ordinal wrong-cell guard.**
    `"VDS - - 600 V"` yields one number at ordinal 0 while `max` is column 2,
    so the guard reports a provable mismatch and the claim is P0 even though
    the value really is in the Max column. That is the reference's behaviour,
    ported as-is, with its own test documenting the limitation. Do not silently
    "fix" it — change it on both sides and let the gold decide.

### Design laws that are load-bearing

13. **A `Pending` derived constraint contributes nothing** until confirmed.
    That is what makes "derived is never silent" mechanical rather than a
    review convention.
14. **An `absmax` rating yields `Unknown`, not `Fail`.** It is the wrong
    physical quantity and cannot be compared at all. The part may be fine; we
    simply have not established it. Treating it as `Fail` would wrongly
    eliminate survivors.
15. **Margin is applied to the requirement**, never the claim, and is never
    silent. `Margin` has no `Default`.

---

## 5. Open items and blockers

| item | state | note |
|---|---|---|
| **87K corpus location** | **blocker for N4** | Candidate locations: `/Volumes/M5_4TB/exports/power-datasheet-pairs/{ti,rohm,infineon}-20260908/` (9,201 rows) and `results/lora-v1-pairs/`. Neither matches "2,713 v1 / 87K v2". Must be pinned before N4 can run. |
| G7 residual | open | Form XObject: pdfium bakes the parent transform into enumerated children, so composing the form matrix double-applies it. Recursing with the *parent* matrix takes `msp430fr6005` p29 to exact drawing count (208 = 208). `msp430g2744` p15 still shows a y-distribution mismatch. Then decomposition normalization (`re`→4 corners, Bézier triples) to ≥99% vertex parity. |
| git working tree | 35 pre-existing uncommitted changes on `curve/07e-evidence-supply` | **not ours.** Do not commit them. Our work is committed (12 commits this session). |
| `~/.cargo/bin` on PATH | not done | one line in `~/.zshrc`, needs the user's word |
| vendor priors content | decided (D6) | derived from `dw-coverage`, never authored. Not yet implemented. |
| `dw-queue` lease semantics | open | needs durations and policy |
| C7 request/response schemas | open | gated by D7; specify per-surface, not per-endpoint |

---

## 6. Working agreements

- **The gold is the spec.** When the port and the reference disagree, the
  reference is right until a `DECISIONS.md` entry says otherwise. Record the
  divergence in the parity ledger (`dw-parity`, D2). Never silently
  re-specify.
- **Every change ships with its gate.** A crate without a gold gate or a
  compile-fail proof is not done.
- **Commit only tested work.** Clean checkins. Do not stage files you did not
  write.
- **`clippy::pedantic` is on for library code.** Test modules carry a
  documented `#[allow]` block for the restrictions that are noise there
  (indexing, `expect`, single-variant wildcards). Do not weaken the library
  lint.
- **Regex construction lives in one place** per crate
  (`dw_normalize::re::build`), with a documented `#[allow(clippy::expect_used)]`
  — a malformed constant regex must fail loudly, because silently losing a
  symbol is exactly the fail-open the crate exists to prevent.
- **`parse_condition` and `verify_claims` are long on purpose.** The order of
  checks *is* the ladder. Splitting them breaks the line-by-line mapping to the
  Python that makes the parity reviewable. Both carry a documented
  `#[allow(clippy::too_many_lines)]`.
- **Do not invent.** If a rule is not in the gold or the reference docstring,
  it is not a rule. Write it in the PRD and get it ratified first.

---

## 7. Suggested next session

1. **Question ranking.** Port `harness/discovery/service.py::next_question`
   (expected cohort reduction) into `dw-decide`. It is verified over 1,881
   parts / 86,736 claims — port it, do not re-derive it.
2. **Gate the phrase layer against the query telemetry xlsx**, weighted by
   search count. 1,485 query rows / 703 unique strings / 8,653 searches. The
   `Theme` column is the labelled category tier.
3. **Evidence deep links.** Content-addressed page render at 144 DPI +
   row-band crop (`C13`), so "every number opens to the page it came from" is
   literally true in a browser.
4. Then #3–#11 in §2 above.

The acceptance test is one sentence long. Everything on that list is in
service of it.
