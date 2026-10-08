# SEARCH RECON — architecture reconnaissance and reuse map (PRD-SEARCH-01, deliverable 1)

Written: 2026-10-08, branch `search/evidence-retrieval-v0`. Read-only survey
of the live repo; every claim below was verified against source on this
branch. Line numbers refer to the surveyed files.

## 1. What exists and is reused (do not rebuild)

| # | Capability | Where | Reuse decision |
|---|---|---|---|
| 1 | Evidence provenance model — `document_sha256`, `page_1based`, `bbox`, `table_index`/`row_index`/`column_index`, `quoted_text`; kinds SOURCE_RECORD / TEXT_SPAN / TABLE_CELL / IMAGE_REGION | `harness/electronics/models.py:35` (`EvidenceKind`), `:184` (`EvidenceReference`) with cross-field validators | The evidence-unit locator vocabulary copies these kinds verbatim so units and claims interoperate |
| 2 | Claim grain with entity scope, conditions, evidence tuples | `FactClaim` `harness/electronics/models.py:247`; typed conditions in `harness/electronics/condition_model.py` | Claim units are projected from `claims_canonical`, not re-extracted |
| 3 | Family-vs-OPN honesty — `part_sources.coverage_kind` ∈ primary / family / mention / ordering_table; family matrix binds a column to an OPN only when printed, else `part_number: null`; family documents "never resolve to an orderable part" | `harness/catalog/store.py:30-38`; `harness/electronics/family_device_matrix.py` (module docstring); `harness/electronics/family_document.py` (module docstring) | Unit `applicability` carries `coverage_kind` unchanged; the query layer surfaces it and never widens it (PRD R1 law) |
| 4 | Document revision tracking | `doc_revisions` table `harness/catalog/store.py:76-82` (rev_code, rev_date, doc_kind, supersedes_sha); `consensus.conflict_class='revision_diff'` | Copied onto units at index time |
| 5 | Verification state — adjudication verdicts SUPPORTED / UNSUPPORTED / NOT_IN_DOC / AMBIGUOUS; **M4 cell_verifier is the sole writer of `verified`** | `adjudication_ledger` `harness/pipeline/store.py:53-66`; law 7 in CONTEXT_HANDOFF.md | Units read adjudication state as `verification_state`; search never writes verdicts, never mints `verified` |
| 6 | Idempotent, version-keyed extraction | `results` UNIQUE(corpus_key, kind_hint, extractor_version) `harness/pipeline/store.py:38-50`; GCI state-hash gating refuses unchanged re-uploads `harness/gci/indexer.py:66-94` | The evidence index uses the same natural key: (doc_sha256, grain, locator, extraction_version) |
| 7 | Embeddings — sealed immutable BGE sidecars (`vectors.f32` + `index.jsonl`, schema-stamped) for admitted claims; embedder endpoints dgx1/e10b :8800/:8804 (SERVING-QUALIFIED) | `harness/electronics/embeddings.py:77` (`seal_embedding_sidecar`), RUNBOOK.md fleet table | Query-side embeddings use the same encoder family; vectors stored per-unit in SQLite (GCI `_pack_vector` float32 pattern, `harness/gci/storage.py:33`) |
| 8 | Section/figure/table unit readers | `harness/electronics/page_index.py:39` (LANE_PATTERNS: pin_or_ball, parametrics, power_modes, typical_characteristics, series_summary, opn_decoder), `typical_curves.py` (caption/axis-tick/legend anchors), `region_detect.py` (table bboxes), `locator.py` (pin-definition pages) | Grain extractors for section/figure/table units call these; no new PDF parsing is written |
| 9 | Versioned, release-pinned HTTP API pattern (stdlib only) | `harness/discovery/api.py` (:8790), `service.py` (`corpus_release` pins reads), `store.py` (results stamped with release) | `harness/search/api.py` copies the shape: ThreadingHTTPServer, JSON envelope, release stamp on every response (:8791) |
| 10 | Gold benchmark conventions | `tests/fixtures/gold/*.jsonl` hand-labeled {input…, expected: {…}} | `search_benchmark_v1.jsonl` follows the same frozen-jsonl pattern |
| 11 | GCI (code search) | `harness/gci/` — RepoSnapshot/semantic-slice indexing with SQLite vectors + cosine | **Not corpus search** — it indexes source code. Its storage/indexer architecture (packed vectors, generation retention, hash-gated increments) is the template, not the substrate |

## 2. The actual gap (what PRD-SEARCH-01 builds)

1. No corpus-level retrieval over datasheet evidence: no FTS/BM25 index over
   page text, tables, or figures (only GCI, which indexes code).
2. Embeddings exist as sealed claim-grain sidecars only — no queryable
   per-unit vector index, no query-time embedding path.
3. No hybrid ranker (semantic + exact/symbol + applicability + quality).
4. No intent-aware query expansion (R3).
5. No versioned search API contract (R4).

## 3. New components (all additive, nothing upstream is edited)

```
harness/search/
    units.py      evidence-unit schema + SQLite store (units, FTS5 x2,
                  vectors, releases)  — deliverable 2
    expand.py     deterministic intent-hypothesis expansion  — R3
    query.py      hybrid retrieval: FTS5 bm25 + trigram ident + cosine
                  fusion, coverage/verification boosts, filters, rationale
    indexer.py    incremental idempotent indexer + catalog adapter
                  (claims_canonical/part_sources/doc_revisions → units)
                  — deliverable 4
    api.py        versioned API, :8791, release-pinned  — deliverable 3
scripts/
    search_index.py       CLI indexer (dry-run capable)
    search_benchmark.py   frozen-benchmark scorer (fixture + live modes)
    search_example.py     consumer integration example  — deliverable 6
tests/fixtures/gold/search_benchmark_v1.jsonl   frozen benchmark (100+)
tests/test_search_*.py    unit + API tests
SEARCH_RUNBOOK.md         operations  — deliverable 7
```

Storage: `/Volumes/M5_4TB/extract-results/search_index.db` (new file; the
pipeline.db / catalog.db schemas are untouched; catalog is opened read-only
by the indexer).

## 4. Index model

- **Unit natural key**: `(doc_sha256, grain, locator_json, extraction_version)`; `unit_id` = sha256 of the key — re-indexing the same extraction is a no-op (idempotent), a better extraction is a new version (immutable supersede), a rejected claim flips `verification_state` without touching text.
- **Grains** (R1): document, section, table, figure, claim, family, application.
- **Per-unit retained**: doc sha, page + locator (bbox/table/row/col/span in EvidenceReference vocabulary), text_repr, identifiers, vendor, doc_class, category, family, applicability [{scope, value, coverage_kind}], revision, extraction_version, verification_state(+source), retired flag.
- **Retrieval**: unicode61 FTS5 over normalized technical text (digit-letter boundary splitting makes "100mA" and "3.3V" searchable as printed), trigram FTS5 over identifiers (substring/prefix part-number and family search without knowing the MPN), optional per-unit vectors + cosine (pilot: packed float32 in SQLite, brute-force — catalog scale is ~60K claims).
- **Release pinning**: content fingerprint over sorted unit ids; every query response carries `release`; `releases` table logs builds for reproducibility and rollback (restore previous db file — runbook).

## 5. Laws this layer inherits (non-negotiable)

1. Provenance is law — every unit carries sha + page + locator + quote/structured repr; no locator → not indexed.
2. Applicability is never widened: family evidence keeps `coverage_kind=family` end-to-end (surfaced, never converted).
3. `verified` is M4's word alone; search reports adjudication state as-is.
4. Relevance is not qualification — the API contract says so on every response.
5. Read-only over catalog.db; the only mutable state is the search index itself.
6. Fleet budgets: pilot uses deterministic indexing + existing serving-qualified embedders; no new vision pass, no frontier quota.

## 6. Ownership and coordination

- This repo (M5 extraction lane) owns `harness/search/` end-to-end.
- Mac mini factory: untouched (no acquisition/vault changes) — mail heads-up
  in `/Volumes/M5_4TB/agent-inbox/mac-mini/` per protocol before the pilot
  index build.
- M4 (cell_verifier): read-only consumer of their verdicts via the
  adjudication ledger; no writes.
- Ports: 8791 (search API) — checked against RUNBOOK fleet table
  (:8790 discovery, :8888 V4.1, :8901 canon, :8950 student, :8800/:8804
  embedders). No service restarts; the API is a new process.

## 7. Review addendum (2026-10-08, provisional approval)

Approved provisionally, not production. Changes shipped in response:

1. **Worktree isolation (P3)** — this lane now works in
   `../Harnessv1-search`; `.agent-branch` + `scripts/agent_guard.py` +
   shared pre-commit shim make cross-lane commits a hard stop. The main
   worktree belongs to whoever checked it out.
2. **Provenance serving gate (P1)** — every unit carries `evidence_grade`
   (evidence_grade | discovery_only). Placeholder identities
   (`unhashed:`) or imprecise locators are searchable but visibly marked
   and structurally barred from verified-evidence filters.
3. **Durable evidence identity** — `evidence_id` (ev-…) stable across
   extraction versions; the citation handle for the shared evidence
   service (review recommendation: one contract behind discovery,
   comparison, curves, adjudication, and product citations).
4. **Decomposed evaluation (P0)** — coverage / conditional retrieval /
   locator validity measured independently (`--decomposed`). Finding:
   coverage 0.901 (gap = live connectors only), conditional recall 0.143
   — the blocker is engine ranking (semantic vocabulary gap), not corpus
   coverage. Earlier pilot framing corrected in SEARCH_RUNBOOK.
5. **Curve lane integration (P2)** — curve fixtures indexed as
   figure-grain units with full structured payloads (conditions, points,
   digitization quality); envelope summarizes, `/v1/units/{id}` returns
   full points. Margin adjudication stays with the curve lane's bounded
   operating-point query; retrieval returns curve + conditions +
   applicability + page + uncertainty.
6. **Vector path wired** (`--embed`, `SEARCH_EMBED_URL`) — the
   conditional-recall fix; attach pending fleet reachability.
