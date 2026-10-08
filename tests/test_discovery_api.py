"""Discovery service + API tests (temp DBs, synthetic + optional catalog)."""

from __future__ import annotations

import json
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from harness.discovery import service, store
from harness.discovery.evaluator import Atom, evaluate_part


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "designs.db")


SYNTH_POP = {
    "GOOD-PART": [
        {"symbol": "VDSS", "value": 75.0, "condition": "",
         "provenance": {"quote": "75"}},
        {"symbol": "IDDC", "value": 8.0, "condition": "",
         "provenance": {"quote": "8"}},
    ],
    "WEAK-PART": [
        {"symbol": "VDSS", "value": 30.0, "condition": "",
         "provenance": {"quote": "30"}},
        {"symbol": "IDDC", "value": 6.0, "condition": "",
         "provenance": {"quote": "6"}},
    ],
    "OPAQUE-PART": [
        {"symbol": "QG", "value": 30.0, "provenance": {"quote": "30"}},
    ],
}


def test_design_lifecycle(db):
    design_id = service.create_design(db, aisle="power", name="24V rail")
    assert design_id.startswith("dw-")

    atoms = service.add_requirements(db, design_id, [
        {"axis": "vds_rating_v", "value": 36.0},
        {"axis": "id_continuous_a", "value": 5.0},
    ])
    assert {a["axis"] for a in atoms} == {
        "vds_rating_v", "id_continuous_a"}

    summary = service.discover(db, design_id, population=SYNTH_POP)
    assert summary["candidates"] == 3
    assert summary["cohorts"]["passing"] == 1     # GOOD-PART
    assert summary["cohorts"]["eliminated"] == 1  # WEAK-PART (30 V)
    assert summary["cohorts"]["unknown"] == 1     # OPAQUE-PART
    assert summary["corpus_release"].startswith("dw-power-")

    # the release pins reproducibility (spec 45)
    got = service.get_design(db, design_id)
    assert got["latest_result"]["cohorts"]["passing"] == 1

    led = service.ledger(db, design_id, cohort="eliminated")
    assert led["rows"][0]["part"] == "WEAK-PART"
    v = led["rows"][0]["verdicts"]["vds_rating_v"]
    assert v["verdict"] == "FAIL"
    assert v["evidence"]["provenance"]["quote"] == "30"


def test_requirement_override_replaces_atom(db):
    design_id = service.create_design(db)
    service.add_requirements(db, design_id, [
        {"axis": "vds_rating_v", "value": 36.0}])
    service.add_requirements(db, design_id, [
        {"axis": "vds_rating_v", "value": 60.0, "margin_pct": 20.0}],
        replace=True)
    atoms = service.add_requirements(db, design_id, [])
    active = [a for a in atoms if a["axis"] == "vds_rating_v"]
    assert len(active) == 1
    assert active[0]["value"] == 60.0
    assert active[0]["margin_pct"] == 20.0


def test_not_sure_answer_is_recorded_not_enforced(db):
    """Spec 31-32: 'not sure' keeps the atom out of evaluation."""
    design_id = service.create_design(db)
    service.add_requirements(db, design_id, [
        {"axis": "vds_rating_v", "value": 36.0},
        {"axis": "id_continuous_a", "value": 5.0,
         "state": "not_sure"},
    ])
    atoms = service.add_requirements(db, design_id, [])
    assert {a["axis"] for a in atoms} == {"vds_rating_v"}  # active only


def test_discover_requires_atoms(db):
    design_id = service.create_design(db)
    with pytest.raises(ValueError):
        service.discover(db, design_id, population=SYNTH_POP)


def test_next_question_ranks_by_expected_reduction(db):
    design_id = service.create_design(db)
    service.add_requirements(db, design_id, [
        {"axis": "vds_rating_v", "value": 36.0}])
    q = service.next_question(db, design_id, population=SYNTH_POP)
    axes = [x["axis"] for x in q["questions"]]
    assert "vds_rating_v" not in axes       # already answered
    assert "id_continuous_a" in axes
    id_q = next(x for x in q["questions"] if x["axis"] == "id_continuous_a")
    assert id_q["askable"] is True
    assert id_q["parts_with_claims"] == 2
    assert id_q["expected_fail_at_median"] >= 0
    assert "help" in id_q


def test_ledger_before_discover_is_error(db):
    design_id = service.create_design(db)
    with pytest.raises(ValueError):
        service.ledger(db, design_id)


def test_api_smoke(db, tmp_path):
    """HTTP front door: full loop over a live ephemeral server."""
    import os
    import threading
    import urllib.request

    catalog = tmp_path / "catalog.db"
    con = sqlite3.connect(catalog)
    con.executescript(
        "CREATE TABLE parts (opn TEXT PRIMARY KEY, mpn_base TEXT, vendor"
        " TEXT, family TEXT, package TEXT, first_seen REAL,"
        " source_doc_shas TEXT);"
        "CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,"
        " symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,"
        " unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,"
        " extractor_version TEXT, doc_sha256 TEXT, created_at REAL);")
    con.execute("INSERT INTO parts VALUES ('GOOD-PART',NULL,NULL,NULL,"
                "NULL,0,'[]')")
    con.execute("INSERT INTO claims_canonical (opn, symbol, value,"
                " provenance) VALUES ('GOOD-PART','VDSS',75.0,?)",
                (json.dumps({"quote": "75"}),))
    con.commit()
    con.close()

    os.environ["DESIGNS_DB"] = db
    os.environ["DISCOVERY_CATALOG"] = str(catalog)
    os.environ["DISCOVERY_PORT"] = "0"

    # import AFTER env: module constants bind at import time
    from http.server import ThreadingHTTPServer
    from harness.discovery import api as api_mod

    server = ThreadingHTTPServer(("127.0.0.1", 0), api_mod.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"

        def post(path, body):
            req = urllib.request.Request(
                base + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        def get(path):
            with urllib.request.urlopen(base + path) as r:
                return json.loads(r.read())

        made = post("/designs", {
            "aisle": "power", "name": "smoke",
            "atoms": [{"axis": "vds_rating_v", "value": 36.0}]})
        assert made["design_id"].startswith("dw-")
        summary = post(f"/designs/{made['design_id']}/discover", {})
        assert summary["cohorts"]["passing"] == 1
        led = get(f"/designs/{made['design_id']}/ledger?cohort=passing")
        assert led["rows"][0]["part"] == "GOOD-PART"
        q = get(f"/designs/{made['design_id']}/next-question")
        assert any(x["axis"] == "id_continuous_a" for x in q["questions"])
        got = get(f"/designs/{made['design_id']}")
        assert got["latest_result"]["cohorts"]["passing"] == 1
    finally:
        server.shutdown()


def test_curve_evidence_route_is_advisory_only(db, tmp_path):
    """PRD-CURVE-02 R7: the curve-evidence route serves the evidence-query
    contract and can never promote curves into hard-elimination rules.

    Patches the api module attributes directly (its env constants bind at
    import time) and restores them, so test_api_smoke's first-import env
    trick above keeps working."""

    import threading
    import urllib.request

    from harness.discovery import api as api_mod
    from harness.discovery.curves import load_reference_curves

    pilot = Path(__file__).parent / "fixtures" / "gold" / \
        "curve_evidence_pilot"
    curves = load_reference_curves(
        [pilot / "tps548c26_p10.json", pilot / "lmr33610_p8.json"]
    )
    saved = (api_mod.DESIGNS_DB, api_mod.CATALOG,
             api_mod.CURVE_EVIDENCE_GLOB, api_mod._CURVES_CACHE)
    api_mod.DESIGNS_DB = db
    api_mod.CURVE_EVIDENCE_GLOB = str(pilot / "nothing-here.json")
    api_mod._CURVES_CACHE = curves  # inject; the glob points nowhere
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), api_mod.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"

            def post(path, body):
                req = urllib.request.Request(
                    base + path, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())

            design_id = service.create_design(db, aisle="power",
                                              name="12V rail")
            body = {
                "phenomenon": "efficiency_vs_load",
                "operating_point": {"x": 10.0},
                "conditions": {"vin_v": 12.0, "vout_v": 1.1,
                               "categorical": {"mode": "fccm"}},
            }
            out = post(f"/designs/{design_id}/curve-evidence", body)
            assert out["promotion"] == "none"
            assert out["design_id"] == design_id
            assert len(out["results"]) == 8
            assert out["results"][0]["guarantee"] is False
            assert out["derived"]["status"] == "proposal"
            assert out["not_usable"], "reasons a curve cannot be used"
        finally:
            server.shutdown()
    finally:
        (api_mod.DESIGNS_DB, api_mod.CATALOG,
         api_mod.CURVE_EVIDENCE_GLOB, api_mod._CURVES_CACHE) = saved
