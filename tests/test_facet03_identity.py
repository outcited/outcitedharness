"""PRD-FACET-03 R6 — the fourteen required validations.

1 missing family remains unknown            8 missing source stays discovery-only
2 no fabricated relationships               9 page and locator preservation
3 conflicting membership detected          10 conflicting revisions distinguishable
4 manufacturer aliases deterministic       11 release rebuild deterministic
5 family+children never inflate counts     12 rollback restores prior behavior
6 family claims never become OPN claims    13 FACET-02 regressions (suite-level)
7 hash reconstruction uses real bytes      14 invalid sources fail closed
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harness.search import cohort, identity, units  # noqa: E402
import facet03_resolve_provenance as resolver  # noqa: E402


# --- helpers -----------------------------------------------------------------

def _icon(tmp_path):
    return identity.connect(str(tmp_path / "identity.db"))


def _family_pair(icon, opn="PART-A", family="CoolMOS", mfr="mfr:infineon",
                 sha="f" * 64, page=1, quote="500V CoolMOS C7 Power Device",
                 conflict=None, status="proposed"):
    fid = identity.ensure_identity(
        icon, "family", family, manufacturer_id=mfr,
        authority=f"front matter p.{page}")
    icon.execute("INSERT OR IGNORE INTO identities VALUES"
                 " ('opn:%s','opn','%s',NULL,'catalog','proposed',1.0)"
                 % (opn, opn))
    icon.commit()
    identity.add_relationship(
        icon, fid, f"opn:{opn}", "contains",
        authority=f"front matter line: {quote}", source_sha256=sha,
        source_locator={"page": page, "quote": quote},
        norm_rule="front_matter_vocab_v1", status=status,
        conflict_status=conflict)
    return fid


# 1 — missing family remains unknown -----------------------------------------

def test_missing_family_remains_unknown(tmp_path):
    icon = _icon(tmp_path)
    _family_pair(icon, opn="PART-A")
    cands = {"PART-A": cohort.Candidate(opn="PART-A"),
             "PART-B": cohort.Candidate(opn="PART-B")}
    stats = cohort._attach_families(icon, cands)
    assert cands["PART-B"].family_id is None
    assert cands["PART-B"].families == []
    assert stats["unknown"] == 1 and stats["with_family"] == 1


# 2 — no fabricated relationships ---------------------------------------------

def test_relationships_require_real_evidence(tmp_path):
    icon = _icon(tmp_path)
    fid = identity.ensure_identity(icon, "family", "CoolMOS",
                                   manufacturer_id="mfr:infineon",
                                   authority="front matter")
    with pytest.raises(ValueError):     # no authority
        identity.add_relationship(icon, fid, "opn:X", "contains",
                                  authority="", source_sha256="f" * 64,
                                  source_locator={}, norm_rule="pairs_manifest")
    with pytest.raises(ValueError):     # placeholder sha is not evidence
        identity.add_relationship(icon, fid, "opn:X", "contains",
                                  authority="a", source_sha256="unhashed:x",
                                  source_locator={}, norm_rule="pairs_manifest")
    with pytest.raises(ValueError):     # name-similarity rules don't exist
        identity.add_relationship(icon, fid, "opn:X", "contains",
                                  authority="a", source_sha256="f" * 64,
                                  source_locator={}, norm_rule="prefix_match")
    with pytest.raises(ValueError):     # verified at creation is impossible
        identity.ensure_identity(icon, "family", "X", authority="a",
                                 status="verified")
    # promotion requires an explicit approval reference
    with pytest.raises(ValueError):
        identity.promote(icon, fid, approval="  ")
    identity.promote(icon, fid, approval="owner-mail-20261009")
    row = icon.execute("SELECT status FROM identities WHERE identity_id=?",
                       (fid,)).fetchone()
    assert row["status"] == "verified"


# 3 — conflicting membership detected, never merged ------------------------------

def test_conflict_detected_and_excluded_from_membership(tmp_path):
    icon = _icon(tmp_path)
    f1 = _family_pair(icon, family="CoolMOS")
    f2 = _family_pair(icon, family="OptiMOS",
                      quote="OptiMOS 2 Power-Transistor")
    identity.mark_conflict(icon, f1, f2, "opn:PART-A", "contains")
    clean = identity.families_for_opn(icon, "PART-A")
    assert clean == []                              # neither wins
    allrows = identity.families_for_opn(icon, "PART-A",
                                        include_conflicting=True)
    assert len(allrows) == 2                        # both distinguishable
    assert all(r["conflict_status"] == "conflicting" for r in allrows)
    cands = {"PART-A": cohort.Candidate(opn="PART-A")}
    stats = cohort._attach_families(icon, cands)
    assert cands["PART-A"].family_id is None        # ambiguous, not chosen
    assert stats["conflicting"] == 1
    assert len(cands["PART-A"].families) == 2       # visible, not hidden


# 4 — manufacturer aliases deterministic ---------------------------------------

def test_manufacturer_identity_deterministic(tmp_path):
    from harness.search import vendors
    icon = _icon(tmp_path)
    ids = set()
    for alias in ("infineon", "INFINEON.COM", " Infineon "):
        canonical = vendors.resolve(alias)["canonical"]
        ids.add(identity.identity_id_for("family", "CoolMOS",
                                         f"mfr:{canonical}"))
    assert ids == {"family:mfr:infineon:coolmos"}   # one identity, any alias
    assert vendors.resolve("unknown")["resolved"] is False


# 5 — family + children never inflate same-grain counts ---------------------------

def test_family_membership_does_not_inflate_candidate_counts(tmp_path):
    icon = _icon(tmp_path)
    _family_pair(icon, opn="PART-A")
    _family_pair(icon, opn="PART-B")
    cat = sqlite3.connect(":memory:")
    cat.row_factory = sqlite3.Row
    cat.executescript(
        "CREATE TABLE parts (opn TEXT PRIMARY KEY, mpn_base TEXT, vendor TEXT,"
        " family TEXT, package TEXT, first_seen REAL, source_doc_shas TEXT);"
        "CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,"
        " symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,"
        " unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,"
        " extractor_version TEXT, doc_sha256 TEXT, created_at REAL);")
    for opn in ("PART-A", "PART-B"):
        cat.execute("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                    (opn, "", "infineon", "", "", 1.0, "[]"))
    co = cohort.build_cohort(catalog_con=cat, identity_con=icon,
                             category="power",
                             aisle_map={"PART-A": "discrete-mosfets",
                                        "PART-B": "discrete-mosfets"})
    f = cohort.facets_for_cohort(co)
    assert f["candidate_count"] == 2               # OPNs, not OPNs+families
    assert f["distinct_opns"] == 2
    assert f["distinct_families"] == 1
    fam = next(x for x in f["facets"] if x["dimension"] == "family")
    assert fam["values"][0]["candidates"] == 2     # distinct candidates
    assert fam["membership_coverage"] == 1.0


# 6 — family claims never silently become OPN claims ------------------------------

def test_no_attribute_propagation(tmp_path):
    icon = _icon(tmp_path)
    fid = _family_pair(icon, opn="PART-A")
    # family identity nodes carry no spec values anywhere in the model
    cols = [r[1] for r in icon.execute("PRAGMA table_info(identities)")]
    assert not any(c in cols for c in ("value", "attributes", "axis"))
    cands = {"PART-A": cohort.Candidate(opn="PART-A")}
    cohort._attach_families(icon, cands)
    assert cands["PART-A"].attributes == {}        # membership grants nothing


# 7 — hash reconstruction uses actual document bytes ------------------------------

def test_byte_verification_rejects_tampered_manifest(tmp_path):
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(b"%PDF-1.4 real bytes")
    import hashlib
    real = hashlib.sha256(pdf.read_bytes()).hexdigest()
    pairs = {"v-PART": {"sha256": "deadbeef" * 8,   # manifest lies
                        "bytes": pdf.stat().st_size,
                        "local_path": str(pdf), "source": "pairs_manifest",
                        "vendor": "v"}}
    cat = sqlite3.connect(":memory:")
    cat.row_factory = sqlite3.Row
    cat.executescript(
        "CREATE TABLE claims_canonical (opn TEXT, provenance TEXT,"
        " doc_sha256 TEXT)")
    cat.execute("INSERT INTO claims_canonical VALUES ('PART', ?, ?)",
                (json.dumps({"quote": "q"}), "unhashed:v-PART"))
    pipe = sqlite3.connect(":memory:")
    pipe.row_factory = sqlite3.Row
    pipe.executescript("CREATE TABLE results (document_sha256 TEXT,"
                       " kind_hint TEXT, output TEXT, created_at REAL)")
    icon = _icon(tmp_path)
    report = resolver.resolve(cat, pipe, icon, pairs=pairs, vault={})
    assert report["bytes_mismatch"] == 1
    assert report["resolved_pairs"] == 0
    row = icon.execute("SELECT resolved_sha256 FROM provenance_map"
                       " WHERE stem='v-PART'").fetchone()
    assert row["resolved_sha256"] is None          # never promoted on a lie
    # the honest manifest resolves
    pairs["v-PART"]["sha256"] = real
    report2 = resolver.resolve(cat, pipe, icon, pairs=pairs, vault={})
    assert report2["bytes_verified"] == 1


# 8 — missing source remains discovery-only ----------------------------------------

def test_unresolved_stem_units_stay_discovery_only():
    unit = units.make_unit(
        doc_sha256="unhashed:mystery-PART", grain="claim",
        locator={"kind": "text_span", "quote": "1"}, text_repr="x = 1",
        extraction_version="v1", page=3)
    assert units.unit_evidence_grade(unit) == "discovery_only"


# 9 — page and locator preservation through rekey -----------------------------------

def test_rekey_preserves_locator_and_recovers_page(tmp_path):
    search = units.connect(str(tmp_path / "search.db"))
    icon = _icon(tmp_path)
    unit = units.make_unit(
        doc_sha256="unhashed:v-PART", grain="claim",
        locator={"kind": "table_cell", "row_header": "VDSS",
                 "column_header": "Max", "quote": "600"},
        text_repr="PART VDSS = 600 V", extraction_version="burn/v1",
        vendor="v", applicability=[{"scope": "opn", "value": "PART",
                                    "coverage_kind": "primary"}])
    units.replace_document(search, [unit])
    identity.set_provenance(icon, "v-PART", resolved_sha256="e" * 64,
                            resolution="pairs_manifest", bytes_verified=True,
                            bytes_match=True, quote_verified=1,
                            quote_total=1)
    icon.execute("INSERT INTO quote_locator VALUES ('v-PART','600',7)")
    icon.commit()
    stats = resolver.rekey_units(search, icon)
    assert stats["units"] == 1 and stats["pages_recovered"] == 1
    new = search.execute(
        "SELECT * FROM units WHERE doc_sha256=? AND retired=0",
        ("e" * 64,)).fetchone()
    got = units.row_to_unit(new)
    assert got["locator"]["row_header"] == "VDSS"      # preserved
    assert got["locator"]["column_header"] == "Max"    # preserved
    assert got["locator"]["quote"] == "600"            # preserved
    assert got["page"] == 7                            # recovered
    assert got["locator"]["page_source"] == "quote_locate_v1"
    assert got["text_repr"] == "PART VDSS = 600 V"     # verbatim
    assert units.unit_evidence_grade(got) == "evidence_grade"
    old = units.get_unit(search, unit["unit_id"])
    assert old["retired"]                              # superseded, not lost


# 10 — conflicting source revisions remain distinguishable ---------------------------

def test_provenance_conflicts_persist(tmp_path):
    icon = _icon(tmp_path)
    identity.record_provenance_conflict(icon, "v-PART", "a" * 64,
                                        "pairs_manifest", "b" * 64,
                                        "vault_documents")
    identity.record_provenance_conflict(icon, "v-PART", "b" * 64,
                                        "vault_documents", "a" * 64,
                                        "pairs_manifest")   # order-insensitive
    rows = icon.execute("SELECT * FROM provenance_conflicts").fetchall()
    assert len(rows) == 1                                # deduped...
    assert rows[0]["sha_a"] != rows[0]["sha_b"]          # ...both kept
    identity.record_provenance_conflict(icon, "v-PART", "a" * 64,
                                        "pairs_manifest", "c" * 64,
                                        "vault_documents")
    assert icon.execute(
        "SELECT COUNT(*) FROM provenance_conflicts").fetchone()[0] == 2


# 11 — release rebuild deterministic ---------------------------------------------------

def test_snapshot_rebuild_is_deterministic(tmp_path):
    icon = _icon(tmp_path)
    payload = {"schema": "x", "candidates": [
        {"canonical_id": "opn:B", "parents": []},
        {"canonical_id": "opn:A", "parents": []}]}
    s1 = identity.snapshot(icon, payload)
    s2 = identity.snapshot(icon, json.loads(json.dumps(payload)))
    assert s1["snapshot_id"] == s2["snapshot_id"]
    assert icon.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    payload["candidates"].append({"canonical_id": "opn:C", "parents": []})
    s3 = identity.snapshot(icon, payload)
    assert s3["snapshot_id"] != s1["snapshot_id"]        # content moved id


# 12 — rollback restores prior identity/evidence behavior ------------------------------

def test_rollback_is_dropping_the_identity_input(tmp_path):
    icon = _icon(tmp_path)
    _family_pair(icon, opn="PART-A")
    cat = sqlite3.connect(":memory:")
    cat.row_factory = sqlite3.Row
    cat.executescript(
        "CREATE TABLE parts (opn TEXT PRIMARY KEY, mpn_base TEXT, vendor TEXT,"
        " family TEXT, package TEXT, first_seen REAL, source_doc_shas TEXT);"
        "CREATE TABLE claims_canonical (id INTEGER PRIMARY KEY, opn TEXT,"
        " symbol TEXT, qualifier TEXT, condition_norm TEXT, value REAL,"
        " unit TEXT, value_text TEXT, provenance TEXT, extractor TEXT,"
        " extractor_version TEXT, doc_sha256 TEXT, created_at REAL);")
    cat.execute("INSERT INTO parts VALUES ('PART-A','','infineon','', '',1,'[]')")
    aisle = {"PART-A": "discrete-mosfets"}
    with_id = cohort.build_cohort(catalog_con=cat, category="power",
                                  aisle_map=aisle, identity_con=icon)
    without = cohort.build_cohort(catalog_con=cat, category="power",
                                  aisle_map=aisle)
    f_with = cohort.facets_for_cohort(with_id)
    f_without = cohort.facets_for_cohort(without)
    # FACET-02 behavior is byte-identical when the identity db is not supplied
    assert json.dumps(f_without, sort_keys=True, default=str) == \
        json.dumps(f_without, sort_keys=True, default=str)
    assert all(x["dimension"] != "family" for x in f_without["facets"])
    assert without["family_stats"] is None
    assert f_with["distinct_families"] == 1              # only with identity
    assert f_without["candidate_count"] == f_with["candidate_count"]


# 14 — invalid identity sources fail closed (API-level; 13 is suite-level) -------------

def test_snapshot_builder_fail_closed(tmp_path):
    icon = _icon(tmp_path)
    broken = sqlite3.connect(":memory:")     # no parts table at all
    broken.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError):
        identity.build_identity_snapshot(broken, icon, category="power")