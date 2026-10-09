"""PRD-FACET-02 validation suite (R6) — candidate-centric cohort narrowing.

Ten required validations, each one test:
 1 no evidence-unit inflation of candidate counts
 2 no family-to-OPN scope expansion
 3 correct range and SI-unit filtering
 4 correct unknown handling
 5 deterministic vendor normalization
 6 no unsupported engineering eliminations
 7 consistent facet counts after successive selections
 8 evidence-grade versus discovery-only separation
 9 correct behavior when multiple documents support one candidate
10 deterministic rebuild
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from harness.search import axes, cohort, vendors

POWER_AISLE = {"PART-A": "discrete-mosfets", "PART-B": "discrete-mosfets",
               "PART-C": "discrete-mosfets", "PART-UNK": "discrete-mosfets"}


def _catalog(tmp_path):
    path = tmp_path / "catalog.db"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE parts (opn TEXT PRIMARY KEY, mpn_base TEXT, vendor TEXT,
                            family TEXT, package TEXT, first_seen REAL,
                            source_doc_shas TEXT);
        CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,
            symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,
            unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,
            extractor_version TEXT, doc_sha256 TEXT, created_at REAL);
        CREATE TABLE conditions_typed (condition_verbatim TEXT,
            parsed TEXT, parser_version TEXT, schema TEXT, row_count INTEGER,
            updated_at REAL);
    """)
    for opn in ("PART-A", "PART-B", "PART-C", "PART-UNK"):
        con.execute("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                    (opn, "", "infineon.com", "", "TO-247", 1.0, "[]"))
    claims = [
        # PART-A: 650 V, 30 A, 20 mOhm
        ("PART-A", "VDSS", None, "", 650, "V", "650", "sha_a"),
        ("PART-A", "ID", None, "", 30, "A", "30", "sha_a"),
        ("PART-A", "RDS(ON)", "max", "VGS=10 V", 20, "mΩ", "20", "sha_a"),
        # PART-B: 100 V, 120 A, 3.9 mOhm
        ("PART-B", "VDSS", None, "", 100, "V", "100", "sha_b"),
        ("PART-B", "ID", None, "", 120, "A", "120", "sha_b"),
        ("PART-B", "R_DS(ON)", "max", "VGS=10 V", 3.9, "mΩ", "3.9", "sha_b"),
        # PART-C: 600 V, 10 A (no RDS(on))
        ("PART-C", "VDSS", None, "", 600, "V", "600", "sha_c"),
        ("PART-C", "ID", None, "", 10, "A", "10", "sha_c"),
        # PART-UNK: no recognised symbols at all
        ("PART-UNK", "FOO", None, "", 1, "x", "1", "sha_u"),
    ]
    for opn, sym, qual, cond, val, unit, vt, doc in claims:
        con.execute(
            "INSERT INTO claims_canonical (opn, symbol, qualifier,"
            " condition_norm, value, unit, value_text, provenance, extractor,"
            " extractor_version, doc_sha256, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (opn, sym, qual, cond, val, unit, vt,
             json.dumps({"quote": vt}), "burn", "v1", doc, 1.0))
    con.commit()
    return con


def _search(tmp_path, with_evidence=True):
    path = tmp_path / "search.db"
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
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
    if with_evidence:
        rows = [
            # PART-A: two docs support it (multi-document, R6.9)
            ("u-a1", "a" * 64, "claim", "PART-A", 2),
            ("u-a2", "b" * 64, "claim", "PART-A", 2),
            # PART-B: discovery-only (unhashed, no page)
            ("u-b1", "unhashed:PART-B", "claim", "PART-B", None),
            # PART-C: one graded section unit
            ("u-c1", "c" * 64, "section", "PART-C", 3),
        ]
        for uid, sha, grain, opn, page in rows:
            con.execute(
                "INSERT INTO units (unit_id, doc_sha256, grain, page,"
                " locator, text_repr, applicability, retired, created_at)"
                " VALUES (?,?,?,?,?,?,?,0,1.0)",
                (uid, sha, grain, page, '{"kind":"text_span"}', f"t {opn}",
                 json.dumps([{"scope": "opn", "value": opn,
                              "coverage_kind": "primary"}])))
    con.commit()
    return con


def _cohort(tmp_path, **kw):
    return cohort.build_cohort(
        catalog_con=_catalog(tmp_path),
        search_con=_search(tmp_path, kw.pop("with_evidence", True)),
        category="power", aisle_map=POWER_AISLE, **kw)


# 1 — no evidence-unit inflation
def test_candidate_counts_are_not_evidence_unit_counts(tmp_path):
    f = cohort.facets_for_cohort(_cohort(tmp_path))
    assert f["candidate_count"] == 4              # 4 OPNs, NOT 4 units
    # PART-A has two evidence units but is ONE candidate
    assert f["evidence_units"] == 4               # units counted separately
    vendor_facet = next(x for x in f["facets"]
                        if x["dimension"] == "vendor")
    infineon = next(v for v in vendor_facet["values"]
                    if v["value"] == "infineon")
    assert infineon["candidates"] == 4            # not the unit total


# 2 — no family-to-OPN scope expansion
def test_no_family_to_opn_expansion(tmp_path):
    co = _cohort(tmp_path)
    assert co["grain"] == "opn"
    assert any("family" in n for n in co["notes"])
    # a claim written against PART-A never lands on PART-B
    assert "vds_rating_v" in co["candidates"]["PART-A"].attributes
    assert co["candidates"]["PART-A"].attributes["vds_rating_v"]["value"] \
        == 650.0
    assert co["candidates"]["PART-B"].attributes["vds_rating_v"]["value"] \
        == 100.0


# 3 — range + SI-unit filtering
def test_si_normalization_and_range_filtering(tmp_path):
    co = _cohort(tmp_path)
    # mΩ -> ohm: 20 mΩ = 0.02 Ω, 3.9 mΩ = 0.0039 Ω
    a = co["candidates"]["PART-A"].attributes["rds_on_ohm"]
    b = co["candidates"]["PART-B"].attributes["rds_on_ohm"]
    assert abs(a["value"] - 0.02) < 1e-9 and a["unit"] == "ohm"
    assert abs(b["value"] - 0.0039) < 1e-9
    # range bucket
    assert cohort.bucket_for("vds_rating_v", 650) == "500-1000 V"
    assert cohort.bucket_for("rds_on_ohm", 0.0039) == "<5 mOhm"
    f = cohort.facets_for_cohort(co, constraints={"vds_rating_v": "500-1000 V"})
    assert f["candidate_count"] == 2              # PART-A (650) + PART-C (600)


# 4 — unknown handling
def test_unknown_candidates_reported_and_not_eliminated(tmp_path):
    co = _cohort(tmp_path)
    f = cohort.facets_for_cohort(co)
    rds = next(x for x in f["facets"] if x["dimension"] == "rds_on_ohm")
    # PART-C and PART-UNK have no RDS(on) -> unknown, still candidates
    assert rds["unknown_candidates"] == 2
    assert f["candidate_count"] == 4
    part_c = co["candidates"]["PART-C"]
    assert part_c.axis_value("rds_on_ohm") is None   # unknown, not eliminated
    assert "PART-C" in co["candidates"]


# 5 — deterministic vendor normalization
def test_vendor_normalization_deterministic():
    assert vendors.resolve("infineon.com")["canonical"] == "infineon"
    assert vendors.resolve("infineon")["canonical"] == "infineon"
    assert vendors.resolve("TI")["canonical"] == "texas-instruments"
    assert vendors.resolve("ti.com")["canonical"] == "texas-instruments"
    assert vendors.resolve("Texas Instruments")["canonical"] == \
        "texas-instruments"
    # placeholders never guessed
    assert vendors.resolve("unknown")["canonical"] is None
    assert vendors.resolve("unknown")["rule"] == "placeholder"
    assert vendors.resolve("")["resolved"] is False
    # provenance preserved
    r = vendors.resolve("ti.com")
    assert r["original"] == "ti.com" and r["rule"] == "alias"


# 6 — no unsupported engineering eliminations
def test_unknown_is_not_eliminated_by_a_hard_requirement(tmp_path):
    co = _cohort(tmp_path)
    # engineer requires >= 500 V; PART-B (100 V) is known-unsuitable, but
    # PART-UNK (no VDS evidence) is UNKNOWN -> must not be called unsuitable
    f = cohort.facets_for_cohort(co, constraints={"vds_rating_v": "500-1000 V"})
    assert f["candidate_count"] == 2              # PART-A (650) + PART-C (600)
    # PART-UNK has no VDS: unplaced and reported, never judged unsuitable
    assert "PART-UNK" in f["unknown_excluded"]["vds_rating_v"]
    assert any("never judged unsuitable" in n for n in f["notes"])


# 7 — consistent facet counts after successive selections
def test_successive_selection_consistency(tmp_path):
    co = _cohort(tmp_path)
    step1 = cohort.facets_for_cohort(co)
    assert step1["candidate_count"] == 4
    step2 = cohort.facets_for_cohort(
        co, constraints={"vds_rating_v": "500-1000 V"})
    assert step2["candidate_count"] == 2
    # a chosen dimension is no longer offered
    assert all(x["dimension"] != "vds_rating_v" for x in step2["facets"])
    step3 = cohort.facets_for_cohort(
        co, constraints={"vds_rating_v": "500-1000 V",
                         "id_continuous_a": "<5 A"})
    assert step3["candidate_count"] <= step2["candidate_count"]


# 8 — evidence-grade vs discovery-only separation
def test_evidence_grade_separation(tmp_path):
    co = _cohort(tmp_path)
    f = cohort.facets_for_cohort(co)
    assert f["evidence_grade_units"] >= 3         # a1,a2,c1 graded
    assert f["discovery_only_units"] >= 1         # b1 unhashed
    assert f["evidence_grade_units"] + f["discovery_only_units"] == \
        f["evidence_units"]


# 9 — multiple documents support one candidate
def test_multi_document_support_counted_once(tmp_path):
    co = _cohort(tmp_path)
    a = co["candidates"]["PART-A"]
    assert len(a.evidence_unit_ids) == 2          # two docs
    assert a.evidence_grade_units == 2
    f = cohort.facets_for_cohort(co)
    assert f["candidate_count"] == 4              # still one candidate


# 3b — interval semantics + vendor constraint
def test_interval_axis_overlaps_buckets(tmp_path):
    con = _catalog(tmp_path)
    # give PART-A an explicit VDD min/max interval (range-op axis)
    for qual, val in (("min", 2.0), ("max", 3.6)):
        con.execute(
            "INSERT INTO claims_canonical (opn, symbol, qualifier,"
            " condition_norm, value, unit, value_text, provenance, extractor,"
            " extractor_version, doc_sha256, created_at)"
            " VALUES ('PART-A','VDD',?,'',?,'V',?,'{}','burn','v1','sha_a',"
            " 1.0)", (qual, val, str(val)))
    con.commit()
    co = cohort.build_cohort(catalog_con=con,
                             search_con=_search(tmp_path),
                             category="power", aisle_map=POWER_AISLE)
    # VDD is a power-agnostic symbol; assert via the typed attribute shape
    # using the mcu axis set on a synthetic candidate instead
    from harness.search import axes as axis_mod
    vdd = axis_mod.axis_for("VDD")
    assert vdd is not None and vdd.op == "range"
    typed = co["candidates"]["PART-A"].attributes.get("supply_voltage_v")
    assert typed is not None and typed["kind"] == "interval"
    assert typed["value_min"] == 2.0 and typed["value_max"] == 3.6
    # interval [2.0, 3.6] overlaps BOTH neighbouring buckets honestly
    assert cohort.value_in_bucket("supply_voltage_v", typed, "1.2-3.3 V")
    assert cohort.value_in_bucket("supply_voltage_v", typed, "3.3-5 V")
    assert not cohort.value_in_bucket("supply_voltage_v", typed, "<1.2 V")


def test_vendor_constraint_filters_candidates(tmp_path):
    con = _catalog(tmp_path)
    con.execute("UPDATE parts SET vendor='ti.com' WHERE opn='PART-B'")
    con.commit()
    co = cohort.build_cohort(catalog_con=con,
                             search_con=_search(tmp_path),
                             category="power", aisle_map=POWER_AISLE)
    f_all = cohort.facets_for_cohort(co)
    assert f_all["candidate_count"] == 4
    f_ti = cohort.facets_for_cohort(co, constraints={"vendor":
                                                     "texas-instruments"})
    assert f_ti["candidate_count"] == 1
    f_inf = cohort.facets_for_cohort(co, constraints={"vendor": "infineon"})
    assert f_inf["candidate_count"] == 3


# 10 — deterministic rebuild
def test_deterministic_rebuild(tmp_path):
    f1 = cohort.facets_for_cohort(_cohort(tmp_path))
    f2 = cohort.facets_for_cohort(_cohort(tmp_path))
    assert f1 == f2
    assert json.dumps(f1, sort_keys=True) == json.dumps(f2, sort_keys=True)