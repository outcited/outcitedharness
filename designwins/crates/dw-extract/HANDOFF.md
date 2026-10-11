# HANDOFF — you are building `dw-extract`

**Read this whole file before writing a line of code.** Then read
`../../docs/ASSURANCE.md` before writing a *check*. The crate scaffold is
already in place and green; your job is the implementation.

## Mission

Port the Python extraction surface to Rust in **this crate only**:

| your surface | Python reference (read-only) | gate |
|---|---|---|
| `dw-extract` | `harness/electronics/extraction.py`, `bom_extract.py`, `key_features_grid.py` | extraction gold |

`bom_extract.py` is **uncommitted foreign work** — read it, never edit it.
Source of truth for the mapping: `designwins/docs/MIGRATION.md` (Product A
table). Port the reference's **observable behaviour** — do not improve it. If
the reference is wrong, it is wrong on purpose until the gold says otherwise
(`docs/DECISIONS.md` **D8**: *a fixture is a sample of the reference, not the
reference* — when they disagree, run the live reference).

## Hard boundaries — leaving them breaks a parallel session

Another agent is building `dw-api` in the same workspace right now.

| | you | not you |
|---|---|---|
| write | `designwins/crates/dw-extract/**` and your own fixtures under `tests/fixtures/gold/` with an `extract_` prefix | everything else |
| read | anything | — |
| commit | only your paths, never `git add -A` | `Cargo.toml` (members already set), `Cargo.lock` if you add no deps, `docs/**`, every other crate |

`designwins/docs/HANDOFF.md` has a single writer and it is not you. Report
status; do not document it. If you need a dependency beyond
`{thiserror, serde, serde_json, dw-types}`, that is a decision to report, not
to take silently.

## Discipline (the reason this repo exists)

1. **Every gate ships with a negative control** — a case where the old
   behaviour was wrong and the new one catches it. A test that cannot go red
   is not evidence (`docs/ASSURANCE.md`). The workspace has been bitten by
   this class 15+ times; do not add the 16th.
2. **Lint bar:** `cargo clippy --workspace --all-targets -- -D warnings` with
   `clippy::pedantic` for library code. **Do not weaken the library lint.**
   Test modules may carry a documented `#[allow]`.
3. **Smoke:** from `designwins/`:
   ```bash
   export PATH="$HOME/.cargo/bin:$PATH"   # brew rustup leaves shims unlinked
   cargo fmt --check && echo "FMT OK"
   cargo clippy --workspace --all-targets -- -D warnings && echo "CLIPPY OK"
   cargo test --workspace --no-fail-fast 2>&1 | grep "test result"
   cargo deny check
   ```
   **`--no-fail-fast` is load-bearing** — plain `cargo test` truncates at the
   first failing target and a gate that fails by design lives in `dw-pdf`.
   Count tests honestly; a truncated run is not a green workspace.
4. **Report measured numbers, never recalled ones.** If you say "N of M",
   the number came from a run you did after the last edit.

## Porting gotchas that will bite you (full list: `docs/HANDOFF.md` §6)

- Python `Pattern.match(s, pos)` **anchors at `pos`**; Rust `find()` searches
  anywhere. Silent zero-match failures on well-formed input.
- `fancy-regex` is required for lookaround (`(?<![\w.])`, `(?![A-Za-z])`).
  API: `find` not `search`, `captures` for named groups, `is_match` → `Result`.
- A tokenizer must **discard punctuation** (`drain-source` must not yield
  `-source`).
- **`str::find` is byte-indexed; Python `str.find` is code-point.** Slice by
  character or you will compute the wrong window — and an
  `unwrap_or(&whole)` fallback hides it. This class cost the last session its
  biggest bug (`dw-truth` §5.1 in `docs/HANDOFF.md`).
- Claim `value` is a **string** in the corpus (`"0.5"`, not `0.5`).
- Gold fixtures are not bit-exact on floats: structure exactly, numerics with
  tolerance.
- Rust raw strings have no `\`-newline continuation; Python patterns do.

## What done looks like

- The reference surface is replaced: a live side-by-side gate
  (pattern: `dw-parity --bin dw-replace`, `docs/HANDOFF.md` §2) or an
  extraction-gold gate, with **zero unexplained diffs**.
- FMT · CLIPPY pedantic · deny green; your tests pass under
  `--no-fail-fast`; every gate has its negative control.
- A short report to the human: what is proven, what is open, measured counts.

If you are unsure whether something is in scope: it is not. Finish the crate,
report, and let the human schedule the rest.
