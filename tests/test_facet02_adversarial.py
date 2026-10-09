"""PRD-FACET-02 adversarial tests (release directive item 6, 2026-10-08).

Numeric boundary conditions, sparse-axis ranking, conflicting claims,
missing family identity, multiple evidence sources per candidate — plus
the pilot category gate and the semantic-off invariant.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from harness.search import cohort, vendors


def _catalog_with(claims, parts=("P1", "P2", "P3", "P4"), vendor="infineon"):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE parts (opn TEXT PRIMARY KEY, mpn_base TEXT, vendor TEXT,
                            family TEXT, package TEXT, first_seen REAL,
                            source_doc_shas TEXT);
        CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,
            symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,
            unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,
            extractor_version TEXT, doc_sha256 TEXT, created_at REAL);
        CREATE TABLE conditions_typed (condition_verbatim TEXT, parsed TEXT,
            parser_version TEXT, schema TEXT, row_count INTEGER,
            updated_at REAL);
    """)
    for opn in parts:
        con.execute("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                    (opn, "", vendor, "", "", 1.0, "[]"))
    for opn, sym, qual, cond, val, unit, doc in claims:
        con.execute(
            "INSERT INTO claims_canonical (opn, symbol, qualifier,"
            " condition_norm, value, unit, value_text, provenance, extractor,"
            " extractor_version, doc_sha256, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (opn, sym, qual, cond, val, unit, str(val), "{}", "burn", "v1",
             doc, 1.0))
    con.commit()
    return con


AISLE = {"P1": "discrete-mosfets", "P2": "discrete-mosfets",
         "P3": "discrete-mosfets", "P4": "discrete-mosfets"}


def _search_with(evidence):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE units (unit_id TEXT PRIMARY KEY, doc_sha256 TEXT,
            grain TEXT, page INTEGER, locator TEXT, text_repr TEXT,
            structured TEXT, ident TEXT, vendor TEXT, doc_class TEXT,
            category TEXT, subcategory TEXT, family TEXT,
            applicability TEXT, rev_code TEXT, rev_date TEXT,
            extraction_version TEXT, verification_state TEXT,
            verification_source TEXT, retired INTEGER DEFAULT 0,
            retired_reason TEXT, created_at REAL);
    """)
    for uid, sha, opn, page in evidence:
        con.execute(
            "INSERT INTO units (unit_id, doc_sha256, grain, page, locator,"
            " text_repr, applicability, retired, created_at)"
            " VALUES (?,?,?,?,?,?,?,0,1.0)",
            (uid, sha, "claim", page, "{}", f"t {opn}",
             json.dumps([{"scope": "opn", "value": opn,
                          "coverage_kind": "primary"}])))
    con.commit()
    return con


# --- numeric boundary conditions ------------------------------------------

def test_bucket_boundaries_are_half_open():
    # lo <= v < hi: 30 lands in 30-60, NOT <=30; 1000 lands in >1000
    assert cohort.bucket_for("vds_rating_v", 30) == "30-60 V"
    assert cohort.bucket_for("vds_rating_v", 29.999) == "<=30 V"
    assert cohort.bucket_for("vds_rating_v", 1000) == ">1000 V"
    assert cohort.bucket_for("vds_rating_v", 999.9) == "500-1000 V"
    assert cohort.bucket_for("rds_on_ohm", 0.005) == "5-20 mOhm"
    assert cohort.bucket_for("rds_on_ohm", 0.0049999) == "<5 mOhm"


def test_interval_touching_boundary_does_not_overlap():
    # [3.3, 5.0] vs half-open bucket [1.2, 3.3): the shared endpoint 3.3 is
    # NOT in the bucket -> no overlap
    typed = {"kind": "interval", "value": None, "value_min": 3.3,
             "value_max": 5.0}
    assert not cohort.value_in_bucket("supply_voltage_v", typed, "1.2-3.3 V")
    assert cohort.value_in_bucket("supply_voltage_v", typed, "3.3-5 V")
    # [2.0, 3.3] DOES overlap [3.3, 5): the closed interval contains 3.3 and
    # the half-open bucket starts at 3.3 — the device operates at that point
    typed2 = {"kind": "interval", "value": None, "value_min": 2.0,
              "value_max": 3.3}
    assert cohort.value_in_bucket("supply_voltage_v", typed2, "1.2-3.3 V")
    assert cohort.value_in_bucket("supply_voltage_v", typed2, "3.3-5 V")
    # a strictly-interior interval overlaps only its bucket
    typed3 = {"kind": "interval", "value": None, "value_min": 3.0,
              "value_max": 3.2}
    assert cohort.value_in_bucket("supply_voltage_v", typed3, "1.2-3.3 V")
    assert not cohort.value_in_bucket("supply_voltage_v", typed3, "3.3-5 V")


def test_boundary_value_survives_constraint_roundtrip():
    cat = _catalog_with([("P1", "VDSS", None, "", 30, "V", "d1"),
                         ("P2", "VDSS", None, "", 30.0001, "V", "d2"),
                         ("P3", "VDSS", None, "", 60, "V", "d3")])
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map=AISLE)
    f = cohort.facets_for_cohort(co, constraints={"vds_rating_v": "30-60 V"})
    kept = f["candidate_count"]
    assert kept == 2          # P1 (30) and P2 (30.0001); P3 (60) is next bucket
    f2 = cohort.facets_for_cohort(co, constraints={"vds_rating_v": "<=30 V"})
    assert f2["candidate_count"] == 0   # 30 is NOT <=30-bucket (half-open)


# --- sparse-axis ranking ----------------------------------------------------

def test_sparse_axis_never_recommended():
    # 3 of 4 candidates have VDS; exactly 1 has a switching frequency.
    # The sparse axis has trivially high reduction and must NOT win.
    claims = [("P1", "VDSS", None, "", 600, "V", "d1"),
              ("P2", "VDSS", None, "", 650, "V", "d2"),
              ("P3", "VDSS", None, "", 1200, "V", "d3"),
              ("P4", "FSW", None, "", 500, "kHz", "d4")]
    cat = _catalog_with(claims)
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map=AISLE)
    f = cohort.facets_for_cohort(co)
    assert f["recommended_next"] != "switching_frequency_hz"
    rec = next(x for x in f["facets"]
               if x["dimension"] == f["recommended_next"])
    if rec["kind"] == "numeric":
        assert rec["coverage"] >= 0.5


def test_all_sparse_axes_recommend_none_gracefully():
    cat = _catalog_with([("P1", "FSW", None, "", 500, "kHz", "d1")],
                        parts=("P1", "P2", "P3", "P4", "P5", "P6",
                               "P7", "P8", "P9", "P10"))
    aisle = {f"P{i}": "discrete-mosfets" for i in range(1, 11)}
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map=aisle)
    f = cohort.facets_for_cohort(co)
    # structural facets (vendor/subcategory) may still rank; the point is
    # the sparse numeric axis must not be the recommendation
    assert f["recommended_next"] != "switching_frequency_hz"


# --- conflicting claims ------------------------------------------------------

def test_conflicting_conditions_keep_best_per_op_with_condition():
    # RDS(on) at two gate voltages: max_rating axis -> the smaller value
    # wins, and its condition travels with it (no condition mixing).
    claims = [("P1", "RDS(ON)", "max", "vgs=10 v", 20, "mΩ", "d1"),
              ("P1", "R_DS(ON)", "max", "vgs=4.5 v", 40, "mΩ", "d1")]
    cat = _catalog_with(claims, parts=("P1",))
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map={"P1": "discrete-mosfets"})
    typed = co["candidates"]["P1"].attributes["rds_on_ohm"]
    assert abs(typed["value"] - 0.02) < 1e-12
    assert typed["condition"] == "vgs=10 v"


def test_conflicting_values_across_documents_never_merge():
    # Same symbol, two docs, genuinely different values: min_rating keeps
    # the strongest; the weaker is not averaged, hidden, or merged.
    claims = [("P1", "VDSS", None, "", 600, "V", "d1"),
              ("P1", "VDSS", None, "", 650, "V", "d2")]
    cat = _catalog_with(claims, parts=("P1",))
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map={"P1": "discrete-mosfets"})
    assert co["candidates"]["P1"].attributes["vds_rating_v"]["value"] == 650


# --- missing family identity --------------------------------------------------

def test_missing_family_never_fabricated():
    claims = [("P1", "VDSS", None, "", 600, "V", "d1"),
              ("P2", "VDSS", None, "", 650, "V", "d2")]
    cat = _catalog_with(claims, parts=("P1", "P2"))
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map=AISLE)
    assert co["grain"] == "opn"
    f = cohort.facets_for_cohort(co)
    assert all(x["dimension"] != "family" for x in f["facets"])
    for cand in co["candidates"].values():
        assert not hasattr(cand, "family") or cand.category is not None
    assert any("family" in n for n in co["notes"])


# --- multiple evidence sources per candidate ----------------------------------

def test_multi_doc_evidence_counted_once_with_grade_split():
    cat = _catalog_with([("P1", "VDSS", None, "", 600, "V", "d1")],
                        parts=("P1",))
    search = _search_with([
        ("u1", "a" * 64, "P1", 2),               # graded (real sha + page)
        ("u2", "b" * 64, "P1", 5),               # graded
        ("u3", "unhashed:P1", "P1", None),       # discovery-only
    ])
    co = cohort.build_cohort(catalog_con=cat, search_con=search,
                             category="power", aisle_map=AISLE)
    p1 = co["candidates"]["P1"]
    assert len(p1.evidence_unit_ids) == 3        # three sources...
    f = cohort.facets_for_cohort(co)
    assert f["candidate_count"] == 1             # ...one candidate
    assert f["evidence_units"] == 3
    assert f["evidence_grade_units"] == 2
    assert f["discovery_only_units"] == 1
    # R5: the cohort listing carries evidence references, capped and sorted
    listed = f["candidates"][0]
    assert listed["opn"] == "P1"
    assert listed["evidence_units"] == 3
    assert listed["evidence_refs"] == ["u1", "u2", "u3"]


def test_unknown_only_candidate_stays_in_cohort():
    # a candidate with zero recognized claims is unknown everywhere but is
    # NOT dropped from the cohort (no evidence != unsuitable)
    cat = _catalog_with([("P1", "VDSS", None, "", 600, "V", "d1")],
                        parts=("P1", "P2"))
    co = cohort.build_cohort(catalog_con=cat, search_con=None,
                             category="power", aisle_map=AISLE)
    f = cohort.facets_for_cohort(co)
    assert f["candidate_count"] == 2
    vds = next(x for x in f["facets"] if x["dimension"] == "vds_rating_v")
    assert vds["unknown_candidates"] == 1


# --- pilot gate + semantic-off invariant --------------------------------------

def test_vendor_registry_rejects_lookalikes():
    # near-miss strings must not resolve by fuzzy luck
    assert vendors.resolve("infineon technologies ag")["resolved"] is False
    assert vendors.resolve("infineo")["resolved"] is False
    assert vendors.resolve("TI-embedded")["resolved"] is False
    # but registered aliases resolve deterministically
    for alias in ("infineon", "INFINEON.COM", " Infineon "):
        assert vendors.resolve(alias)["canonical"] == "infineon"

# --- API pilot gate + semantic-off invariant (release items 7, 9) ------------

@pytest.fixture()
def api_server(monkeypatch, tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from harness.search import api, indexer, units

    # fixture corpus -> temp search db; gate config forced to power-only
    corpus = tmp_path.parent.parent / "Harnessv1-facet02/tests/fixtures/gold/search_fixture_corpus.jsonl"
    from pathlib import Path
    corpus = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"
    db = tmp_path / "search.db"
    con = units.connect(str(db))
    catalog = indexer.catalog_from_corpus_jsonl(corpus)
    merged = list(indexer.claim_units(catalog)) + list(indexer.family_units(catalog))
    grouped = {}
    for u in merged:
        grouped.setdefault((u["doc_sha256"], u["extraction_version"]), []).append(u)
    for (doc, version), group in grouped.items():
        units.replace_document(con, group, doc_sha256=doc, extraction_version=version)
    con.close()
    monkeypatch.setattr(api, "SEARCH_DB", str(db))
    monkeypatch.setattr(api, "CATALOG", str(tmp_path / "absent-catalog.db"))
    monkeypatch.setattr(api, "FACET_PILOT_CATEGORIES", frozenset({"power"}))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, body):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_gated_categories_refused_with_reason(api_server):
    for cat in ("mcu", "connectors"):
        status, body = _get(f"{api_server}/v1/discovery/facets?category={cat}")
        assert status == 403
        assert body["error"] == "category_gated"
        assert body["allowed"] == ["power"]
        assert "FACET02_PILOT_HANDOFF" in body["notice"]


def test_gate_missing_catalog_fails_closed(api_server):
    # power passes the gate but the cohort source is absent -> 500-class
    # error must not masquerade as an empty cohort
    status, body = _get(f"{api_server}/v1/discovery/facets?category=power")
    assert status >= 500 or "error" in body


def test_semantic_off_by_default(api_server):
    # no SEARCH_EMBED_URL configured -> FTS-only; no semantic rationale
    status, body = _post(f"{api_server}/v1/search",
                         {"query": "rdson 3.9 milliohm", "limit": 5})
    assert status == 200
    for unit in body["units"]:
        assert not any("semantic" in r for r in unit["rationale"])
    assert body["qualification"] == "none"
