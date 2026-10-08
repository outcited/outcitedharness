"""Frozen benchmark integrity + pilot acceptance gates (PRD eval)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402

from search_benchmark import decomposed, load_benchmark, run  # noqa: E402

from harness.search import indexer, units  # noqa: E402

BENCH = Path(__file__).parent / "fixtures/gold/search_benchmark_v1.jsonl"
CORPUS = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"


def test_benchmark_is_frozen_and_well_formed():
    entries = load_benchmark(BENCH)
    assert len(entries) >= 100, "PRD requires at least 100 searches"
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert {e["aisle"] for e in entries} == {"mcu", "power", "connectors"}
    kinds = {e["kind"] for e in entries}
    assert kinds == {"paraphrase", "terminology_mismatch", "figure_only",
                     "table_header", "cross_document", "ambiguous",
                     "unsupported"}
    for entry in entries:
        assert entry["query"].strip()
        if entry["kind"] == "unsupported":
            assert not entry["expected"]["anchors"]
    assert sum(1 for e in entries if e.get("fixture")) >= 20


def test_figure_and_cross_document_kinds_present():
    entries = load_benchmark(BENCH)
    assert sum(1 for e in entries if e["kind"] == "figure_only") >= 10
    assert sum(1 for e in entries if e["kind"] == "cross_document") >= 10
    assert sum(1 for e in entries if e["kind"] == "unsupported") >= 5


@pytest.fixture()
def con():
    con = units.connect(":memory:")
    catalog = indexer.catalog_from_corpus_jsonl(CORPUS)
    merged = list(indexer.claim_units(catalog)) + \
        list(indexer.family_units(catalog))
    grouped: dict[tuple[str, str], list[dict]] = {}
    for unit in merged:
        grouped.setdefault(
            (unit["doc_sha256"], unit["extraction_version"]), []).append(unit)
    for (doc, version), group in grouped.items():
        units.replace_document(con, group, doc_sha256=doc,
                               extraction_version=version)
    return con


def test_fixture_pilot_acceptance_gates(con):
    """Pilot targets, locked as regressions: locator >= 0.85, zero must_not
    violations, zero-anchor entries never scored as recall failures."""
    entries = load_benchmark(BENCH, fixture=True)
    report = run(entries, con)
    assert report["scored"] >= 20
    assert report["recall_at_10"] >= 0.90, report
    assert report["ndcg_at_10"] >= 0.75, report
    assert report["locator_accuracy"] >= 0.85, report
    assert report["must_not_violations"] == 0, report
    assert report["latency_p95_ms"] < 100, report


def test_scoring_bounds(con):
    entries = load_benchmark(BENCH, fixture=True, aisle="connectors")
    report = run(entries, con)
    for metric in ("recall_at_10", "ndcg_at_10", "locator_accuracy"):
        if report[metric] is not None:
            assert 0.0 <= report[metric] <= 1.0


def test_decomposed_eval_separates_coverage_from_ranking(con):
    """P0 directive: a coverage gap must not read as an engine failure."""
    entries = load_benchmark(BENCH, fixture=True)
    report = decomposed(entries, con)
    cov = report["1_corpus_coverage"]
    cond = report["2_conditional_retrieval"]
    loc = report["3_locator_validity"]
    # every fixture anchor exists in the fixture index -> full coverage
    assert cov["coverage_rate"] == 1.0
    assert cond["entries"] == cov["covered"]
    assert cond["recall_at_10"] >= 0.90
    assert cond["must_not_violations"] == 0
    assert loc["matched_hits"] > 0
    assert loc["locator_precision"] is not None
    assert set(cov["per_aisle"]) == {"mcu", "power", "connectors"}


def test_index_has_anchor_negative(con):
    from search_benchmark import index_has_anchor
    assert index_has_anchor(con, "STM32F103")
    assert not index_has_anchor(con, "ZZZNOTACORPUSPART999")
