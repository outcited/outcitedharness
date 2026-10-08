"""Hybrid retrieval tests (PRD-SEARCH-01 R2/R4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.search import indexer, query, units

CORPUS = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"


@pytest.fixture()
def con(tmp_path):
    con = units.connect(str(tmp_path / "search.db"))
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


def _anchors(response):
    return [u["text_repr"][:60] for u in response["units"]]


def test_symbol_folding_finds_rds_on(con):
    r = query.search(con, "rdson 3.9 milliohm", limit=3,
                     with_interpretations=False)
    assert any("IPF039N08NF2S" in t for t in _anchors(r))
    hit = r["units"][0]
    assert hit["locator"]["quote"] == "RDS(on),max 3.9 mOhm"


def test_unit_aware_query_matches_printed_form(con):
    r = query.search(con, "750 V device", limit=3,
                     with_interpretations=False)
    assert any("AIMBG75R040M1H" in t for t in _anchors(r))


def test_part_number_prefix_search_without_killing_mpn(con):
    r = query.search(con, "STM32F103", limit=5,
                     with_interpretations=False)
    assert r["units"]
    assert all("STM32F103" in u["text_repr"] or
               u["family"] == "STM32F103" for u in r["units"])


def test_semantic_phrasing_reaches_vocabulary_mismatch(con):
    r = query.search(con, "chip that sleeps most of the time coin cell",
                     limit=10, with_interpretations=False)
    assert any("Sleep, Stop and Standby" in u["text_repr"]
               for u in r["units"])


def test_filters(con):
    r = query.search(con, "72 MHz", limit=10,
                     filters={"vendor": "st.com"},
                     with_interpretations=False)
    assert r["units"] and all(u["vendor"] == "st.com" for u in r["units"])
    r = query.search(con, "document covers", limit=10,
                     filters={"grain": "family"},
                     with_interpretations=False)
    assert r["units"] and all(u["grain"] == "family" for u in r["units"])


def test_min_verification_ladder_filters(con):
    con.execute("UPDATE units SET verification_state='rejected'"
                " WHERE grain='family'")
    con.commit()
    r = query.search(con, "document covers", limit=20,
                     filters={"min_verification": "unverified"},
                     with_interpretations=False)
    assert all(u["verification_state"] != "rejected" for u in r["units"])


def test_retired_units_hidden_unless_asked(con):
    row = con.execute("SELECT unit_id FROM units WHERE grain='family'"
                      " LIMIT 1").fetchone()
    units.retire_document(
        con, con.execute("SELECT doc_sha256 FROM units WHERE unit_id=?",
                         (row[0],)).fetchone()[0], "test")
    r = query.search(con, "document covers", limit=50,
                     with_interpretations=False)
    assert all(not u["unit_id"] == row[0] for u in r["units"])
    r2 = query.search(con, "document covers", limit=50,
                      filters={"include_retired": True},
                      with_interpretations=False)
    assert any(u["unit_id"] == row[0] for u in r2["units"])


def test_envelope_contract(con):
    r = query.search(con, "low power wireless industrial sensor", limit=3)
    assert r["schema"] == "harness.search-response.v1"
    assert r["query"]["original"] == "low power wireless industrial sensor"
    assert r["query"]["interpretations"]  # hypotheses present
    assert all(i["status"] == "hypothesis"
               for i in r["query"]["interpretations"])
    assert r["provenance"]["release"].startswith("search-release-v1-")
    assert r["qualification"] == "none"
    assert "notice" in r
    for u in r["units"]:
        assert u["score"] >= 0 and u["rationale"]
        assert set(u) >= {"unit_id", "grain", "doc_sha256", "page",
                          "locator", "text_repr", "applicability",
                          "verification_state", "score", "rationale",
                          "revision", "extraction_version"}


def test_applicability_never_widened(con):
    r = query.search(con, "StrongIRFET 80 V", limit=5,
                     with_interpretations=False)
    ipf = [u for u in r["units"] if "IPF039N08NF2S" in u["text_repr"]]
    assert ipf
    for u in ipf:
        for app in u["applicability"]:
            assert app["coverage_kind"] in ("primary", "family", "mention",
                                            "ordering_table", "burn-primary")


def test_vector_semantic_path(con):
    target = con.execute(
        "SELECT unit_id FROM units WHERE family='CoolSiC' AND grain='claim'"
        " LIMIT 1").fetchone()[0]
    dim = 8
    base = [0.1] * dim
    units.attach_vector(con, target, "fake", base)
    for (uid, ) in con.execute(
            "SELECT unit_id FROM units WHERE unit_id != ?", (target,)):
        units.attach_vector(con, uid, "fake", [-0.1] * dim)

    def embed(texts):
        return [base]

    r = query.search(con, "onboard charger silicon carbide switch",
                     limit=3, embed=embed, with_interpretations=False)
    assert r["units"][0]["unit_id"] == target
    assert any("semantic" in x for x in r["units"][0]["rationale"])


def test_no_hits_returns_clean_empty(con):
    r = query.search(con, "zzzqqq xyzzy", limit=5,
                     with_interpretations=False)
    assert r["units"] == []
    assert r["provenance"]["release"]
