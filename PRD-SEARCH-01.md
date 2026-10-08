# PRD-SEARCH-01: Engineering Evidence Retrieval

**Owner:** M5 / Harnessv1 extraction team  
**Repository:** `outcited/outcitedharness`  
**Priority:** P0 — parallel to existing extraction  
**Status:** Proposed; reconnaissance required before implementation

> Ratified verbatim 2026-10-08 by Sam (session authorization: branch
> `search/evidence-retrieval-v0`, port 8791). Implementation notes live in
> `SEARCH_RECON.md` (reuse map) and `SEARCH_RUNBOOK.md` (operations).

## Mission

Make the existing digitized technical corpus searchable at engineering-evidence granularity.

The system must retrieve specific technical evidence relevant to an engineer's expressed problem, even when the engineer does not know the manufacturer's terminology, the category, the family, or the part number.

This is NOT another PDF crawler, a generic chatbot, or a replacement for the canonical catalog.

## Existing infrastructure to reuse

Inspect before implementing:

- `harness/pipeline/` — page substrate, claims, quote verification, source geometry
- `harness/catalog/` — canonical claims and source relationships
- `harness/electronics/family_document.py`
- `harness/electronics/family_device_matrix.py`
- `harness/electronics/typical_curves.py`
- `CONTEXT_HANDOFF.md`
- Existing embeddings, GCI, search, and persistent indexes where appropriate

Coordinate with the Mac mini factory rather than recreating its acquisition or vault functions.

## Functional requirements

### R1. Evidence-unit indexing

Index retrievable units at these grains:

1. Document
2. Section or passage
3. Table with headers, cells, and conditions
4. Figure with caption, axis labels, and legend
5. Verified engineering claim
6. Family or series relationship
7. Reference-design application relationship, when explicitly documented

Every unit must retain:

- Source artifact SHA-256
- Page and locator or bounding region
- Printed text or structured representation
- Manufacturer and document classification
- Known category / family / part applicability
- Document revision if known
- Extraction and verification status

Never silently convert family-level evidence into OPN-level evidence.

### R2. Hybrid retrieval

Support:

- Exact technical-term search
- Symbol and unit-aware search
- Semantic natural-language search
- Category and family filtering
- Figure/table-specific search
- Evidence quality filtering

Rank by semantic relevance, exact technical matching, applicability, evidence quality, and context.

A relevant result must not be buried simply because the datasheet uses different vocabulary from the engineer.

### R3. Intent-aware query expansion

Given a human engineering description, propose a small set of alternate technical interpretations.

Example:

"Low-power wireless industrial sensor"

Possible interpretations:
- Low sleep current
- Low active energy per measurement
- Integrated wireless MCU
- High-temperature operating capability
- Industrial communications compatibility

Return interpretations as hypotheses, not confirmed requirements.

Preserve the original user wording and confidence of each interpretation.

### R4. Search result contract

Expose a versioned API returning:

- Original query
- Candidate technical interpretations
- Matching evidence units
- Manufacturer / family / OPN relationships
- Source provenance
- Verification state
- Relevance rationale
- Evidence-release identifier

Do not claim an engineering qualification merely because a passage is semantically relevant.

### R5. Corpus-scale behavior

Use existing extracted text, geometry, and claims. Do not send all PDFs through vision again.

Use immutable or idempotent incremental indexing keyed to document hash and extraction version.

Support updates when a document is replaced, an extraction improves, or a claim is rejected.

Respect current resource ownership, queues, storage boundaries, and fleet budgets.

## Evaluation

Create a frozen benchmark of at least 100 engineering searches spanning MCU, Power, and Connectors.

Include:
- Natural-language paraphrases
- Manufacturer terminology mismatches
- Figure-only evidence
- Table-header dependencies
- Cross-document evidence
- Ambiguous applications
- Unsupported conclusions

Compare against the currently deployed retrieval baseline, where available.

Measure Recall@10, nDCG@10, source-locator accuracy, incorrect family/OPN applicability, and p50/p95 latency.

**Acceptance targets for initial pilot:**

- At least 90% correct source locators on the reviewed benchmark
- Zero known invented quotations in the accepted test set
- No observed unauthorized family-to-OPN scope expansion
- Material retrieval improvement over the measured baseline
- Reproducible index release and rollback

Do not claim corpus-wide precision from the benchmark alone.

## Non-goals

- No universal engineering recommendation engine
- No autonomous gold promotion
- No fabricated design compatibility
- No customer BOM collection
- No duplicate crawler
- No new full-corpus vision pass

## Deliverables

1. Architecture reconnaissance and reuse map
2. Evidence-unit schema
3. Search API contract
4. Incremental indexer and query service
5. Frozen benchmark with measured baseline
6. Consumer integration example
7. Operational runbook

## Delivery rules

Begin read-only. Identify existing implementations and reuse them.

Present the plan and repo ownership before modifications. Use an isolated branch after authorization. Do not restart services, alter production releases, or promote evidence without approval.

**Success:** An engineer can describe a problem in ordinary language and discover relevant, precisely located technical evidence across the corpus without knowing an MPN.
