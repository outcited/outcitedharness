# HANDOFF — the Rust port

**Read this first if you are picking up the work cold.** This document and
`PROVENANCE.md` are the entire handoff. Everything below is measured, not
assumed — if a claim here is wrong, the tests will say so; if it is right, you
do not need to rediscover it.

---

## 0. Start here: prove the workspace is green

```bash
export PATH="$HOME/.cargo/bin:$PATH"     # brew rustup leaves the shims unlinked
cd designwins
cargo fmt --check && echo "FMT OK"
cargo clippy --workspace --all-targets -- -D warnings && echo "CLIPPY OK"
cargo test --workspace 2>&1 | grep "test result"
cargo deny check
```

**What green looks like:** `FMT OK`, `CLIPPY OK`, **eleven `test result` lines —
ten `ok` totalling 82 tests, plus one `FAILED`** — and `cargo deny` reporting
`advisories ok, bans ok, licenses ok, sources ok`.

**The one `FAILED` is `g7_path_segments_match_python_reference`.** That is a
*gate*, not a regression — it blocks `dw-pdf` until the residual in §2 is
closed. It is expected and correct. Any other failure is a real problem.

If the workspace is not green, **stop and fix that first**. Nothing below is
meaningful on a red build.

### Orientation

| thing | answer |
|---|---|
| repo root | `/Users/samkim/Harnessv1` (a Python factory being ported) |
| Rust workspace | `designwins/` |
| entry point | `dw_funnel::accept(sentence, registry, cohort, facts, release)` |
| dependency graph | `dw-types → dw-normalize → dw-decide → dw-knives → dw-funnel`, with `dw-truth` / `dw-evidence` / `dw-release` / `dw-parity` / `dw-pdf` alongside. **Acyclic** — do not create a cycle; `dw-funnel` exists precisely to avoid one |
| toolchain | `rustc 1.98.1`, `cargo-deny 0.20.2` |
| lint bar | `clippy::pedantic` at `-D warnings` for library code. Test modules carry a documented `#[allow]`. **Do not weaken the library lint** |

### Do not touch

`git status` shows **~36 uncommitted changes that are not ours** — modified
`harness/*.py`, `scripts/*.py`, `tests/*.py`, and several root `.md` files, all
predating this work and sitting on `curve/07e-evidence-supply`. **Do not stage,
commit, revert, or clean them.** They are someone else's work in progress.

Our work is already committed — **19 commits**, each with its gates green. Only
files under `designwins/`, `scripts/{emit_pdf_engine_reference,fetch_pdfium,
freeze_corpus}.*`, `tests/fixtures/gold/*`, and `PLAN.md` are ours.

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
  crates/dw-decide     Build / Atom / Cohort / evaluator + the trust gate
  crates/dw-knives     axes + phrase layer (D4/D5) — colloquial -> knives
  crates/dw-funnel     the composition root: accept(sentence) -> Build
  crates/dw-evidence   resolvable provenance: page render + crop (C13)
  crates/dw-release    ReleaseId -> immutable append-only manifest
  crates/dw-parity     D2 discrepancy ledger + corpus manifest
  crates/dw-pdf        PDFium engine, renderer, + D1 G7 gate
scripts/
  freeze_corpus.py               content-addressed freeze, drift-detecting
  emit_pdf_engine_reference.py   MuPDF reference fixture for G7
  fetch_pdfium.sh                pinned PDFium 7881, checksum-verified
```

**10 crates · 34 Rust files · ~9,700 lines · 86 tests passing.** G7 is the one
intentional failure. **`PLAN.md` §12's "87K claim diff = 0 unexplained" is
met** — N4a 9,551/9,551 and N4b 10,270/10,270 weighted 64,245/64,245, both
against `catalog.db`.

Dependency graph is acyclic and each layer has one job:

```
dw-types -> dw-normalize -> dw-decide -> dw-knives -> dw-funnel
   law      comparability     the funnel   colloquial->knives  composition
                |                |
           dw-truth         dw-evidence / dw-release   (the trust chain)
                |                |
           dw-parity             dw-pdf  (engine + render)
```

`dw-funnel::accept` is **the** entry point. The PLAN §0 sentence is a test
(`acceptance_test_sentence_to_cohort`), not a paragraph:

```rust
let answer = dw_funnel::accept(
    "an outdoor weather thermostat bluetooth",
    &dw_knives::seed_from_measured_chips(),
    &cohort,             // dw_decide::Cohort of distinct Candidate parts
    &facts,              // BTreeMap<Candidate, CandidateFacts>
    release,             // dw_types::ReleaseId
);
// answer.build = { cohort, derived[Pending], explain, exclusions, unknowns, next }
```

### Gates

| gate | what | status |
|---|---|---|
| build / fmt / clippy pedantic / cargo-deny | whole workspace | **green** |
| N1a `condition_model_v1.jsonl` | 167/167 | **green** |
| N1b `rating_class_v1.jsonl` | 63/63 | **green** |
| N3 compile-fail | 4 laws | **green** |
| N2 property tests | units round-trip, coverage reflexivity, margin explicitness | **green** |
| Week 0 parity | ledger runs empty, `cutover_permitted` | **green** |
| **Trust gate** | evidence resolves · releases resolve · unresolvable ⇒ `Unknown` | **green** (see `PROVENANCE.md` §9) |
| **N4a condition corpus** | `catalog.db::conditions_typed`, **9,551/9,551** | **green** |
| **N4b claim diff** | `claims_canonical`, **10,270/10,270** distinct · **64,245/64,245** claims weighted | **green — this is the PLAN §12 gate** |
| C3/C13 provenance | 86,736/86,736 claims carry quote + sha + page | **green** |
| D1 G7 path segments | PDFium vs MuPDF | **OPEN — expected** |

The commands are in §0 above.

---

## 2. Definition of done is not yet met. Critical path, trust-first.

**Read `PROVENANCE.md` before this section.** It reorders everything below by
what makes an answer *believable* rather than by what completes the acceptance
sentence, and it says honestly which links in the trust chain are typed but not
real.

Two of the acceptance sentence's clauses — "every number opens to the page it
came from" and "I can reproduce this" — are the trust links, and they are the
two that are **not real today**. Building the other clauses first produces a UI
that looks accountable and is not. That is the failure mode worth being afraid
of.

### Tier 1 — an answer can be believed. **DONE 2026-10-10. Do not rebuild.**

| # | work | unlocks | landed in |
|---|---|---|---|
| 1.1 | **`dw-evidence`**: page render @ 144 DPI + row-band crop, content-addressed, engine-pinned, stored locally | "every number opens to the page it came from" | `crates/dw-evidence`, `dw_pdf::render_rgba` |
| 1.2 | **`dw-release`**: `ReleaseId` resolves to an immutable manifest (corpus freeze sha, vocab versions, embedder fingerprint, engine identity, code sha, `previous` for lineage) | "I can reproduce this" | `crates/dw-release` |
| 1.3 | **One rule in `dw-decide::judge`**: unresolvable evidence ⇒ `Unknown`, never `Pass` | provenance failure refuses an answer instead of silently passing | `dw_decide::judge` + the `Provenance` trait |

### Tier 2 — an answer can be relied on at scale

| # | work | unlocks | state |
|---|---|---|---|
| ~~2.1~~ | ~~**Gate N4: condition + claim corpora**~~ | proof the port did not drift | **DONE** — N4a 9,551/9,551 · N4b 10,270/10,270 weighted 64,245/64,245 · provenance 86,736/86,736 |
| 2.2 | **Phrase-layer gate** against the query telemetry, count-weighted (1,485 rows / 8,653 searches) | proof the colloquial mapping is right | — |

**Next is the phrase-layer gate (2.2), then Tier 3.**

### Tier 3 — an answer is useful

| # | work | unlocks |
|---|---|---|
| 3.1 | **Question ranking** — port `next_question` (verified over 1,881 parts / 86,736 claims). Current ranking by `affected` is an honest proxy, not the real thing | "the next question that would narrow it most" |
| 3.2 | **`dw-search`** (D4: the colloquial front door; FTS is the residue path) | — |
| 3.3 | **`dw-coverage`** — cube + gap ledger. `20260814_yellow_scorecard` is the measured substrate | "lists what it could not evaluate" at corpus scale |
| 3.4 | **`dw-knives` axis registry** — generalise past 5 power axes (QA finding F-03) | — |

### Tier 4 — it runs

`dw-structure` / `dw-recon` / `dw-graph` (Week 2-3) · `dw-extract` / `dw-queue` /
`dw-release` / `dw-model` / `dw-learn` (Week 3-5) · `dw-api` (D7 delivery gate)
· `dw-controller` · cutover.

## 2b. Storage — three surfaces, know which is which

| surface | path | what it is | use it for |
|---|---|---|---|
| **yosemiteraid5** | `/Users/samkim/mnt/yosemiteraid5/{Public,Home}` | **local NVMe NAS, fast, writable.** SMB `tapes@yosemiteraid5.local`, `nobrowse` so it does **not** appear in `/Volumes` — you have to know the path | the work target. `Public/m5-opencode/` is a clean landing zone |
| **M5_4TB** | `/Volumes/M5_4TB` | spinning-disk NAS, bulk data. **Unmounts without warning** | read-only source. Never a build or store target (`PLAN` §10) |
| **Electronicdataassets** | `/Users/samkim/Volumes/Electronicdataassets` | SMB `harness@192.168.4.54`, contains `m5-4tb/` and `tapes4tb/` mirrors + `backups/m5/` | cross-checking; some files are readable here that are denied on the mount |

**SQLite on these mounts needs `?mode=ro&immutable=1`.** Plain `connect()`
fails with "unable to open database file" because SQLite wants to create a
WAL/journal and is denied. `catalog.db` copies to `yosemiteraid5` in ~2.7s
(58 MB) and reads fine there.

`yosemiteraid5` is the one to build against. The evidence store (`dw-evidence`)
and release store (`dw-release`) already refuse the NAS by design; point them
here.

## 3. Ground truth — where the answers already are

Do not re-derive these. They are measured.

| thing | location |
|---|---|
| the plan | `PLAN.md` (Revision 3) — **its body is partly stale**. Read §19 "Corrections" at the bottom: it names which sections are superseded and by what. Where a `DECISIONS.md` entry disagrees with `PLAN.md`, **the decision wins** |
| ratified laws | `designwins/docs/DECISIONS.md` — D1–D7. **These supersede `PLAN.md`** |
| contracts | `designwins/docs/CONTRACTS.md` — `Build` first, then C1–C13 |
| normalization spec | `designwins/docs/PRD-NORMALIZE.md` — reviewed, with its own corrections recorded in §10 |
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

**When the NAS is mounted, most of it is readable** — `exports/`, `vault/`,
`extract-results/`, root-level docs. One exception: `harness-training/datasets/`
returns `EACCES` on `open` from this sandbox while `stat`/`readdir` succeed. The
tree is `0700` and owned by us with no ACL, so it is a sandbox/TCC read-deny
rather than a permissions problem. The 87K corpus is in there; see §7.

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
