"""Indexer tests (PRD-SEARCH-01 R5, deliverable 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.search import indexer, units

CORPUS = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"


@pytest.fixture()
def catalog():
    return indexer.catalog_from_corpus_jsonl(CORPUS)


@pytest.fixture()
def con(tmp_path):
    return units.connect(str(tmp_path / "search.db"))


def test_fixture_catalog_loads(catalog):
    assert catalog.execute("SELECT COUNT(*) FROM parts").fetchone()[0] >= 8
    assert catalog.execute(
        "SELECT COUNT(*) FROM claims_canonical").fetchone()[0] >= 25


def test_claims_fold_per_printed_cell(catalog):
    claims = list(indexer.claim_units(catalog))
    stm = [u for u in claims if u["doc_sha256"] ==
           "unhashed:st-STM32F103x4"]
    # VDD min and max print in DIFFERENT cells (different column headers):
    # two units, each carrying its own column provenance.
    vdd = [u for u in stm if "VDD" in u["text_repr"]]
    assert len(vdd) == 2
    cols = {u["locator"].get("column_header") for u in vdd}
    assert cols == {"Min", "Max"}
    # Same printed cell (identical row/col/quote) with two symbols folds
    # into ONE unit.
    catalog.execute(
        "INSERT INTO claims_canonical (opn, symbol, qualifier,"
        " condition_norm, value, unit, value_text, provenance, extractor,"
        " extractor_version, doc_sha256, created_at)"
        " VALUES ('IPF039N08NF2S','E1',NULL,'',1,'V','1',"
        " '{\"quote\":\"cell\",\"row_header\":\"R\",\"column_header\":\"C\","
        "\"page\":2}', 'burn','fixture-v1',"
        " 'unhashed:infineon-IPF039N08NF2S', 5.0),"
        " ('IPF039N08NF2S','E2',NULL,'',2,'V','2',"
        " '{\"quote\":\"cell\",\"row_header\":\"R\",\"column_header\":\"C\","
        "\"page\":2}', 'burn','fixture-v1',"
        " 'unhashed:infineon-IPF039N08NF2S', 5.0)")
    catalog.commit()
    again = list(indexer.claim_units(catalog))
    cell = [u for u in again
            if u["doc_sha256"] == "unhashed:infineon-IPF039N08NF2S"
            and "E1" in u["text_repr"]]
    assert len(cell) == 1
    assert "E2" in cell[0]["text_repr"]


def test_claim_unit_carries_provenance_and_coverage(catalog):
    claims = list(indexer.claim_units(catalog))
    ipf = [u for u in claims if u["doc_sha256"] ==
           "unhashed:infineon-IPF039N08NF2S" and "VDS = 80 V" in
           u["text_repr"]]
    assert len(ipf) == 1
    unit = ipf[0]
    assert unit["text_repr"].startswith("IPF039N08NF2S infineon.com")
    assert unit["page"] == 1
    assert unit["locator"]["row_header"] == "VDS"
    assert unit["locator"]["quote"] == "VDS 80 V"
    assert unit["applicability"] == [
        {"scope": "opn", "value": "IPF039N08NF2S",
         "coverage_kind": "burn-primary"}]  # verbatim, never renamed
    assert unit["vendor"] == "infineon.com"
    assert unit["family"] == "StrongIRFET"
    assert unit["verification_state"] == "unverified"


def test_adjudication_annotation_without_minting(catalog):
    adjudications = {
        ("unhashed:infineon-IPF039N08NF2S", "VDS 80 V"): "supported"}
    claims = list(indexer.claim_units(catalog,
                                      adjudications=adjudications))
    unit = [u for u in claims if u["doc_sha256"] ==
            "unhashed:infineon-IPF039N08NF2S" and "VDS = 80 V" in
            u["text_repr"]][0]
    assert unit["verification_state"] == "supported"
    assert unit["verification_source"] == "adjudication_ledger"


def test_only_latest_extraction_version_indexed(catalog):
    catalog.execute(
        "INSERT INTO claims_canonical (opn, symbol, qualifier,"
        " condition_norm, value, unit, value_text, provenance, extractor,"
        " extractor_version, doc_sha256, created_at)"
        " VALUES ('IPF039N08NF2S','NEWAXIS',NULL,'',1,'V','1',"
        " '{\"quote\":\"q\",\"page\":9}', 'burn','v2',"
        " 'unhashed:infineon-IPF039N08NF2S', 99.0)")
    catalog.commit()
    claims = [u for u in indexer.claim_units(catalog)
              if u["doc_sha256"] == "unhashed:infineon-IPF039N08NF2S"]
    versions = {u["extraction_version"] for u in claims}
    assert versions == {"burn/v2"}
    assert any("NEWAXIS" in u["text_repr"] for u in claims)


def test_family_units_from_part_sources_only(catalog):
    fams = list(indexer.family_units(catalog))
    doc = [f for f in fams if f["doc_sha256"] == "unhashed:st-STM32F103x4"]
    assert len(doc) == 1
    assert "STM32F103C4T6" in doc[0]["text_repr"]
    assert len(doc[0]["applicability"]) == 2
    assert {a["coverage_kind"] for a in doc[0]["applicability"]} == \
        {"primary"}


def test_topology_units_application_grain(tmp_path):
    wave = tmp_path / "wave.jsonl"
    wave.write_text(json.dumps({
        "schema": "harness.electronics-power-topology.v1",
        "part_number": "TESTPART", "vendor": "ti.com",
        "selector_aisle": "discrete-mosfets",
        "sha256": "c" * 64, "reader": "power_topology.v1",
        "topologies": ["buck", "boost"], "integration_class": "controller",
        "isolated": None,
        "evidence": [{"field": "topology", "value": "buck",
                      "quote": "buck converter application", "page": 3}],
    }) + "\n")
    out = list(indexer.topology_units(str(wave)))
    assert len(out) == 1
    unit = out[0]
    assert unit["grain"] == "application"
    assert unit["category"] == "power"
    assert unit["page"] == 3
    assert "buck" in unit["text_repr"] and "buck converter application" in \
        unit["text_repr"]
    assert unit["doc_sha256"] == "c" * 64  # real sha from the wave


def test_aisle_map_from_wave(tmp_path):
    wave = tmp_path / "wave.jsonl"
    wave.write_text(json.dumps({
        "part_number": "TESTPART", "vendor": "ti.com",
        "selector_aisle": "microcontrollers", "sha256": "c" * 64,
        "schema": "x", "topologies": [], "integration_class": None,
        "isolated": None, "evidence": [],
    }) + "\n")
    assert indexer.aisle_map_from_wave(str(wave)) == {"TESTPART": "mcu"}
    assert indexer.aisle_map_from_wave(str(tmp_path / "missing.jsonl")) == {}


def _disk_catalog(tmp_path):
    """Materialize the fixture corpus as a file-backed catalog db."""
    import sqlite3
    src = indexer.catalog_from_corpus_jsonl(CORPUS)
    path = tmp_path / "catalog.db"
    disk = sqlite3.connect(path)
    for (sql,) in src.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
        disk.execute(sql)
    for table in ("parts", "part_sources", "doc_revisions",
                  "claims_canonical"):
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        for row in rows:
            disk.execute(
                f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})",
                tuple(row))
    disk.commit()
    disk.close()
    return str(path)


def test_index_catalog_end_to_end_idempotent(con, tmp_path):
    catalog_path = _disk_catalog(tmp_path)
    stats1 = indexer.index_catalog(con, catalog_path, wave_path=None)
    stats2 = indexer.index_catalog(con, catalog_path, wave_path=None)
    assert stats1["added"] == stats1["units"] > 0
    assert stats2["added"] == 0
    assert stats2["replaced"] == stats2["units"] == stats1["units"]
    assert units.unit_count(con) == stats1["units"]


def test_index_catalog_dry_run_writes_nothing(con, tmp_path):
    catalog_path = _disk_catalog(tmp_path)
    stats = indexer.index_catalog(con, catalog_path, wave_path=None,
                                  dry_run=True)
    assert stats["units"] > 0
    assert units.unit_count(con) == 0
