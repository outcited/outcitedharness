"""B1 regression — verified identity authority, and where it lives.

The production-readiness assessment reported "626 memberships proposed, 0
promoted" because that is what this branch's documents say. The live
identity store says otherwise: every relationship is ``verified``, with
owner approval references recorded in ``meta``. The commits that executed
those admissions (6ba8c66e, b76f14be, 4e4398aa, 0c23a34d) are NOT in this
branch's ancestry, and this branch's identity API cannot even write a
verified relationship.

Contract pinned here:
  * the identity store is the serving authority for family/series — the
    catalog's ``parts.family`` column is never read into a candidate, and a
    bogus catalog value can never override a verified membership;
  * ``verified`` is reachable only through ``promote()`` with an explicit
    approval reference, and relationships cannot be written verified here;
  * the live store matches the recorded authority artifact (counts,
    statuses, approval refs, admission commits and their ancestry);
  * no document in this branch may still claim that nothing was promoted
    without carrying the correction that points at the authority record.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from harness.search import cohort as cohort_mod
from harness.search import identity as identity_mod

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "results/discovery01-remediation/b1_identity_authority.json"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"

FAMILY_APPROVAL = "FACET03-OWNER-APPROVAL-2026-10-09-961ef951"
SERIES_APPROVAL = "FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d"
EDGES_APPROVAL = "FACET03-SERIES-EDGES-OWNER-APPROVAL-2026-10-09-a56e177d"
ADMISSION_COMMITS = ["6ba8c66e", "b76f14be", "4e4398aa", "0c23a34d"]

STALE_PATTERNS = (
    r"[Nn]othing here is approved",
    r"no `promote\(\)` called",
    r"remain `proposed`",
    r"nothing promoted",
    r"verified coverage is 0",
    r"proposed family memberships",
)


def _catalog(tmp_path, family_value=""):
    path = tmp_path / "catalog.db"
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
    for opn in ("PART-A", "PART-B"):
        con.execute("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                    (opn, "", "infineon.com", family_value, "TO-247", 1.0,
                     "[]"))
    for opn, vds in (("PART-A", 650), ("PART-B", 100)):
        con.execute(
            "INSERT INTO claims_canonical (opn, symbol, qualifier,"
            " condition_norm, value, unit, value_text, doc_sha256,"
            " extractor_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (opn, "VDSS", None, "", vds, "V", str(vds), "c" * 64, "v1", 1.0))
    con.commit()
    return con


def _identity_store(tmp_path, *, verified=True, approval="APPROVAL-REF-1"):
    path = tmp_path / "identity.db"
    con = identity_mod.connect(str(path))
    fam = identity_mod.ensure_identity(
        con, "family", "CoolSiC", manufacturer_id="mfr:infineon",
        authority="datasheet_front_matter")
    if verified:
        identity_mod.promote(con, fam, approval=approval)
    identity_mod.add_relationship(
        con, fam, "opn:PART-A", "contains",
        authority="datasheet_front_matter", source_sha256="d" * 64,
        source_locator={"page_1based": 1}, norm_rule="front_matter_vocab_v1")
    return con, fam


# --- the identity store is the serving authority ----------------------------

def test_b1_verified_membership_wins_over_a_bogus_catalog_family(tmp_path):
    cat = _catalog(tmp_path, family_value="WRONG-CATALOG-FAMILY")
    icon, fam = _identity_store(tmp_path)
    try:
        out = cohort_mod.build_cohort(
            catalog_con=cat, search_con=None, category=None,
            subcategory=None, aisle_map={}, identity_con=icon)
    finally:
        cat.close()
        icon.close()
    cand = out["candidates"]["PART-A"]
    assert cand.family_label == "CoolSiC"
    assert cand.family_id == fam
    assert out["family_stats"]["with_family"] == 1
    assert "WRONG-CATALOG-FAMILY" not in json.dumps(
        {k: v for k, v in vars(cand).items() if not k.startswith("_")},
        default=str)


def test_b1_membership_status_travels_from_the_relationship_row(tmp_path):
    """The serving path reports what the store records and never upgrades
    it. Promoting the identity NODE does not silently verify its
    relationships (this branch has no packet-level promotion); when the
    relationship row itself is verified - as every row in the live store is -
    the served status is verified."""
    cat = _catalog(tmp_path)
    icon, fam = _identity_store(tmp_path)
    try:
        out = cohort_mod.build_cohort(
            catalog_con=cat, search_con=None, category=None,
            subcategory=None, aisle_map={}, identity_con=icon)
        assert out["candidates"]["PART-A"].family_status == "proposed"
        node = icon.execute(
            "SELECT status FROM identities WHERE identity_id=?",
            (fam,)).fetchone()[0]
        assert node == "verified", "the node promotion itself must hold"

        # the admitted state, as executed on the facet/03 lane: the
        # relationship rows carry verified
        icon.execute("UPDATE relationships SET status='verified'"
                     " WHERE parent_id=?", (fam,))
        icon.commit()
        out2 = cohort_mod.build_cohort(
            catalog_con=cat, search_con=None, category=None,
            subcategory=None, aisle_map={}, identity_con=icon)
    finally:
        cat.close()
        icon.close()
    assert out2["candidates"]["PART-A"].family_status == "verified"
    assert out2["candidates"]["PART-A"].families[0]["status"] == "verified"


def test_b1_catalog_family_column_is_never_read_into_a_candidate(tmp_path):
    cat = _catalog(tmp_path, family_value="WRONG-CATALOG-FAMILY")
    try:
        out = cohort_mod.build_cohort(
            catalog_con=cat, search_con=None, category=None,
            subcategory=None, aisle_map={}, identity_con=None)
    finally:
        cat.close()
    for cand in out["candidates"].values():
        assert getattr(cand, "family_label", None) in (None, "")
        assert getattr(cand, "family_id", None) in (None, "")
    blob = json.dumps(out["notes"])
    assert "0/1882" in blob or "catalog" in blob, \
        "the catalog family gap must stay disclosed"


def test_b1_unverified_membership_still_travels_with_its_status(tmp_path):
    """Statuses are never upgraded by the serving path."""
    cat = _catalog(tmp_path)
    icon, fam = _identity_store(tmp_path, verified=False)
    try:
        out = cohort_mod.build_cohort(
            catalog_con=cat, search_con=None, category=None,
            subcategory=None, aisle_map={}, identity_con=icon)
    finally:
        cat.close()
        icon.close()
    cand = out["candidates"]["PART-A"]
    assert cand.family_status == "proposed"
    assert cand.families[0]["status"] == "proposed"


# --- verified is only reachable through promote() with an approval ----------

def test_b1_identities_cannot_be_created_verified(tmp_path):
    con = identity_mod.connect(str(tmp_path / "i.db"))
    try:
        with pytest.raises(ValueError):
            identity_mod.ensure_identity(
                con, "family", "CoolMOS", manufacturer_id="mfr:infineon",
                authority="x", status="verified")
    finally:
        con.close()


def test_b1_promotion_requires_an_approval_reference(tmp_path):
    con = identity_mod.connect(str(tmp_path / "i.db"))
    try:
        iid = identity_mod.ensure_identity(
            con, "family", "OptiMOS", manufacturer_id="mfr:infineon",
            authority="x")
        with pytest.raises(ValueError):
            identity_mod.promote(con, iid, approval="   ")
        identity_mod.promote(con, iid, approval="APPROVAL-REF-2")
        row = con.execute("SELECT status FROM identities WHERE identity_id=?",
                          (iid,)).fetchone()
        assert row[0] == "verified"
        meta = con.execute("SELECT value FROM meta WHERE key=?",
                           (f"approval:{iid}",)).fetchone()
        assert meta is not None and meta[0] == "APPROVAL-REF-2"
    finally:
        con.close()


def test_b1_this_branch_cannot_write_a_verified_relationship(tmp_path):
    con = identity_mod.connect(str(tmp_path / "i.db"))
    try:
        iid = identity_mod.ensure_identity(
            con, "family", "StrongIRFET", manufacturer_id="mfr:infineon",
            authority="x")
        with pytest.raises(ValueError):
            identity_mod.add_relationship(
                con, iid, "opn:PART-Z", "contains", authority="x",
                source_sha256="e" * 64, source_locator={"page_1based": 1},
                norm_rule="front_matter_vocab_v1", status="verified")
    finally:
        con.close()


def test_b1_relationships_require_real_artifact_sha(tmp_path):
    con = identity_mod.connect(str(tmp_path / "i.db"))
    try:
        iid = identity_mod.ensure_identity(
            con, "family", "CoolSiC", manufacturer_id="mfr:infineon",
            authority="x")
        with pytest.raises(ValueError):
            identity_mod.add_relationship(
                con, iid, "opn:PART-Z", "contains", authority="x",
                source_sha256="unhashed:placeholder",
                source_locator={"page_1based": 1},
                norm_rule="front_matter_vocab_v1")
    finally:
        con.close()


# --- the recorded authority artifact matches the live store -----------------

@pytest.mark.skipif(not Path(IDENTITY_DB).exists(),
                    reason="live identity store unavailable")
def test_b1_authority_record_matches_the_live_store():
    assert AUTHORITY.exists(), "authority record was not generated"
    rec = json.loads(AUTHORITY.read_text())
    con = sqlite3.connect(f"file:{IDENTITY_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute("SELECT count(*) FROM relationships").fetchone()[0]
        unverified = con.execute(
            "SELECT count(*) FROM relationships WHERE status<>'verified'"
        ).fetchone()[0]
        by_parent = {r[0]: r[1] for r in con.execute(
            "SELECT i.kind || '|' || CASE WHEN r.child_id LIKE 'opn:%' THEN"
            " 'opn' ELSE 'series' END, count(*) FROM relationships r"
            " JOIN identities i ON i.identity_id=r.parent_id GROUP BY 1")}
        approvals = {r[0]: json.loads(r[1]).get("approval_ref") for r in
                     con.execute("SELECT key, value FROM meta WHERE key LIKE"
                                 " 'approval:%'")}
    finally:
        con.close()

    assert rec["live_state"]["relationships_total"] == total
    assert rec["live_state"]["relationships_not_verified"] == unverified
    assert unverified == 0
    assert rec["live_state"]["opn_to_family_verified"] == \
        by_parent["family|opn"] == 626
    assert rec["live_state"]["opn_to_series_verified"] == \
        by_parent["series|opn"] == 237
    assert rec["live_state"]["series_to_family_verified"] == \
        by_parent["family|series"] == 15
    ar = rec["approval_references"]
    assert ar["family"] == FAMILY_APPROVAL
    assert ar["series"] == SERIES_APPROVAL
    assert ar["series_edges"] == EDGES_APPROVAL
    for ref in (FAMILY_APPROVAL, SERIES_APPROVAL, EDGES_APPROVAL):
        assert ref in approvals.values(), f"missing approval ref {ref}"
    assert ar["approval_rows"] == len(approvals) == 23
    assert ar["node_approvals"] == 19 and ar["edge_approvals"] == 4
    assert rec["live_state"]["verified_family_nodes"] == 4
    assert rec["live_state"]["verified_series_nodes"] == 15


@pytest.mark.skipif(not Path(IDENTITY_DB).exists(),
                    reason="live identity store unavailable")
def test_b1_admission_commits_and_ancestry_are_recorded():
    rec = json.loads(AUTHORITY.read_text())
    recorded = {c["commit"][:8] for c in rec["admission_commits"]}
    assert set(ADMISSION_COMMITS) <= recorded
    for entry in rec["admission_commits"]:
        sha = entry["commit"]
        rc = subprocess.run(["git", "merge-base", "--is-ancestor", sha,
                             "HEAD"], cwd=ROOT).returncode
        assert entry["in_this_branch_ancestry"] is (rc == 0), \
            f"ancestry flag wrong for {sha}"
    assert rec["reproducibility"][
        "admission_code_present_in_this_branch"] is False
    assert not any(e["in_this_branch_ancestry"]
                   for e in rec["admission_commits"]), \
        "if the admission commits land in this branch, update the record"


# --- no document may still claim nothing was promoted -----------------------

def test_b1_no_stale_nothing_promoted_claim_without_a_correction():
    offenders = []
    for path in sorted(ROOT.glob("*.md")):
        text = path.read_text()
        hits = [p for p in STALE_PATTERNS if re.search(p, text)]
        if not hits:
            continue
        corrected = ("STATUS CORRECTION" in text
                     and "b1_identity_authority.json" in text)
        if not corrected:
            offenders.append(f"{path.name}: {hits}")
    assert offenders == [], (
        "documents still assert the pre-admission state without a "
        f"correction: {offenders}")


def test_b1_authority_document_answers_the_operator_questions():
    doc = ROOT / "DISCOVERY01_IDENTITY_AUTHORITY.md"
    assert doc.exists(), "operator-facing authority document is missing"
    text = doc.read_text()
    for needle in ("verified family", "verified series", "serving path",
                   "parts.family", "approval", "ancestry"):
        assert needle.lower() in text.lower(), f"missing: {needle}"
