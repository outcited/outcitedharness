# SEARCH RUNBOOK — engineering-evidence retrieval (PRD-SEARCH-01)

Owner: M5 / Harnessv1 extraction lane. Branch: `search/evidence-retrieval-v0`,
isolated worktree `../Harnessv1-search` (agent-isolation directive 2026-10-08;
the main worktree belongs to whichever lane checked it out — never switch it).
Architecture and reuse map: `SEARCH_RECON.md`. Requirements: `PRD-SEARCH-01.md`.

## Provisional-approval status (2026-10-08 review)

Approved provisionally; NOT production search. Serving stays localhost-only
(hard-refused non-localhost binds unless `SEARCH_ALLOW_REMOTE=1`, which
requires auth + network policy first). Hard invariants under regression
test: zero invented quotations, zero family→OPN scope expansion.

## Worktree isolation (P3)

Every agent works in its own worktree + branch. This lane:
`git worktree add ../Harnessv1-search search/evidence-retrieval-v0`.
`.agent-branch` at the worktree root names the only branch it may commit
to; the shared `.git/hooks/pre-commit` shim delegates to
`scripts/agent_guard.py` when the checked-out branch carries it (no-op on
lanes that don't adopt it). A cross-lane commit is now a hard stop.

## Provenance serving gate (P1)

Every unit carries `evidence_grade`:

- `evidence_grade` — real artifact sha256 AND a precise locator (page or
  bbox). May satisfy `min_verification` filters.
- `discovery_only` — placeholder identity (`unhashed:` stems) or imprecise
  locator. Fully searchable, visibly marked, and STRUCTURALLY BARRED from
  satisfying any verified-evidence filter (`min_verification` above
  `unverified` excludes it in the query layer, not by convention).

Responses also carry per-unit `evidence_id` (`ev-…`): the durable evidence
identity that stays stable across extraction-version bumps (`unit_id`
moves; `evidence_id` does not). Consumers cite `evidence_id`.

## Decomposed evaluation (P0) — the three separated questions

```
scripts/search_benchmark.py --live --decomposed
```

1. corpus coverage — does the expected evidence exist in the index at all?
2. conditional retrieval — when it exists, does it reach the top-10?
3. locator validity — are returned hits traceable (page + cell/quote/bbox/
   figure) and evidence-grade?

Live results 2026-10-08 (101 anchor-bearing entries):

| Layer | Result | Reading |
|---|---|---|
| coverage | 0.901 (mcu 1.00 / power 1.00 / connectors 0.667) | corpus gap is ONLY live-connector material |
| conditional recall@10 | **0.143** | **the blocker: engine ranking, not coverage** |
| locator precision | 0.80 (72/90; the 18 = unhashed burn-wave units) | gate marks them correctly |
| must_not violations | 0 | invariant holds |

Correction of the pilot framing: the earlier "missing aisles explain the
live 0.129" claim was WRONG for mcu/power — their anchors are present; the
engine simply fails to rank them for natural-language queries. FTS bm25
alone cannot bridge the vocabulary gap. The semantic vector path is
required, not optional.

### Attaching vectors (the conditional-recall fix)

```
scripts/search_index.py --embed http://100.81.201.24:8800/v1/embeddings
SEARCH_EMBED_URL=... python -m harness.search.api   # query side
```

Idempotent (skips units already embedded with the same model), batches of
16, model bge-m3-cr-tapes-v1 (the serving-qualified dgx1/e10b embedders —
same encoder family as the claim sidecars). NOT yet run: fleet endpoints
were unreachable from this box at session time. Re-run the decomposed eval
after attaching; conditional recall is the number to watch.

## Curve lane integration (P2)

```
scripts/search_index.py --curves /Volumes/M5_4TB/extract-results/curves-pilot-v1
```

Indexes the curve lane's committed pilot fixtures (their contract verbatim:
real document sha256, printed page, axes, per-series printed conditions,
digitized points, digitization quality) as figure-grain units with a full
`structured` payload. 40 figures from 8 documents live now. The envelope
slims curve series to conditions + x-range + point-count + samples; the
full points ride on `GET /v1/units/{id}`. Margin adjudication
("efficiency ≥ 90 % @ 12 V in / 5 V out / 2 A") deliberately stays with the
curve lane's bounded operating-point query — retrieval returns curve,
conditions, applicability, page, and uncertainty (fit residuals); consumers
evaluate. Hash-named artifacts (vishay pilot) mint no part numbers.

## What this is

A read-only retrieval layer over extraction outputs. It indexes evidence
units (document / section / table / figure / claim / family / application
grains) with full provenance into `search_index.db`, and serves a
versioned, release-pinned search API on :8791. It never writes to
pipeline.db or catalog.db, never writes `verified` (M4's word alone), and
never widens family-level evidence into OPN-level evidence.

## Serving

```
SEARCH_DB=/Volumes/M5_4TB/extract-results/search_index.db \
SEARCH_HOST=127.0.0.1 SEARCH_PORT=8791 \
.venv/bin/python -m harness.search.api
```

Routes: `GET /v1/health`, `GET /v1/release`, `GET /v1/units/{id}`,
`POST /v1/search {query, limit?, filters?, interpretations?}`.
Filters: vendor, family, category, grain, min_verification,
include_retired. stdlib only, same shape as the discovery front door.

Consumer example: `scripts/search_example.py` (auto-falls back to the
offline library path when :8791 is not serving).

## Build / refresh the index

```
scripts/search_index.py            # catalog + topology wave (idempotent)
scripts/search_index.py --dry-run  # report only
scripts/search_index.py --release  # print the pinned release
```

- Idempotency: natural key (doc_sha256, grain, locator, extraction_version);
  re-running with unchanged inputs replaces rows in place, release unchanged.
- A better extraction = new extraction_version = immutable supersede (both
  versions retained in the units table; only the latest per doc is indexed).
- A replaced document: `units.retire_document(con, sha, reason)` (soft
  retire; queries hide retired units unless include_retired).
- Full rebuild from scratch: delete search_index.db and re-run (≈5s for
  92K units on the M5 volume; per-doc commits are fsync-bound — the
  indexer batches the whole run into one transaction).

## Releases and rollback

Every response carries the content-addressed release
(`search-release-v1-<fp12>`; fingerprint hashes unit ids + text +
verification state, so content cannot hide behind a stable id). Builds are
appended to the `releases` table for audit.

Rollback = restore the previous db file (nightly Tapes_4TB backup covers
extract-results; or rebuild from catalog in seconds — the index is derived):
```
cp search_index.db search_index.db.broken
sqlite3 search_index.db "SELECT * FROM releases ORDER BY id DESC LIMIT 5"
# restore from backup, or: rm search_index.db* && scripts/search_index.py
```

## Benchmark

```
scripts/search_benchmark.py --fixture          # CI subset (73 entries)
scripts/search_benchmark.py --live             # full 108 + quote audit
scripts/search_benchmark.py --live --aisle power --verbose
```

Frozen set: `tests/fixtures/gold/search_benchmark_v1.jsonl` (108 entries;
mcu 36 / power 40 / connectors 32; kinds: paraphrase, terminology_mismatch,
figure_only, table_header, cross_document, ambiguous, unsupported).
Fixture corpus: `tests/fixtures/gold/search_fixture_corpus.jsonl`.

Pilot measurements (2026-10-08):

| Mode | Recall@10 | nDCG@10 | locator | must_not viol | invented quotes | p50/p95 |
|---|---|---|---|---|---|---|
| fixture (reviewed subset) | 0.964 | 0.842 | 0.909 | 0 | n/a (fixture) | 0.7/1.4 ms |
| live (91,758 units incl. curves) | 0.129 | 0.111 | 0.000* | 0 | 0 / 259 checked | 24/91 ms |

*Live number decomposed above: coverage 0.901, conditional recall 0.143,
locator precision 0.80. Use the decomposed numbers, not the blended one.

## Vectors (semantic path)

Required by the decomposed-eval finding (conditional recall 0.143 on
FTS-only). Attach with `scripts/search_index.py --embed URL` using the
serving-qualified embedders (dgx1/e10b :8800/:8804, bge-m3-cr-tapes-v1 —
same encoder family as `harness/electronics/embeddings.py` sidecars;
idempotent per model, batches of 16). Query side activates via
`SEARCH_EMBED_URL` on the API or the `embed=` callable on `search()`;
without it, retrieval stays FTS-only (deterministic tier, no fleet cost).
Not yet attached live — fleet endpoints unreachable at session time; this
is the single highest-leverage next action, then re-run the decomposed
eval.

## Adjudication annotation (verification states)

The indexer accepts `adjudications={(doc_sha, quote): state}` copied from
pipeline.db `adjudication_ledger` verdicts. Wiring (extract → annotate →
re-index) is planned but NOT yet automated; until then every live unit
reports `unverified` honestly. Never wire `verified` from anywhere but M4's
cell_verifier output.

## Fleet / ownership notes

- Port 8791 (checked against RUNBOOK fleet table; 8790 discovery, 8888
  V4.1, 8901 canon, 8950 student, 8800/8804 embedders).
- Storage: new file under /Volumes/M5_4TB/extract-results/ (search_index.db
  + WAL). Nightly mac-mini backup already covers that tree.
- Budgets: indexing is deterministic (uncapped tier); no vision, no
  frontier quota, no new corpus passes (R5).
- Mail protocol: before first production serving, drop a heads-up in
  /Volumes/M5_4TB/agent-inbox/mac-mini/ and m4opencode (frontmatter per
  AGENT_NETWORK.md). Not yet done — pilot is local-only.
- Do not restart any existing service to deploy this; it is a new process.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| API 404 unknown unit | index rebuilt between request and fetch | re-run search (release changed) — expected, units are immutable |
| p95 latency creep | unit count growth (per-hit SELECT + brute cosine) | add candidates shortlist; do not "fix" with commit changes |
| release never changes after rebuild | fingerprint cached, meta not dirty | `units.index_release(con, force=True)` |
| conditional recall low, coverage high | vectors not attached | `--embed` (above), then re-run decomposed |
| conditional recall low, coverage low | corpus gap (e.g. connectors) | fill the aisle upstream; retrieval cannot rank what isn't indexed |
| FTS "malformed MATCH expression" | empty/odd query string | fts_query() quotes everything; if seen, check technical_normalize changes |
| sqlite locked | indexer + API on same db concurrently | WAL allows it; if sustained, indexer holds the big transaction — let it finish (seconds) |
| commit refused by "agent guard" | worktree HEAD ≠ .agent-branch | switch branches in YOUR worktree; never re-branch another agent's worktree |
