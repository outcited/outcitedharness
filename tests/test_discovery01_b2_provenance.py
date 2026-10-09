"""B2 regression — provenance must resolve from the real M4 evidence schema.

Assessment evidence (results/discovery01-production-readiness/
provenance_probe.json): a supported journey cited 4 curve evidence ids, the
resolver returned ``documents: {}`` and listed all 4 as unresolved — even
though the two ``document_sha256`` values behind those rankings match 9 and
22 active units in the M5 index. Cause: M4 08C nests provenance under
``entry.source.*`` while ``resolve_evidence_refs`` read only top-level
``document_sha256`` / ``datasheet_sha256``.

These tests pin the corrected contract:
  * nested ``source.document_sha256`` is authoritative and resolves;
  * the legacy top-level path still resolves when nested is absent;
  * disagreement between the two fails closed as an explicit conflict
    (never a silent pick);
  * a missing sha stays explicitly unresolved with a reason;
  * a sha absent from the index stays explicitly unresolved with a reason;
  * resolution is reported per evidence id, with the M4 locator carried
    through so provenance can be followed to the held artifact.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.search import identity, m4_client, orchestrator

SHA_NESTED = "0cef2db3d1a1b022d13de380a3038cf9f3a2c5155bc12cbd44daae708eaba175"
SHA_OTHER = "023354480de7bf3ed3ce37cbd048326a750873da92a613100e5a69818a8b4ad2"

FIXTURES = Path(__file__).parent / "fixtures/discovery01"
JOURNEYS = FIXTURES / "m4_frozen_journeys_v1.json"
CROSSWALK = FIXTURES / "m4_identity_crosswalk_v1.json"
CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"


def _index(rows):
    """Minimal read-only-shaped M5 index over the columns the resolver uses."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE units (unit_id TEXT PRIMARY KEY, "
                "doc_sha256 TEXT, page INTEGER, locator TEXT, "
                "verification_state TEXT, retired INTEGER)")
    for i, r in enumerate(rows):
        con.execute("INSERT INTO units VALUES (?,?,?,?,?,?)", (
            r.get("unit_id", f"u{i}"), r["doc_sha256"], r.get("page"),
            json.dumps(r.get("locator") or {}),
            r.get("verification_state", "unverified"),
            int(r.get("retired", 0))))
    return con


def _answer(entries, ineligible=()):
    return {"comparison": {"condition_matched_entries": entries,
                           "approximate_scenario_entries": []},
            "hard_eligibility": {"eligible": [], "ineligible": list(
                ineligible), "unknown": []}}


def _entry(eid, *, nested=None, top_document=None, top_datasheet=None,
           page=14, figure=4, part="SiC461"):
    e = {"evidence_id": eid, "part": part, "value": 93.9}
    if nested is not None:
        e["source"] = {"document_sha256": nested, "document_revision": None,
                       "page_1based": page, "figure_index": figure,
                       "caption": "Fig. 15 - Efficiency vs. Output Current",
                       "source_artifact": f"{nested[:12]}.pdf",
                       "locator": {"page_1based": page,
                                   "figure_index": figure,
                                   "series_index": 2}}
    if top_document is not None:
        e["document_sha256"] = top_document
    if top_datasheet is not None:
        e["datasheet_sha256"] = top_datasheet
    return e


# --- nested (authoritative) source schema -----------------------------------

def test_b2_nested_source_sha_resolves_to_indexed_units():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14,
                   "locator": {"bbox": [1, 2, 3, 4]}},
                  {"doc_sha256": SHA_NESTED, "page": 14},
                  {"doc_sha256": SHA_NESTED, "page": 15}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-1", nested=SHA_NESTED)]))
    assert out["unresolved_evidence_ids"] == []
    assert out["documents"][SHA_NESTED]["indexed_units"] == 3
    view = out["evidence_ids"]["curve-1"]
    assert view["resolved"] is True
    assert view["document_sha256"] == SHA_NESTED
    assert view["sha_path"] == "source.document_sha256"
    assert view["page_1based"] == 14 and view["figure_index"] == 4
    assert view["source_artifact"] == f"{SHA_NESTED[:12]}.pdf"
    assert view["m4_page_present_in_index"] is True
    assert "evidence_grade" in view["evidence_grades"]
    assert out["resolution"] == {"cited": 1, "resolved": 1, "unresolved": 0,
                                 "conflicts": 0}


def test_b2_approximate_entries_are_resolved_too():
    con = _index([{"doc_sha256": SHA_OTHER, "page": 9}])
    answer = _answer([])
    answer["comparison"]["approximate_scenario_entries"] = [
        _entry("curve-approx", nested=SHA_OTHER, page=9, part="TPS548C26")]
    out = orchestrator.resolve_evidence_refs(con, answer)
    assert out["curve_evidence_ids"] == ["curve-approx"]
    assert out["evidence_ids"]["curve-approx"]["resolved"] is True
    assert out["unresolved_evidence_ids"] == []


# --- legacy top-level path --------------------------------------------------

def test_b2_legacy_top_level_document_sha_still_resolves():
    con = _index([{"doc_sha256": SHA_OTHER, "page": 7}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-legacy", top_document=SHA_OTHER,
                             page=7)]))
    view = out["evidence_ids"]["curve-legacy"]
    assert view["resolved"] is True
    assert view["sha_path"] == "document_sha256"
    assert out["provenance_conflicts"] == []


def test_b2_legacy_top_level_datasheet_sha_still_resolves():
    con = _index([{"doc_sha256": SHA_OTHER, "page": 7}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-legacy2", top_datasheet=SHA_OTHER)]))
    assert out["evidence_ids"]["curve-legacy2"]["resolved"] is True
    assert out["evidence_ids"]["curve-legacy2"]["sha_path"] == \
        "datasheet_sha256"


def test_b2_nested_and_agreeing_top_level_resolve_once_without_conflict():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-agree", nested=SHA_NESTED,
                             top_document=SHA_NESTED)]))
    assert out["provenance_conflicts"] == []
    assert out["evidence_ids"]["curve-agree"]["resolved"] is True
    assert out["resolution"]["conflicts"] == 0


# --- disagreement fails closed ----------------------------------------------

def test_b2_conflicting_nested_and_top_level_fails_closed():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14},
                  {"doc_sha256": SHA_OTHER, "page": 3}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-conflict", nested=SHA_NESTED,
                             top_document=SHA_OTHER)]))
    assert out["evidence_ids"]["curve-conflict"]["resolved"] is False
    assert out["evidence_ids"]["curve-conflict"]["reason"] == \
        "provenance_conflict"
    assert out["unresolved_evidence_ids"] == ["curve-conflict"]
    conflict = out["provenance_conflicts"][0]
    assert conflict["evidence_id"] == "curve-conflict"
    assert conflict["nested_source_sha256"] == SHA_NESTED
    assert conflict["top_level_sha256"] == SHA_OTHER
    assert conflict["action"]
    assert out["resolution"]["conflicts"] == 1


# --- missing provenance stays explicit --------------------------------------

def test_b2_missing_sha_remains_explicitly_unresolved():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14}])
    out = orchestrator.resolve_evidence_refs(con, _answer([_entry(
        "curve-nosha")]))
    assert out["unresolved_evidence_ids"] == ["curve-nosha"]
    view = out["evidence_ids"]["curve-nosha"]
    assert view["resolved"] is False
    assert view["reason"] == "no_source_sha256"
    assert view["document_sha256"] is None
    assert out["documents"] == {}          # nothing invented


def test_b2_sha_absent_from_index_is_explicitly_unresolved():
    con = _index([{"doc_sha256": SHA_OTHER, "page": 1}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-notindexed", nested=SHA_NESTED)]))
    view = out["evidence_ids"]["curve-notindexed"]
    assert view["resolved"] is False
    assert view["reason"] == "document_not_in_m5_index"
    assert out["unresolved_evidence_ids"] == ["curve-notindexed"]
    assert out["documents"][SHA_NESTED]["indexed_units"] == 0


def test_b2_m4_page_absent_from_index_is_disclosed_not_hidden():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 3}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-pagediff", nested=SHA_NESTED,
                             page=14)]))
    view = out["evidence_ids"]["curve-pagediff"]
    assert view["resolved"] is True
    assert view["m4_page_present_in_index"] is False
    assert view["index_pages"] == [3]


def test_b2_evidence_id_in_m5_unit_namespace_still_resolves_directly():
    con = _index([{"unit_id": "curve-known-unit", "doc_sha256": SHA_OTHER,
                   "page": 5}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-known-unit")]))
    view = out["evidence_ids"]["curve-known-unit"]
    assert view["resolved"] is True
    assert view["reason"] is None
    assert view["resolution_path"] == "m5_unit_id"


def test_b2_hard_eligibility_rule_evidence_still_collected():
    con = _index([{"doc_sha256": SHA_OTHER, "page": 2}])
    answer = _answer([], ineligible=[{
        "candidate": "cand-x", "part": "LMR33610",
        "rules": [{"rule": "vin_min", "evidence": {
            "datasheet_sha256": SHA_OTHER}}]}])
    out = orchestrator.resolve_evidence_refs(con, answer)
    assert out["documents"][SHA_OTHER]["indexed_units"] == 1


def test_b2_retired_units_are_not_resurrected():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14, "retired": 1}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-retired", nested=SHA_NESTED)]))
    assert out["evidence_ids"]["curve-retired"]["resolved"] is False
    assert out["documents"][SHA_NESTED]["indexed_units"] == 0


def test_b2_no_filesystem_paths_leak():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-1", nested=SHA_NESTED)]))
    blob = json.dumps(out, default=str)
    assert "/Volumes/" not in blob and "/Users/" not in blob


def test_b2_precedence_rule_is_documented_in_the_payload():
    con = _index([{"doc_sha256": SHA_NESTED, "page": 14}])
    out = orchestrator.resolve_evidence_refs(
        con, _answer([_entry("curve-1", nested=SHA_NESTED)]))
    assert "source.document_sha256" in out["precedence"]
    assert "fail" in out["precedence"].lower()


# --- real journey: the assessment's 4/4 unresolved must resolve --------------

@pytest.mark.skipif(not (Path(CATALOG).exists() and Path(SEARCH_DB).exists()
                         and Path(JOURNEYS).exists()),
                    reason="live M5 stores or frozen M4 fixtures unavailable")
def test_b2_real_journey_resolves_every_cited_evidence_id():
    from harness.search import indexer
    catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    search.row_factory = sqlite3.Row
    icon = identity.connect(IDENTITY_DB) if Path(IDENTITY_DB).exists() \
        else None
    try:
        r = orchestrator.engineering_session(
            {"category": "power", "subcategory": "buck",
             "constraints": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
             "comparison": {"metric": "efficiency",
                            "mode": "condition_matched"}},
            catalog_con=catalog, search_con=search, identity_con=icon,
            aisle_map=indexer.aisle_map_from_wave(WAVE),
            transport=m4_client.FrozenTransport(JOURNEYS),
            crosswalk=orchestrator.load_crosswalk(CROSSWALK))
    finally:
        catalog.close()
        search.close()
        if icon:
            icon.close()

    ev = r["evidence"]
    cited = ev["curve_evidence_ids"]
    assert len(cited) == 4, f"expected the assessment's 4 cited ids: {cited}"
    assert ev["unresolved_evidence_ids"] == []
    assert ev["resolution"]["resolved"] == 4
    assert ev["provenance_conflicts"] == []
    assert ev["documents"], "no document resolved"
    for eid in cited:
        view = ev["evidence_ids"][eid]
        assert view["resolved"] is True
        assert view["document_sha256"]
        assert view["page_1based"] is not None
        assert view["indexed_units"] > 0
        assert view["source_artifact"]
    # provenance is followable to the held artifact
    assert all(d["indexed_units"] > 0 for d in ev["documents"].values())
    blob = json.dumps(r, default=str)
    assert "/Volumes/" not in blob and "/Users/" not in blob
