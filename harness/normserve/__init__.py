"""NORM-SERVE-01: normalization → store → serve (issue #64 contract).

Normalize extractor output once per (opn, doc_sha, field) into typed rows
with mandatory provenance, store (SQLite now; PG on x via adapter), and
serve GET /api/v1/opn/<OPN> with no LLM at serve time.
"""
