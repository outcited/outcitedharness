"""Evidence-unit store tests (PRD-SEARCH-01 R1)."""

from __future__ import annotations

import json

import pytest

from harness.search import units


@pytest.fixture()
def con(tmp_path):
    return units.connect(str(tmp_path / "search.db"))


def _unit(**over):
    base = dict(
        doc_sha256="a" * 64, grain="claim",
        locator={"kind": "table_cell", "row_header": "VDS",
                 "column_header": "Max", "quote": "80"},
        text_repr="IPF039N08NF2S VDS = 80 V", extraction_version="v1",
        vendor="infineon.com", family="StrongIRFET",
        ident=["IPF039N08NF2S"],
        applicability=[{"scope": "opn", "value": "IPF039N08NF2S",
                        "coverage_kind": "primary"}],
        page=3)
    base.update(over)
    return units.make_unit(**base)


def test_make_unit_stamps_deterministic_id():
    a, b = _unit(), _unit()
    assert a["unit_id"] == b["unit_id"]
    other = _unit(extraction_version="v2")
    assert other["unit_id"] != a["unit_id"]


def test_make_unit_enforces_provenance_law():
    with pytest.raises(ValueError):
        _unit(doc_sha256="   ")
    with pytest.raises(ValueError):
        _unit(text_repr="  ")
    with pytest.raises(ValueError):
        _unit(locator={})


def test_make_unit_rejects_unknown_enums():
    with pytest.raises(ValueError):
        _unit(grain="paragraph")
    with pytest.raises(ValueError):
        _unit(verification_state="definitely")
    with pytest.raises(ValueError):
        _unit(doc_class="pamphlet")
    with pytest.raises(ValueError):
        _unit(applicability=[{"scope": "galaxy", "value": "x"}])
    with pytest.raises(ValueError):
        _unit(applicability=[{"scope": "opn", "value": "x",
                              "coverage_kind": "guaranteed"}])


def test_m4_verified_requires_source():
    with pytest.raises(ValueError):
        _unit(verification_state="m4_verified")
    assert _unit(verification_state="m4_verified",
                 verification_source="cell_verifier")["verification_source"] \
        == "cell_verifier"


def test_page_may_come_from_locator():
    unit = _unit(locator={"kind": "text_span", "page": 7}, page=None)
    assert unit["page"] == 7
    with pytest.raises(ValueError):
        _unit(page=0)


def test_burn_primary_coverage_is_kept_verbatim():
    unit = _unit(applicability=[{"scope": "opn", "value": "X",
                                 "coverage_kind": "burn-primary"}])
    assert unit["applicability"][0]["coverage_kind"] == "burn-primary"


def test_technical_normalize_folds_units_and_symbols():
    assert units.technical_normalize("RDS(on),max") == "rds on max"
    assert units.technical_normalize("100mA") == "100 ma"
    assert units.technical_normalize("3.3V") == "3 3 v"
    assert units.technical_normalize("rdson") == "rds on"
    assert units.technical_normalize("3v3 tolerant") == "3 3 v tolerant"
    assert units.technical_normalize("") == ""


def test_replace_document_is_idempotent(con):
    first = units.replace_document(con, [_unit()])
    assert first["added"] == 1 and first["removed"] == 0
    again = units.replace_document(con, [_unit()])
    assert again["added"] == 0 and again["replaced"] == 1
    assert units.unit_count(con) == 1


def test_replace_document_removes_stale_units(con):
    keep, stale = _unit(), _unit(locator={"kind": "text_span",
                                          "quote": "different"})
    units.replace_document(con, [keep, stale])
    result = units.replace_document(con, [keep])
    assert result["removed"] == 1
    assert units.get_unit(con, stale["unit_id"]) is None


def test_replace_document_collapses_duplicates(con):
    dup = _unit()
    units.replace_document(con, [dup, dict(dup)])
    assert units.unit_count(con) == 1


def test_new_extraction_version_supersedes_not_rewrites(con):
    v1 = _unit()
    v2 = _unit(extraction_version="v2", text_repr="better text")
    units.replace_document(con, [v1])
    units.replace_document(con, [v2])
    assert units.unit_count(con) == 2  # immutable: both versions retained
    got = units.get_unit(con, v1["unit_id"])
    assert got["text_repr"] == "IPF039N08NF2S VDS = 80 V"


def test_replace_document_rejects_mixed_docs(con):
    with pytest.raises(ValueError):
        units.replace_document(
            con, [_unit(), _unit(doc_sha256="b" * 64)])
    with pytest.raises(ValueError):
        units.replace_document(con, [])


def test_retire_document_soft_retires(con):
    unit = _unit()
    units.replace_document(con, [unit])
    assert units.retire_document(con, unit["doc_sha256"], "superseded") == 1
    got = units.get_unit(con, unit["unit_id"])
    assert got["retired"] and got["retired_reason"] == "superseded"
    assert units.unit_count(con, active_only=True) == 0
    assert units.unit_count(con, active_only=False) == 1


def test_vectors_roundtrip_and_cosine(con):
    unit = _unit()
    units.replace_document(con, [unit])
    units.attach_vector(con, unit["unit_id"], "bge-test", [1.0, 0.0, 0.0])
    assert units.vectors_available(con)
    row = con.execute("SELECT vec, dim FROM vectors WHERE unit_id=?",
                      (unit["unit_id"],)).fetchone()
    assert row["dim"] == 3
    vec = units.unpack_vector(row["vec"])
    assert abs(units.cosine(vec, [1.0, 0.0, 0.0]) - 1.0) < 1e-6
    assert units.cosine(vec, [0.0, 1.0, 0.0]) == 0.0


def test_release_pins_content_and_updates(con):
    units.replace_document(con, [_unit()])
    rel1 = units.index_release(con)
    assert rel1["unit_count"] == 1
    cached = units.index_release(con)
    assert cached == rel1  # cached, not recomputed
    units.replace_document(con, [_unit(extraction_version="v2")])
    rel2 = units.index_release(con)
    assert rel2["release"] != rel1["release"]
    logged = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    assert logged == 2  # append-only build log for rollback audits


def test_locator_is_canonical_json(con):
    unit = _unit(locator={"column_header": "Max", "row_header": "VDS",
                          "kind": "table_cell"})
    got = units.get_unit(
        con, units.replace_document(con, [unit]) and unit["unit_id"])
    assert got["locator"] == {"kind": "table_cell", "row_header": "VDS",
                              "column_header": "Max"}
    assert got["applicability"][0]["coverage_kind"] == "primary"
