"""Engineering-evidence retrieval over the digitized corpus (PRD-SEARCH-01).

Modules:
    units     evidence-unit schema + store (search_index.db)
    expand    intent-hypothesis expansion (deterministic v1)
    query     hybrid retrieval (FTS5 bm25 + trigram ident + vectors)
    indexer   incremental idempotent indexer over catalog/wave outputs
    api       versioned search API (:8791, release-pinned)

Laws: provenance on every unit; applicability never widened (coverage_kind
travels end-to-end); `verified` is M4's word alone; relevance is not
qualification.
"""
