"""B6 regression — M5 candidate generation controls the M4 ranking population.

Owner-approved architecture: M5 owns candidate generation; M4 ranks ONLY the
candidate set explicitly supplied by M5. The production system must not
silently rank a separate M4-only universe while presenting the result as
though it came from the M5 discovery cohort, and a fixture crosswalk is not
an acceptable production identity layer.

Assessment evidence (identity_reconciliation.json): the live path loaded
tests/fixtures/discovery01/m4_identity_crosswalk_v1.json (21 rows), all 20
M4 bundle parts were absent from the 1,577-part M5 cohort
(identity_mapped_exact 0), and build_m4_question sent no candidate list, so
facet narrowing could not influence the ranking.

Contract pinned here (mode ``discovery_cohort``, the production default):
  * an explicit candidate payload is built after category selection, facet
    filtering, identity resolution and unknown retention, and every
    candidate carries canonical id, OPN, manufacturer, grain, discovery
    state, identity mapping and provenance counts;
  * the payload is release-bound and its hash is reproducible;
  * M4 receives exactly that list; the disclosed count equals the transport
    count; the fixture crosswalk is not used or read;
  * a ranked entry for a candidate M5 never sent is a structured violation,
    never silently presented;
  * sent == ranked + excluded + unknown + unresolved (no candidate may
    disappear without an explicit reason);
  * facet changes change the payload deterministically; ordering is stable;
  * an over-large population fails closed instead of truncating silently;
  * the legacy M4-universe path stays available but is labeled
    ``out_of_cohort`` and reports that promotion is blocked while it is the
    normal path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.search import cohort as cohort_mod
from harness.search import identity as identity_mod
from harness.search import candidate_transport, m4_client, orchestrator
from harness.search.m4_client import M4ServiceError

LIVE_RELEASE = "curve-evidence-bundle-v3-5875789dcf3f"


# --- hermetic stores --------------------------------------------------------

def _catalog(tmp_path, opns, families=None):
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
    for opn in opns:
        con.execute("INSERT INTO parts VALUES (?,?,?,?,?,?,?)",
                    (opn, "", "infineon.com", "", "TO-247", 1.0, "[]"))
        con.execute(
            "INSERT INTO claims_canonical (opn, symbol, qualifier,"
            " condition_norm, value, unit, value_text, doc_sha256,"
            " extractor_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (opn, "VDSS", None, "", 650, "V", "650", "c" * 64, "v1", 1.0))
    con.commit()
    return con


def _identity(tmp_path, memberships):
    con = identity_mod.connect(str(tmp_path / "identity.db"))
    nodes = {}
    for label, kind in {("CoolSiC", "family"), ("OptiMOS", "family"),
                        ("CoolSiC-G2", "series")}:
        nodes[label] = identity_mod.ensure_identity(
            con, kind, label, manufacturer_id="mfr:infineon",
            authority="datasheet_front_matter")
    for label in nodes:
        identity_mod.promote(con, nodes[label], approval="APPROVAL-REF-B6")
    for opn, label in memberships.items():
        identity_mod.add_relationship(
            con, nodes[label], f"opn:{opn}", "contains",
            authority="datasheet_front_matter", source_sha256="d" * 64,
            source_locator={"page_1based": 1},
            norm_rule="front_matter_vocab_v1")
    # Mirror the admitted live state: in facet_identity.db every
    # relationship row is verified (executed on the facet/03 lane by
    # promote_packet; this branch's API can only write proposed - see
    # tests/test_discovery01_b1_identity_authority.py).
    con.execute("UPDATE relationships SET status='verified'")
    con.commit()
    return con


class ScopedFakeM4:
    """Answers the way a scope-respecting M4 must: it ranks only what it was
    given, and reports per-candidate accounting."""

    def __init__(self, evidence_opns=(), extra_unsolicited=None,
                 ineligible=(), unknown=()):
        self.evidence_opns = set(evidence_opns)
        self.extra_unsolicited = extra_unsolicited or []
        self.ineligible = set(ineligible)
        self.unknown = set(unknown)
        self.calls = []

    def decide(self, question, candidate_scope=None):
        self.calls.append({"question": question, "scope": candidate_scope})
        sent = (candidate_scope or {}).get("candidates") or []
        matched = [c for c in sent if c["opn"] in self.evidence_opns]
        unresolved = [c for c in sent if c["opn"] not in self.evidence_opns]
        ranked, inelig, unk = [], [], []
        for c in matched:
            if c["opn"] in self.ineligible:
                inelig.append({"candidate": c["canonical_id"],
                               "part": c["opn"], "grain": "opn",
                               "rules": [{"rule": "vin_min",
                                          "verdict": "ineligible",
                                          "reason": "VIN rating too low",
                                          "required": 48.0, "rated": 36.0}]})
            elif c["opn"] in self.unknown:
                unk.append({"candidate": c["canonical_id"], "part": c["opn"],
                            "missing_ratings": ["vin_min"],
                            "note": "electrically unconfirmed; retained, "
                                    "never eliminated"})
            else:
                ranked.append({"candidate": c["canonical_id"],
                               "part": c["opn"], "value": 90.0, "unit": "%",
                               "evidence_id": f"curve-{c['opn']}",
                               "evidence_class": "measured",
                               "guarantee": False,
                               "adjudication_state": "machine_verified",
                               "source": {"document_sha256": "e" * 64,
                                          "page_1based": 3},
                               "interpolation": {"supporting_points": [
                                   {"x": 1}, {"x": 2}]},
                               "eligibility_verdict": "eligible"})
        for extra in self.extra_unsolicited:
            ranked.append(extra)
        return m4_client.DecisionResult(
            answer={
                "schema": m4_client.DECISION_SCHEMA_EXPECTED,
                "question": dict(question),
                "evidence_release": LIVE_RELEASE,
                "bundle_sha256": "f" * 64,
                "counts": {
                    "total_candidates": len(matched),
                    "hard_eligible_candidates": len(matched) - len(inelig)
                    - len(unk),
                    "parametrically_compatible_candidates": len(matched),
                    "candidates_with_comparable_curve_evidence": len(ranked),
                    "candidates_with_noncomparable_evidence": 0,
                    "candidates_missing_curve_evidence": len(unresolved),
                    "candidates_requiring_verification": 0,
                    "evidence_rows_attached": len(ranked)},
                "hard_eligibility": {
                    "eligible": [c["canonical_id"] for c in matched
                                 if c["opn"] not in self.ineligible
                                 and c["opn"] not in self.unknown],
                    "ineligible": inelig, "unknown": unk},
                "evidence_availability": {},
                "comparison": {
                    "condition_matched_entries": ranked,
                    "approximate_scenario_entries": [],
                    "comparison_level": {"achieved_level": "within_family"}},
                "candidate_scope": {
                    "schema": "harness.electronics-candidate-scope.v1",
                    "population": (candidate_scope or {}).get("population"),
                    "received": len(sent),
                    "matched": len(matched),
                    "unresolved": len(unresolved),
                    "unresolved_sample": [
                        {"canonical_id": c["canonical_id"], "opn": c["opn"],
                         "reason": "no_evidence_in_bundle"}
                        for c in unresolved[:50]],
                    "ranking_strictly_in_cohort": True,
                    "m4_universe_substituted": False,
                    "match_rule": "exact candidate id, else exact OPN/device,"
                                  " else case-insensitive exact; never fuzzy",
                },
                "laws": []},
            transport="fake-scoped",
            contract={"decision_schema": m4_client.DECISION_SCHEMA_EXPECTED,
                      "m4_evidence_release": LIVE_RELEASE,
                      "m4_bundle_sha256": "f" * 64},
            latency_ms=1.0)


@pytest.fixture
def env(tmp_path):
    opns = [f"IPB{i:03d}" for i in range(12)]
    cat = _catalog(tmp_path, opns)
    icon = _identity(tmp_path, {"IPB001": "CoolSiC", "IPB002": "CoolSiC",
                                "IPB003": "OptiMOS"})
    search = sqlite3.connect(":memory:")
    search.row_factory = sqlite3.Row
    search.executescript("""
        CREATE TABLE units (unit_id TEXT PRIMARY KEY, doc_sha256 TEXT,
            grain TEXT, page INTEGER, locator TEXT, text_repr TEXT,
            structured TEXT, ident TEXT, vendor TEXT, doc_class TEXT,
            category TEXT, family TEXT, applicability TEXT DEFAULT '[]',
            rev_code TEXT, rev_date TEXT, extraction_version TEXT,
            verification_state TEXT DEFAULT 'unverified',
            verification_source TEXT, retired INTEGER DEFAULT 0,
            retired_reason TEXT, created_at REAL);
        CREATE TABLE vectors (unit_id TEXT PRIMARY KEY, model TEXT,
            dim INTEGER, vec BLOB);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE releases (id INTEGER PRIMARY KEY AUTOINCREMENT,
            release TEXT, built_at REAL, unit_count INTEGER,
            fingerprint TEXT);
        CREATE TABLE release_cache (policy_identity TEXT PRIMARY KEY,
            release TEXT, fingerprint TEXT, unit_count INTEGER,
            active_count INTEGER, vector_identity TEXT DEFAULT '',
            dirty INTEGER DEFAULT 0, computed_at REAL);
    """)
    # the orchestrator defaults category to "power", so the temp cohort needs
    # aisle placements to be selected at all
    aisle = {opn: "discrete-mosfets" for opn in opns}
    yield {"catalog_con": cat, "search_con": search, "identity_con": icon,
           "aisle_map": aisle, "opns": opns}
    cat.close()
    search.close()
    icon.close()


def _session(env, transport, **req):
    base = {"category": "power", "constraints": {"vin_v": 48, "vout_v": 5,
                                                 "iout_a": 0.5},
            "comparison": {"metric": "efficiency"}}
    base.update(req)
    return orchestrator.engineering_session(
        base, catalog_con=env["catalog_con"], search_con=env["search_con"],
        identity_con=env["identity_con"], aisle_map=env["aisle_map"],
        transport=transport)


# --- the payload is explicit, complete and identity-resolved ----------------

def test_b6_candidate_payload_is_sent_with_required_identity_fields(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:3])
    r = _session(env, t)
    scope = t.calls[0]["scope"]
    assert scope is not None, "no candidate payload was sent to M4"
    assert scope["schema"] == "harness.discovery01-candidate-scope.v1"
    assert scope["population"] == "discovery_cohort"
    assert len(scope["candidates"]) == len(env["opns"])
    for c in scope["candidates"]:
        assert c["canonical_id"] == f"opn:{c['opn']}"
        assert c["manufacturer"]
        assert c["grain"] == "opn"
        assert c["eligibility_state"] in ("sent_for_evaluation",
                                          "unknown_attributes_retained")
        assert "identity" in c and "provenance" in c
        assert set(c["identity"]) >= {"family_id", "family_label",
                                      "family_status", "mapped",
                                      "identity_source"}
    # release-bound
    assert scope["releases"]["m5_evidence_release"]
    assert scope["releases"]["m5_identity_snapshot"] is not None
    assert scope["crosswalk"]["version"]
    assert scope["crosswalk"]["sha256"]


def test_b6_identity_mapping_is_generated_not_fixtured(env, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("the fixture crosswalk was read on the "
                             "production path")
    monkeypatch.setattr(orchestrator, "load_crosswalk", _boom)
    t = ScopedFakeM4(evidence_opns=env["opns"][:2])
    r = _session(env, t)
    rel = r["releases"]
    assert "crosswalk_fixture_sha256" not in rel
    assert rel["identity_crosswalk_sha256"]
    assert rel["identity_crosswalk_source"] == "generated:identity-store"
    mapped = [c for c in t.calls[0]["scope"]["candidates"]
              if c["identity"]["mapped"]]
    assert {c["opn"] for c in mapped} == {"IPB001", "IPB002", "IPB003"}
    assert all(c["identity"]["family_status"] == "verified" for c in mapped)


def test_b6_unmapped_candidates_remain_explicit(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:1])
    r = _session(env, t)
    cov = r["ranking_population"]["identity_mapping"]["coverage"]
    assert cov["total"] == 12
    assert cov["mapped"] == 3
    assert cov["unmapped"] == 9
    unmapped = [c for c in t.calls[0]["scope"]["candidates"]
                if not c["identity"]["mapped"]]
    assert len(unmapped) == 9
    assert all(c["identity"]["family_id"] is None for c in unmapped)


def test_b6_disclosed_count_equals_transport_count(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:4])
    r = _session(env, t)
    rp = r["ranking_population"]
    assert rp["candidates_sent"] == len(t.calls[0]["scope"]["candidates"])
    assert rp["m4_received"] == rp["candidates_sent"]
    assert rp["discovery_candidate_count"] == rp["candidates_sent"]


def test_b6_accounting_adds_up_no_candidate_disappears(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:6],
                     ineligible={env["opns"][4]},
                     unknown={env["opns"][5]})
    r = _session(env, t)
    rp = r["ranking_population"]
    assert rp["candidates_sent"] == rp["ranked"] + rp["excluded"] + \
        rp["unknown_retained"] + rp["unresolved_no_evidence"]
    assert rp["ranked"] == 4 and rp["excluded"] == 1
    assert rp["unknown_retained"] == 1 and rp["unresolved_no_evidence"] == 6
    assert rp["assertion_holds"] is True
    assert rp["ranking_strictly_in_cohort"] is True
    assert r["partial_result"] is False


def test_b6_hard_exclusions_and_reasons_are_recorded(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:2],
                     ineligible={env["opns"][1]})
    r = _session(env, t)
    inel = [c for c in r["candidate_results"]
            if c["engineering_eligibility"] == "ineligible"]
    assert len(inel) == 1
    assert inel[0]["part"] == env["opns"][1]
    assert inel[0]["explanation"][0]["rule"] == "vin_min"
    assert inel[0]["explanation"][0]["rated"] == 36.0


def test_b6_unknown_candidates_are_retained_never_eliminated(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:2],
                     unknown={env["opns"][1]})
    r = _session(env, t)
    unk = [c for c in r["candidate_results"]
           if c["engineering_eligibility"] == "unknown"]
    assert len(unk) == 1 and unk[0]["part"] == env["opns"][1]
    assert unk[0]["missing_ratings"] == ["vin_min"]
    assert r["ranking_population"]["unknown_retained"] == 1


def test_b6_unsolicited_m4_candidate_is_a_structured_violation(env):
    smuggled = {"candidate": "cand-not-sent", "part": "TPS548C26",
                "value": 99.0, "unit": "%", "evidence_id": "curve-x",
                "source": {"document_sha256": "e" * 64, "page_1based": 1},
                "interpolation": {"supporting_points": [{"x": 1}]}}
    t = ScopedFakeM4(evidence_opns=env["opns"][:2],
                     extra_unsolicited=[smuggled])
    r = _session(env, t)
    assert r["partial_result"] is True
    assert any(f["error"] == "ranking_population_violation"
               for f in r["failures"])
    parts = {e["part"] for e in
             r["comparisons"]["condition_matched_entries"]}
    assert "TPS548C26" not in parts, "an out-of-population candidate was " \
        "presented as an in-cohort ranking"
    assert r["ranking_population"]["out_of_population_entries"] == [
        "TPS548C26"]


def test_b6_empty_candidate_set_is_honest_not_an_error(env):
    t = ScopedFakeM4()
    r = _session(env, t, constraints={"vin_v": 48, "vout_v": 5,
                                      "iout_a": 0.5, "vendor": "nobody"})
    rp = r["ranking_population"]
    assert rp["candidates_sent"] == 0
    assert rp["ranked"] == 0
    assert r["partial_result"] is False
    assert r["comparisons"]["condition_matched_entries"] == []
    assert t.calls[0]["scope"]["candidates"] == []


# --- determinism ------------------------------------------------------------

def test_b6_payload_is_deterministic_and_ordered(env):
    t1, t2 = ScopedFakeM4(evidence_opns=env["opns"][:2]), ScopedFakeM4(
        evidence_opns=env["opns"][:2])
    r1 = _session(env, t1)
    r2 = _session(env, t2)
    ids1 = [c["canonical_id"] for c in t1.calls[0]["scope"]["candidates"]]
    ids2 = [c["canonical_id"] for c in t2.calls[0]["scope"]["candidates"]]
    assert ids1 == ids2 == sorted(ids1)
    assert t1.calls[0]["scope"]["sha256"] == t2.calls[0]["scope"]["sha256"]
    assert r1["ranking_population"]["candidates_sha256"] == \
        r2["ranking_population"]["candidates_sha256"]
    strip = lambda r: json.dumps(  # noqa: E731
        {k: v for k, v in r.items()
         if k not in ("timings_ms", "total_ms", "cache")},
        sort_keys=True, default=str)
    assert strip(r1) == strip(r2)


def test_b6_facet_change_changes_the_payload_deterministically(env):
    all_t = ScopedFakeM4(evidence_opns=env["opns"])
    full = _session(env, all_t)
    fam_t = ScopedFakeM4(evidence_opns=env["opns"])
    narrowed = _session(env, fam_t, constraints={"vin_v": 48, "vout_v": 5,
                                                 "iout_a": 0.5,
                                                 "family": "CoolSiC"})
    sent_full = {c["opn"] for c in all_t.calls[0]["scope"]["candidates"]}
    sent_narrow = {c["opn"] for c in fam_t.calls[0]["scope"]["candidates"]}
    assert sent_narrow == {"IPB001", "IPB002"} < sent_full
    assert narrowed["ranking_population"]["candidates_sent"] == 2
    assert full["ranking_population"]["candidates_sent"] == 12
    # repeat the narrowed request: identical payload
    again = ScopedFakeM4(evidence_opns=env["opns"])
    _session(env, again, constraints={"vin_v": 48, "vout_v": 5,
                                      "iout_a": 0.5, "family": "CoolSiC"})
    assert [c["canonical_id"] for c in
            again.calls[0]["scope"]["candidates"]] == \
        [c["canonical_id"] for c in fam_t.calls[0]["scope"]["candidates"]]


def test_b6_facet_unplaced_unknowns_are_transported_not_dropped(env):
    """Narrowing on an axis nobody has data for must not silently delete the
    cohort: unplaced candidates travel flagged as retained-unknown so M4 can
    apply its own 'missing ratings are unknown, never failure' law."""
    t = ScopedFakeM4()
    r = _session(env, t, constraints={"vin_v": 48, "vout_v": 5,
                                      "iout_a": 0.5,
                                      "rds_on_ohm": "0.005"})
    rp = r["ranking_population"]
    sent = t.calls[0]["scope"]["candidates"]
    assert rp["discovery_candidate_count"] == 0, "narrowed cohort is empty"
    assert rp["candidates_sent"] == len(env["opns"])
    assert rp["unknown_unplaced_included"] == len(env["opns"])
    assert all(c["eligibility_state"] == "unknown_attributes_retained"
               for c in sent)
    assert all("rds_on_ohm" in c["unknown_axes"] for c in sent)
    assert rp["population_accounting"] == {
        "discovery_candidate_count": 0,
        "unknown_unplaced_included": len(env["opns"]),
        "candidates_sent": len(env["opns"]),
        "identity": "candidates_sent == discovery_candidate_count + "
                    "unknown_unplaced_included"}
    assert rp["assertion_holds"] is True
    assert r["partial_result"] is False


def test_b6_disclosure_carries_a_candidate_sample_and_state_counts(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:2])
    r = _session(env, t)
    rp = r["ranking_population"]
    assert rp["candidates_sample"] == sorted(
        f"opn:{o}" for o in env["opns"])[:25]
    assert sum(rp["eligibility_state_counts"].values()) == \
        rp["candidates_sent"]
    assert rp["candidates_sha256"]


def test_b6_question_contract_is_unchanged_by_scoping(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:1])
    _session(env, t)
    q = t.calls[0]["question"]
    assert set(q) == {"requirements", "conditions", "operating_point",
                      "phenomenon"}
    m4_client.validate_question(q)


# --- fail closed ------------------------------------------------------------

def test_b6_oversized_population_fails_closed_without_truncation(env,
                                                                monkeypatch):
    monkeypatch.setattr(candidate_transport, "MAX_CANDIDATE_SCOPE", 5)
    t = ScopedFakeM4(evidence_opns=env["opns"])
    r = _session(env, t)
    assert r["partial_result"] is True
    assert any(f["error"] == "candidate_population_too_large"
               for f in r["failures"])
    assert t.calls == [], "an oversized population must not be sent partly"
    assert r["cohort_state"]["m5_candidate_count"] == 12


def test_b6_frozen_transport_refuses_to_improvise_a_scoped_answer():
    """Pinned journeys were generated unscoped, so the frozen transport must
    refuse a scope rather than answer as if it honoured it."""
    fixture = Path(__file__).parent / "fixtures/discovery01/"         "m4_frozen_journeys_v1.json"
    if not fixture.exists():
        pytest.skip("frozen journey fixture unavailable")
    t = m4_client.FrozenTransport(fixture)
    scope = {"schema": "harness.discovery01-candidate-scope.v1",
             "population": "discovery_cohort", "candidates": []}
    with pytest.raises(M4ServiceError) as e:
        t.decide({"requirements": {"vin_min": 1}, "conditions": {},
                  "operating_point": {"x": 1, "unit": "A"},
                  "phenomenon": "efficiency_vs_load"}, candidate_scope=scope)
    assert e.value.reason == "frozen_transport_no_scope"


def test_b6_base_transport_interface_accepts_a_scope():
    """Both transports share one signature so the orchestrator never has to
    know which is live."""
    import inspect
    for cls in (m4_client.FrozenTransport, m4_client.HttpTransport):
        params = inspect.signature(cls.decide).parameters
        assert "candidate_scope" in params, cls.__name__


# --- legacy path stays available but labeled --------------------------------

def test_b6_legacy_m4_universe_path_is_labeled_out_of_cohort(env):
    class Legacy:
        def __init__(self):
            self.scope = "unset"

        def decide(self, question, candidate_scope=None):
            self.scope = candidate_scope
            return m4_client.DecisionResult(
                answer={
                    "schema": m4_client.DECISION_SCHEMA_EXPECTED,
                    "question": dict(question),
                    "evidence_release": LIVE_RELEASE,
                    "bundle_sha256": "f" * 64,
                    "counts": {"total_candidates": 1,
                               "hard_eligible_candidates": 1,
                               "candidates_with_comparable_curve_evidence": 1,
                               "candidates_with_noncomparable_evidence": 0,
                               "candidates_missing_curve_evidence": 0,
                               "candidates_requiring_verification": 0,
                               "evidence_rows_attached": 1},
                    "hard_eligibility": {"eligible": ["cand-1"],
                                         "ineligible": [], "unknown": []},
                    "evidence_availability": {},
                    "comparison": {"condition_matched_entries": [
                        {"candidate": "cand-1", "part": "TPS548C26",
                         "value": 88.0, "unit": "%",
                         "evidence_id": "curve-1",
                         "source": {"document_sha256": "e" * 64,
                                    "page_1based": 2},
                         "interpolation": {"supporting_points": [{"x": 1}]}}],
                        "approximate_scenario_entries": [],
                        "comparison_level": {"achieved_level":
                                             "cross_manufacturer"}},
                    "laws": []},
                transport="fake-legacy",
                contract={"decision_schema":
                          m4_client.DECISION_SCHEMA_EXPECTED,
                          "m4_evidence_release": LIVE_RELEASE},
                latency_ms=1.0)

    t = Legacy()
    r = _session(env, t, ranking_population="m4_bundle")
    assert t.scope is None, "the legacy path must not send a scope"
    rp = r["ranking_population"]
    assert rp["mode"] == "m4_bundle"
    assert rp["in_cohort"] is False
    assert rp["out_of_cohort"] is True
    assert rp["promotion_blocked_while_normal_path"] is True
    assert "not facet-constrained" in rp["disclosure"]
    assert r["releases"]["crosswalk_fixture_sha256"]


def test_b6_default_mode_is_the_in_cohort_production_path(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:1])
    r = _session(env, t)
    assert r["ranking_population"]["mode"] == "discovery_cohort"
    assert r["ranking_population"]["in_cohort"] is True
    assert t.calls[0]["scope"] is not None


def test_b6_both_release_ids_are_disclosed(env):
    t = ScopedFakeM4(evidence_opns=env["opns"][:1])
    r = _session(env, t)
    rp = r["ranking_population"]
    assert rp["releases"]["m5_evidence_release"] == \
        r["releases"]["m5_evidence_release"]
    assert rp["releases"]["m4_evidence_release"] == LIVE_RELEASE
    assert rp["releases"]["m5_identity_snapshot"] is not None
