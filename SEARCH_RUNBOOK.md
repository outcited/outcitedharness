# SEARCH RUNBOOK — engineering-evidence retrieval (PRD-SEARCH-01)

Owner: M5 / Harnessv1 extraction lane. Branch: `search/evidence-retrieval-v0`.
Architecture and reuse map: `SEARCH_RECON.md`. Requirements: `PRD-SEARCH-01.md`.

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
| live (91,718 units) | 0.129 | 0.111 | 0.000* | 0 | 0 / 259 checked | 24/91 ms |

*Known, disclosed gaps that dominate the live number — the benchmark is
measuring them on purpose:

1. The live catalog (1,882 parts) is power-aisle only: no MCU aisle
   (except ESP8266EX) and no connectors, so ~60 of 108 entries cannot hit.
2. No figure units exist yet (typical-curves wave not yet indexed) — all
   figure_only entries score zero.
3. Burn-wave provenance is `unhashed:<stem>` with null pages, so
   source-locator accuracy is 0 until the substrate backfills real hashes
   and pages (then re-index under a new extraction_version upgrades it).
4. Latency p95 ~90ms is pilot-brute-force (per-hit row SELECT + in-Python
   cosine); fine at 92K units, needs a candidates shortlist at corpus scale.

Do NOT quote the live number as corpus-wide precision (PRD: benchmark ≠
corpus claim). The fixture row is the reviewed-pilot measurement against
the acceptance targets (≥0.90 locator, 0 invented quotes, 0 scope
expansion — all met).

## Vectors (semantic path)

Optional. Attach with `units.attach_vector(con, unit_id, model, values)`
using the serving-qualified embedders (dgx1/e10b :8800/:8804, BGE family —
same encoder as `harness/electronics/embeddings.py` sidecars). The query
path activates only when vectors exist AND an `embed` callable is passed;
without it, retrieval is FTS-only (deterministic tier, no fleet cost).
Batch-attach script is deliberately NOT written yet — pilot on FTS first,
add vectors when the benchmark shows vocabulary-miss the FTS cannot close.

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
| p95 latency creep | unit count growth (per-hit SELECT) | add candidates shortlist; do not "fix" with commit changes |
| release never changes after rebuild | fingerprint cached, meta not dirty | `units.index_release(con, force=True)` |
| FTS "malformed MATCH expression" | empty/odd query string | fts_query() quotes everything; if seen, check technical_normalize changes |
| sqlite locked | indexer + API on same db concurrently | WAL allows it; if sustained, indexer holds the big transaction — let it finish (seconds) |
