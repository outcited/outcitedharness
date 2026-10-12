# HANDOFF — the Rust port

**Read this first if you are picking up cold.** This document plus
`PROVENANCE.md` and `ASSURANCE.md` are the entire handoff. Everything below is
measured, not assumed — if a claim here is wrong the tests will say so; if it
is right you do not need to rediscover it.

---

## 0. Start here — prove the workspace is green

```bash
export PATH="$HOME/.cargo/bin:$PATH"   # brew rustup leaves shims unlinked
cd designwins
cargo fmt --check && echo "FMT OK"
cargo clippy --workspace --all-targets -- -D warnings && echo "CLIPPY OK"
cargo test --workspace --no-fail-fast 2>&1 | grep "test result"
cargo deny check
```

**Green looks like:** `FMT OK` · `CLIPPY OK` · **20 test binaries, 196 tests,
all passing** · `cargo deny` reporting `advisories ok, bans ok, licenses ok,
sources ok`. There is **no expected failure** — the G7 gate went green
2026-10-11 (§5.4).

**`--no-fail-fast` stays in the smoke anyway.** Cargo aborts at the first
failing target, so one red test hides every target after it — that is how a
gate failing by design once hid 4 binaries / 37 tests from the count. The
old smoke line said "16 binaries, 106 tests": a truncated floor, not the
inventory. A green truncated run is not a green workspace.

If it is not green, fix that first. Nothing below means anything on a red build.

### Orientation

| thing | answer |
|---|---|
| repo root | `/Users/samkim/Harnessv1` (a Python factory being ported) |
| Rust workspace | `designwins/` |
| **entry point** | `dw_funnel::accept(sentence, registry, cohort, facts, release)` → `Build` |
| **replacement proof** | `dw-parity --bin dw-replace --mode <condition\|quote>` — both engines live, one run |
| dependency graph | `dw-types → dw-normalize → dw-decide → dw-knives → dw-funnel`, acyclic. `dw-truth` / `dw-evidence` / `dw-release` / `dw-parity` / `dw-pdf` alongside |
| toolchain | `rustc 1.98.1`, `cargo-deny 0.20.2` |
| lint bar | `clippy::pedantic` at `-D warnings` for library code. Test modules carry a documented `#[allow]`. **Do not weaken the library lint** |

### Do not touch

`git status` shows **uncommitted changes that are not ours** (21 at handoff,
the count moves) — modified `harness/*.py`, `scripts/*.py`, `tests/*.py`, root
`.md` files, all predating this work on `curve/07e-evidence-supply`. **Do not
stage, commit, revert, or clean them.** Ours is committed: `designwins/`,
`scripts/{emit_pdf_engine_reference,fetch_pdfium,freeze_corpus,locate_claim_pages,build_evidence,prepare_quote_parity,
parity_reference}.py`, `tests/fixtures/gold/*`, `PLAN.md`. Trust `git status`,
not this number.

---

## 1. Definition of done — three gates

| gate | source | state |
|---|---|---|
| **content** | `PLAN.md` §0 — sentence → cohort + derived + exclusions + unknowns + next + every number opens to the page | **met** — executable, asserted clause-by-clause in `dw-funnel::accept` |
| **delivery** | `DECISIONS.md` D7 — every front end renders from one response | **met 2026-10-11** — `dw-api`: 7 routes, feed items 1–7 inline on every response, audited post-serialisation with negative controls (`dw-serve` on `127.0.0.1:8787`). Fixture-backed store; catalog slots behind `ApiStore` |
| **trust** | `PROVENANCE.md` §2 — evidence resolves, releases resolve, unresolvable ⇒ `Unknown` | **met 2026-10-11** — links 1–8 real/measured, both replacement ledgers empty, G7 green. Residuals (99.6% page location, 8.5% binding coverage) are named, not hidden |
| **board** | `PLAN.md` §0.1 — every category at its gold bar, movement visible | **NOT met — this is the migration's real bar** (merged 2026-10-12). `results/api-coverage/BOARD.md` (run `scripts/extraction_board.py`): MCU pillars 58.5% / pinout 9.8%; POWER thermal 2.9%; CONNECTOR 100% `connectors` but 0% parametrics. Totals lie; Δ is the marker |

**Three gates pass as of 2026-10-11; the board gate (§0.1 of the plan) is
the one that closes the migration.** The trust gate is what lets an engineer
stake a design on the answer; the board is what says the product can be
sold, category by category.

### PLAN §0 clause by clause

| clause | state |
|---|---|
| types one sentence | ✓ `dw-funnel::accept` |
| returns a cohort of distinct parts | ✓ `dw_decide::Cohort` |
| constraints derived **and confirmed** | ✓ `Derived` + `ConfirmState`, `Pending` contributes nothing |
| every exclusion with its evidence | ✓ `Ineligible`, unconstructible without evidence |
| lists what it could not evaluate | ✓ `MissingAxis` first-class |
| next question that narrows most | ⚠ ranked by `affected` proxy, not measured reduction |
| **every number opens to the page** | ✓ **86,398 / 86,736 located (99.6%)**, evidence rendered |

---

## 2. The replacement proof — this is the headline

`dw-parity --bin dw-replace` invokes **both engines live, on the same inputs,
in the same run**. Reference is a subprocess (`scripts/parity_reference.py`),
candidate is in-process, neither shares state, only output is compared, and
both engine identities are recorded.

### `condition_model.parse_condition` — **PROVEN**

```
python:harness.electronics.condition_model.parse_condition
  vs rust:dw_normalize::parse_condition
  subjects  10270   agreements 10270   discrepancies 0   cutover_permitted yes
```

### `quote_verify.verify_claims` — **PROVEN**

```
python:harness.pipeline.quote_verify.verify_claims
  vs rust:dw_truth::verify_claims
  subjects  40   agreements  40   discrepancies  0   cutover_permitted yes
```

All 40 cases (9,890 claims, 2.13M chars) agree. The `header_binds` divergence
was never in `header_binds` — root cause closed in §5.1.

### Why this is stronger than the gold gates

`N1` / `N4a` / `N4b` compare Rust against fixtures **generated from Python at an
earlier moment**. They prove parity against a snapshot and cannot catch a change
on either side the snapshot predates. The replacement gate closes that.

Run it:

```bash
cd designwins
cargo run -q -p dw-parity --bin dw-replace -- \
  --mode condition --corpus ../tests/fixtures/gold/claim_conditions_v1.jsonl \
  --field condition_verbatim
cargo run -q -p dw-parity --bin dw-replace -- \
  --mode quote --corpus ../tests/fixtures/gold/quote_parity/quote_parity_cases.jsonl \
  --field case_path --out /tmp/qled.json
```

---

## 3. Gate inventory

| gate | substrate | bar | state |
|---|---|---|---|
| build / fmt / clippy pedantic / deny | workspace | green | **green** |
| N1a `condition_model_v1.jsonl` | 167 curated | 167/167 | **green** |
| N1b `rating_class_v1.jsonl` | 63 curated | 63/63 | **green** |
| N2 property tests | units · coverage · margin | pass | **green** |
| N3 compile-fail | 4 laws | 4/4 | **green** |
| N4a `condition_typed_catalog_v1.jsonl` | 9,551 from `catalog.db` | 9,551/9,551 | **green** |
| N4b `claim_conditions_v1.jsonl` | 10,270 conditions / 64,245 claims | 10,270/10,270 | **green** |
| C3/C13 provenance | 86,736 claims | quote+sha 100%, **page 0 → 99.6%** | **green** |
| D4 phrase telemetry | 7,423 weighted searches | floor 632 bound / 625 `ProvenFit` | **green** |
| Week 0 parity | ledger | empty | **green** |
| trust gate | links 1–6 | real | **green** |
| **replacement: condition** | 10,270 live | 0 diffs | **green** |
| **replacement: quote** | 40 cases live (9,890 claims) | 0 diffs | **green** |
| **D7 feed contract** | 7 routes live (`dw-serve`, `127.0.0.1:8787`) | items 1–7 inline, audited post-serialisation | **green** |
| **question ranking** | `next_question` port, live-reference gold | 3/3 + 2 negative controls | **green** |
| **dw-lessons** | lesson index + proposal-gate `check` | separation measured, controls trip | **green** |
| D1 G7 path segments | PDFium vs PyMuPDF | vertex parity ≥99% | **green 2026-10-11** — 100% on both pages (§5.4) |

---

## 4. Storage — three surfaces, know which is which

| surface | path | what it is | use |
|---|---|---|---|
| **yosemiteraid5** | `/Users/samkim/mnt/yosemiteraid5/{Public,Home}` | **local NVMe, fast, WRITABLE.** `nobrowse`, so hidden from `/Volumes` | the work target. Evidence store lives at `Public/m5-opencode/evidence/` |
| **M5_4TB** | `/Volumes/M5_4TB` → symlink to `Electronicdataassets/m5-4tb` | SMB `//harness@charleybasecamp.local`, spinning disk. **Unmounts** | read-only source. Never a store target (PLAN §10) |
| **Electronicdataassets** | `/Users/samkim/Volumes/Electronicdataassets` | SMB mirror + `backups/m5/` | cross-checks |

**SQLite on these mounts needs `?mode=ro&immutable=1`.** Plain `connect()`
fails with "unable to open database file" because SQLite wants journal space.
`catalog.db` copies to `yosemiteraid5` in ~2.7 s (58 MB) and reads fine there —
that copy is at `…/m5-opencode/scratch/catalog.db`.

**`catalog.db` is the substrate and it is all readable:**

| table | rows | use |
|---|---:|---|
| `claims_canonical` | **86,736** | the "87K" of PLAN §12 |
| `conditions_typed` | 9,551 | N4a oracle |
| `consensus` | 107,033 | resolution trail |
| `parts` / `part_sources` | 1,882 | matches PLAN's discovery count |

**Still denied:** `designwins-v{1,2,3}/canonical/**` — 18 SFT pair files on
`harness-training/datasets/`. Not blocking: the *claims* are open.

---

## 5. OPEN WORK — in priority order

### 5.1 `header_binds` divergence — **CLOSED 2026-10-11**

Fixed and proven: the quote gate is **40/40, 0 discrepancies**. The bug was
never in `header_binds`:

**`str::find` returns a byte offset; the reference slices by code point.**
`quote.rs` built its `±400/±200` span windows in bytes and then
`t_normed.get(start..end).unwrap_or(&t_normed)` — whenever a window edge
landed mid-character (extracted datasheet text is dense with `µ`, `©`, `℃`,
`Ω`, `–`…), `get` returned `None` and the fallback **silently widened the span
to the whole document**. Every header token then hit, so `unbound_header`
became `SUPPORTED` — the 90× `REVIEW→SUPPORTED` class. When `get` succeeded
the window still covered the wrong region (bytes ≠ code points). Same bug in
`token_window_match`'s `±number_window` span. Both sites now use
`char_index`/`char_slice`, each with a negative control that fails on the old
code (`multibyte_window_edge_does_not_widen_span`,
`token_window_edge_does_not_widen_span`).

**Suspects that are NOT the bug — do not re-investigate:**

- `norm(quote)[:40]` prefix collision is real but **reference-shared**:
  identical on both engines, so not a porting divergence. (It does mislocate
  short quotes — `"1.0"` finds the first `1.0` anywhere in the doc — a
  reference limitation to fix on both sides one day.)
- **context-dependence is dead.** 483/483 claims verified isolated vs
  full-map give identical verdicts on the reference; the earlier observation
  was an experimental artifact. `verify_claims` is purely per-claim.
- The "221 diffs" figure was stale (pre-`value_as_f64` fix); the real
  post-fix count was 170, now **0**.

**Latent divergences, not firing on the 40-case corpus** (recorded so they
are not lost; each needs its own negative control when fixed):
`dw_truth::norm` does **not** NFKC-normalise although its doc comment claims
it (`let folded = s;` is a no-op); `find_quote` lacks the reference's
`norm_m4.locates` fallback layer; `value_in_quote`/`column_position_binds`
split numbers differently from the reference's `re.findall(r"-?\d+…")` on
inputs like `"1.5-3"`; `verify_claims` skip semantics differ for
`value: null` claims with a quote (reference skips, port verifies).

### 5.2 `dw-api` — the D7 delivery gate — **DONE 2026-10-11**

Seven routes, feed items 1–7 inline on every response, `problem+json`
renderable errors, evidence assets served as pixels. The feed audit runs on
the **serialised** body with negative controls (strip a block → fail; break
an evidence ref → fail; unreason an exclusion → fail). `dw-serve` binds
`127.0.0.1:8787`, `Bearer crk_…`, no new dependencies. Data is
fixture-backed (`DemoStore`); a catalog store slots behind `ApiStore` without
touching a route.

Found and fixed on the way: `dw-decide`'s wire shape did not round-trip
(`skip_serializing_if` without `serde(default)` — third instance of the §6
class); `dw-serve`'s `read_head` consumed past the header terminator, so
bodies arriving with the headers hung `read_exact`.

### 5.3 Question ranking by measured reduction — **DONE 2026-10-11**

Ported from `harness/discovery/service.py::next_question`, not re-derived:
median probe per unpinned registry axis, `Unknown` never eliminates, rank by
absolute expected fails (stable — registry order breaks ties), help prose
verbatim. The gold is generated **from the live reference**
(`scripts/gold_question_ranking.py`); the typed port is asserted field for
field against it, with negative controls (measured fails not population size;
ties keep registry order; `covers` probes never `FAIL` on a single bound).
`dw_funnel::accept` leads its rail with the measured list; the D5 residue
questions follow, still ranked by the `affected` proxy.

### 5.4 G7 residual — **CLOSED 2026-10-11**

`g7_path_segments_match_python_reference` is green: **100% vertex parity on
both pages**, segments within 0.4–1.9%, geometry exact. Three real bugs,
none of them where §5.4 originally pointed:

1. **Matrix composition was backwards.** pdfium-render's `multiply(&self,
   other)` applies *self first, then other*; the code composed
   `parent.multiply(m)`, inverting the chain. The old "pdfium bakes the form
   matrix / composing double-applies it" reading was this bug's shadow — the
   form's placement matrix (measured: `msp430fr6005` p29's form sits at
   `(65.83, 487.10)`) was simply never applied. This was the whole
   "y-distribution mismatch" on `msp430g2744` p15.
2. **Items were raw path segments, not `MuPDF` items.** pdfium flattens to
   `move`/`line`/`bezier`-triples; `MuPDF` reports `"l"` (2 pts), `"c"`
   (4 pts), `"re"`/`"qu"` (one four-corner item). The regroup is now done
   (`re` → 4 corners, Bézier triples), with unit controls.
3. **Rect collapse carries the outline vertex multiset.** pdfium's flattened
   stream **cannot** distinguish a content-stream `re` from a manually drawn
   four-line box (measured at full precision: both are `move`+4 `line`s,
   exactly axis-aligned, no close). The collapse is geometric and documented;
   a collapsed item lists its four corners **twice** — the vertices four
   stroke edges would contribute — so the reference's multiset (which
   duplicates corners for `"l"`-itemized boxes) always matches.

Not gated, stated anyway: `msp430g2744` p15 enumerates 117 drawings where
`MuPDF` reports 113 (4 duplicated fills). The bars are vertex- and
segment-directed so this does not fail the gate; if item-count parity ever
becomes a bar, that is the next investigation.

### 5.5 Everything else

`dw-search` (D4 front door) · `dw-coverage` (cube + gap ledger) ·
`dw-knives` axis registry generalisation (F-03: 5 axes for all of
electronics) · `dw-structure` / `dw-recon` / `dw-graph` · `dw-extract` /
`dw-queue` / `dw-model` / `dw-learn` · `dw-controller` · cutover.

---

## 6. WHAT THE GATES CAUGHT — do not re-learn these

**Fifteen-plus instances of one class: an assertion or a rule that could not
fail on the property it claimed to test.** `ASSURANCE.md` exists because of
them. Read it before writing a check.

### The three mechanisms

1. **The assertion shares the code's blind spot.** `get("page").is_some()` is
   true for a present-but-`null` field — I reported "86,736/86,736 carry quote
   + sha + page" from a test I had just authored. Truth: **0 pages.**
2. **The bar is a floor and floors do not discriminate.** `coverage >= 0.5`
   passed at **131.2%** because OPNs were summed across family documents
   instead of unioned. `with_quote >= 90%` passed at 90% and I reported 100%.
3. **Reported numbers came from assertions, not queries.** `PRD-NORMALIZE`
   opened with three wrong cardinalities (43 vs 41 symbols, 96 vs 63 rating
   rows, 138 vs 141 typed) — all recalled, none counted.

### Porting gotchas (each cost real time)

| gotcha | why it bit |
|---|---|
| Python `Pattern.match(s, pos)` **anchors at `pos`** | Rust `find()` searches anywhere. Separator and value scans returned **zero matches** on well-formed input |
| Rust raw strings have **no `\`-newline continuation** | Python does. Patterns contained literal backslash-newlines |
| `fancy-regex` is **required** | `_TEMP_UNIT` uses `(?![A-Za-z])`, `_BARE_TEMP` uses `(?<![\w.])`. The `regex` crate has no lookaround |
| `fancy_regex` API | `find` not `search`, `captures` for named groups, `is_match` → `Result`, `Match` has no `.name()` |
| a tokenizer must **discard punctuation** | `\d+\|[a-z]+` never yields `-source` from `drain-source`. Mine did, and header binding silently failed |
| `#[serde(skip)]` needs `serde(default)` on the way in | bit `Verbatim` (which is why `Deserialize` is deliberately absent there) and `ReleaseManifest` |
| **gold fixtures are not bit-exact on floats** | `15*1e-9` is `1.5000000000000002e-08` in **both** Python and Rust; the gold stores `1.5e-08`. Gate N1 compares structure exactly, numerics with tolerance |
| **claim `value` is a string in the corpus** | `"0.5"`, not `0.5`. `Value::as_f64()` returns `None` for a string and silently skipped the ordinal wrong-cell guard |
| reference wire shape is **asymmetric** | `reason_code` emitted as `null`; `evidence_quote` omitted when absent |
| array order is not semantic for keyed records | reference emits claim order, `BTreeMap` emits sorted |
| **`str::find` is byte-indexed; Python `str.find` is code-point** | spans built in bytes cover the wrong region — and `get(a..b).unwrap_or(&s)` then **silently widens to the whole document** when an edge lands mid-char. Cost: the entire `header_binds` divergence. See §5.1 |
| **`cargo test` truncates at the first failing target** | the expected-failing G7 gate hid 4 binaries / 37 tests from the smoke count. "16 binaries, 106 tests" was a floor. **`--no-fail-fast` is part of the smoke** |

### Semantics the gold corrected

- **`table_kind` is NOT consulted for rating class.** Qualifier + symbol
  vocabulary + the word "operating" decide it. `None` ⇒ fail-closed `unknown`.
  `unknown` went 0/17 → 17/17.
- **Pulse/surge vocabulary fires wherever the symbol text is printed**, not
  only in the symbol cell. Gold row 39 carries `E*3 AS` in `parameter`.
- **The curated gold was stale on comparator order.** The live reference emits
  symbol-table order; the fixture had document order. I had bent the *code*
  toward the fixture. See `DECISIONS.md` **D8** — *a fixture is a sample of the
  reference, not the reference.*
- **`value_regex` needed `(?i)`** because `condition_norm` is lowercase-
  normalized. Dropping it broke 81% of the real corpus while N1 stayed green.

### Design laws that are load-bearing

- **A `Pending` derived constraint contributes nothing** until confirmed.
- **An `absmax` rating yields `Unknown`, not `Fail`.** Wrong physical quantity;
  the part may be fine, we just have not established it.
- **Margin is applied to the requirement**, never the claim, and never silently.
- **A constraint that cannot fail is not a constraint.** Boolean atoms need a
  threshold (`MinRating >= 1.0`) or they can never narrow — which made the
  phrase layer decorative.
- **Unresolvable evidence is `Unknown`.** `Provenance` is a parameter of
  `dw_decide::judge`, not a caller-side check, because a check at the call site
  is a check that gets skipped.

---

## 7. Ground truth — where the answers already are

| thing | location |
|---|---|
| the plan | `PLAN.md` (Revision 3) — **body partly stale**, read §19 Corrections at the bottom |
| ratified laws | `designwins/docs/DECISIONS.md` — **D1–D9** |
| contracts | `designwins/docs/CONTRACTS.md` — `Build` first, then C1–C13 |
| normalization spec | `designwins/docs/PRD-NORMALIZE.md` |
| phrase/axis spec | `designwins/docs/PRD-PHRASE.md` |
| trust model | `designwins/docs/PROVENANCE.md` |
| check discipline | `designwins/docs/ASSURANCE.md` — **read before writing a test** |
| Python reference, gate side | `harness/pipeline/{quote_verify,admission,ladder,crop_verify,ball_validate}.py` |
| Python reference, extraction | `harness/electronics/*.py` (49 files) |
| **the funnel, verified** | `harness/discovery/` — 1,881 parts / 86,736 claims |
| condition gold (167) | `tests/fixtures/gold/condition_model_v1.jsonl` |
| rating gold (63) | `tests/fixtures/gold/rating_class_v1.jsonl` |
| conditions corpus (9,551) | `tests/fixtures/gold/condition_typed_catalog_v1.jsonl` |
| claim conditions (10,270) | `tests/fixtures/gold/claim_conditions_v1.jsonl` |
| claim provenance (86,736) | `tests/fixtures/gold/claim_provenance_v2.jsonl` |
| located pages | `tests/fixtures/gold/claim_pages_v1.jsonl` |
| quote parity cases | `tests/fixtures/gold/quote_parity/` |
| knife coverage | `tests/fixtures/gold/knife_coverage_v1.json` |
| category graph | `tests/fixtures/gold/category_graph_v1.json` |
| search telemetry | `tests/fixtures/gold/search_telemetry_v1.jsonl` |
| corpus freeze | `tests/fixtures/gold/corpus_freeze.json` |
| **colloquial chips (D4)** | `…/exports/cr_drops/20260813_mcu_yellow_occasion_holes/holes.json` |
| **four-drawers ruling** | `…/agent-inbox/cursor-cr/20260814T174540Z_*ti-subcategory-vs-application*.md` |
| knife coverage substrate | `…/exports/cr_drops/20260814_yellow_scorecard/2026-08-14.json` |
| evidence store | `…/m5-opencode/evidence/{pages,crops}/` |
| agent mail | `…/Electronicdataassets/agent-inbox/` + `INDEX.jsonl` |

---

## 8. Agent network — I am `m5-opencode`

Protocol: `/Volumes/M5_4TB/AGENT_NETWORK.md` + `MAIL_ONBOARDING_m5-opencode.md`.
Canonical inbox is **`Electronicdataassets/agent-inbox`** (migrated 2026-10-08);
old trees are frozen. Send with
`repos/outcited-ai-sandbox-20260822/scripts/orchestration/send-message.ts`
(its `node_modules` is currently broken on the NAS path — write the file
direct-on-tree with correct YAML frontmatter and append to `INDEX.jsonl`).
**Stamp `agent-inbox/m5-opencode/.heartbeat` whenever you read mail.**

Correspondents: `cursor-cr`/`cr-core` (ingest, publish, gold) · `cursor-tapes`
(embedder + ontology) · `claude-code-browser` (+ `-m4`) (crawlers) ·
`mac-mini` (NAS, fleet, **Hermes advisor**) · `m4opencode` (QA) · `samson`.

**Sent this session:**

| to | subject | state |
|---|---|---|
| `mac-mini` | `hermes-extraction-position-gold-set-and-lessons-20261011` | sent — endorsed the proposal gate, corrected the gold set (86,736 claims not 200 docs), argued experiment one is model×document-class, offered `dw-lessons` |
| `cursor-tapes` | `embedder-coordination-open-plus-lessons-20261011` | sent — three asks from 2026-10-09 still open + lessons-learned request |

**Open inbound:** `mac-mini` offered (a) a 200-doc pinout gold-set
collaboration and (b) extraction-scoped advisor cycles. `cursor-tapes` has not
answered the embedder asks since 2026-10-09. `mac-mini` 2026-10-10 said
*"Tapes_4TB gone — vault is NAS-copy only, relocation pending Sam"* — that was
the permission break, since fixed.

---

## 9. The Hermes question — my position as lead

`mac-mini` has **already built it** (2026-10-11, commit `2e02aaf`, live in
shadow): proposal gate with a DB **trigger forcing `applied=false`** on any
non-baseline source, a `tp4-flash` advisor (DeepSeek 4.1-flash, 1M ctx, tools +
vision, free), Hermes 0.19.0 on the mini, and `mfg orch-replay` 48 h
counterfactual scoring.

**I endorse it.** The trigger is enforcement by construction, not by intention
— the same move as a compile-fail test. And their rule is ours:

> *"the model that generates the answer never grades it. Your Rust/verifier
> side stays the authority on gold promotion."*

**Three positions I put in writing:**

1. **The gold set is 86,736 claims with 99.6% located and hash-verified
   evidence**, not 200 docs. `held_out_ref` should be a claim with a quote, a
   page, a crop hash, and a `ReleaseId`.
2. **Experiment one is model × document-class, not DPI.** Four vision models
   and one is free (DeepSeek local). Free changes the search: cover the matrix,
   don't tune one cell. `MODEL_SETTINGS.md` already shows MiMo-flash and
   DeepSeek-TP4 **tie** on series-count and axis-key and **split** on point
   error and axis-label verbatim — the class decides.
3. **The fourth question has no home.** Their loop is *why → what → did it work
   → how do we preserve the learning*. The first three have a home; the fourth
   does not. **I own `dw-lessons`** — a queryable, git-versioned index over
   `agent-inbox/` + `DECISIONS.md` + `ASSURANCE.md` + the experiments ledger,
   sitting **in front of** the proposal gate so no experiment can be proposed
   without first being checked against known lessons. This session I fumbled SSH
   for an hour on a lesson sitting in my own inbox since 2026-10-08.

**Added a fifth rule to their four:** every promoted change ships with a
**negative control** — a case where the old behaviour was wrong and the new one
catches it. Their `docs/burn 0.972 vs 0.934` is trustworthy because
`wrong-harvests 12 vs 25` is the negative case.

**Boundary:** keep the advisor on **extraction quality only**, off the
claim/admission path until `dw-api` lands. Optimising a surface that is still
moving measures nothing.

**Success metric: not accuracy — caught classes graduating to deterministic
validators.** `microchip:diode → needs_human` (2,950 attempts / 2,938
`permanent_dead` refusals) is a walled-refusal class both advisors converged on
independently on cycle one. The right output is a free permanent validator, not
a better prompt. That is PLAN §9: *a caught class never becomes model-judgment
again.*

---

## 10. What I need from the human

1. **Nothing blocking right now.** The vault permission fix landed and the
   corpus is readable. The 18 SFT pair files are still denied and are **not**
   blocking — the claims are open.
2. **`~/.cargo/bin` on PATH** would save an `export` per command.
3. **The 87K pair-shape question is closed** — `claims_canonical` (86,736) is
   the corpus PLAN §12 means, and N4a/N4b run against it.

---

## 11. Next — the roadmap, in order

**The migration is not done until the board is** (`PLAN.md` §0.1). The work
is the lanes, in order: **MCU → POWER → CONNECTOR** (and the other
active/passive categories), pushing each to its gold bar so the Δ column
moves. The machine around them is built: PDF factory (mac-mini) feeds the
extraction lanes (this box); `dw-api` serves with provenance; m4 renders and
reports gaps.

Infrastructure already done: question ranking (§5.3), `dw-lessons`, G7
(§5.4), `dw-api` (§5.2), `dw-search`, `dw-coverage`, `dw-graph` (ordering +
topology + errata), the axis registry + rating projection, the
`api-store-export` loader + the strata fold (5,835 cr-core parts served),
`dw-queue` (the loop's engine, gold vs the live Python store), and
`dw-recon::document_scope` (gold vs the live reader). Remaining §5.5:
`dw-recon` family modules (census/document/matrix/corpus — 3.7k lines),
`dw-structure` (needs the PDF text layer), `dw-model`/`dw-learn`,
`dw-controller`, cutover.

### Ops notes (2026-10-12, learned the hard way)

- **NFS (`electronicdataassets-nfs` / `nfs_charley`) is a read-only
  mirror** — every write denies, even at the share root. Reads are the
  fast path (5.7×). Writes go local (`PIPELINE_DB=…/results/…`,
  `BURN_OUT=…`) and stage to the NAS later.
- **SSH to charleybasecamp is live** (fleet key `~/.ssh/id_ed25519_nas`);
  sshd reads `authorized_keys` as the user — never chown it root. Use
  `ssh -F /dev/null` when the colima include chain trips on NFS uids.
- The corpus vendor dirs needed `chmod -R o+rwX` server-side (they were
  700 while SMB connected as a different identity).

The acceptance test is one sentence long. Everything on that list is in service
of it.
