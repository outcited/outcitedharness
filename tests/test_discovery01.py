"""PRD-DISCOVERY-01 integration tests — journeys + R10 adversarial items.

Journey values are asserted FROM THE FROZEN M4 FIXTURE (regenerated from
the evidence bundle by scripts/discovery01_freeze_m4_journeys.py), never
hardcoded in production logic. Live M4 (:8793) is unavailable on this
machine — these are contract-level (frozen) integrations and are reported
as such.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.search import identity, m4_client, orchestrator
from harness.search.m4_client import M4ServiceError

FIXTURES = Path(__file__).parent / "fixtures/discovery01"
JOURNEYS = FIXTURES / "m4_frozen_journeys_v1.json"
CROSSWALK = FIXTURES / "m4_identity_crosswalk_v1.json"

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"

pytestmark = pytest.mark.skipif(
    not (Path(CATALOG).exists() and Path(SEARCH_DB).exists()
         and Path(JOURNEYS).exists()),
    reason="live M5 stores or frozen M4 fixtures unavailable")


@pytest.fixture(scope="module")
def deps():
    from harness.search import indexer
    catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    search.row_factory = sqlite3.Row
    icon = identity.connect(IDENTITY_DB) if Path(IDENTITY_DB).exists() \
        else None
    aisle = indexer.aisle_map_from_wave(WAVE)
    transport = m4_client.FrozenTransport(JOURNEYS)
    yield {"catalog_con": catalog, "search_con": search,
           "identity_con": icon, "aisle_map": aisle, "transport": transport,
           "crosswalk": orchestrator.load_crosswalk(CROSSWALK)}
    catalog.close()
    search.close()
    if icon:
        icon.close()


_DEP_KEYS = {"transport", "cache", "crosswalk", "catalog_con",
             "search_con", "identity_con", "aisle_map"}


def _session(deps, **req):
    base = {"category": "power", "subcategory": "buck",
            "discovery_grain": "opn",
            "constraints": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
            "comparison": {"metric": "efficiency",
                           "mode": "condition_matched"},
            "evidence_policy": "machine_verified_advisory"}
    overrides = {k: v for k, v in req.items() if k in _DEP_KEYS}
    base.update({k: v for k, v in req.items() if k not in _DEP_KEYS})
    return orchestrator.engineering_session(base, **{**deps, **overrides})


# --- Journey A: light-load buck selection ------------------------------------

def test_journey_a_values_come_from_frozen_m4(deps):
    r = _session(deps)
    assert r["partial_result"] is False
    assert r["schema"] == orchestrator.ORCHESTRATION_SCHEMA
    comp = r["comparisons"]["condition_matched_entries"]
    by_part = {e["part"]: e["value"] for e in comp}
    assert abs(by_part["SiC461"] - 93.94) < 0.01
    assert abs(by_part["SiC463"] - 92.96) < 0.01
    assert abs(by_part["SiC464"] - 92.95) < 0.01
    assert abs(by_part["LM5161"] - 77.95) < 0.01
    assert r["comparisons"]["comparison_level"]["achieved_level"] == \
        "cross_manufacturer"
    assert r["cohort_state"]["m4_eligible"] == 14
    assert r["releases"]["m4_transport"] == "frozen"
    assert r["releases"]["comparison_fingerprint"]


def test_journey_a_evidence_refs_and_review_state(deps):
    r = _session(deps)
    for entry in r["comparisons"]["condition_matched_entries"]:
        assert entry.get("evidence_id")
    assert r["evidence"]["resolver"] == "m5-search-index"
    assert any(s["kind"] == "verification_pending"
               for s in r["investigation_suggestions"])


# --- Journey B: load step to 3 A ----------------------------------------------

def test_journey_b_reevaluates_and_excludes_with_reasons(deps):
    r = _session(deps, constraints={"vin_v": 48, "vout_v": 5,
                                    "iout_a": 3.0})
    ineligible = [c for c in r["candidate_results"]
                  if c["engineering_eligibility"] == "ineligible"]
    parts = {c["part"]: c for c in ineligible}
    assert "LM5161" in parts                     # hard-ineligible at 3 A
    lm = next(x for x in parts["LM5161"]["explanation"]
              if x["rule"] == "iout_min")
    assert lm["rated"] == 1.0 and lm["required"] == 3.0
    assert "SiC464" in parts                     # 2 A device, excluded
    assert any(c["engineering_eligibility"] == "unknown"
               for c in r["candidate_results"])
    comp_parts = {e["part"] for e in
                  r["comparisons"]["condition_matched_entries"]}
    assert "LM5161" not in comp_parts            # fresh evaluation
    assert r["cohort_state"]["m4_eligible"] == 4


def test_journey_b_not_stale_vs_a(deps):
    a = _session(deps)
    b = _session(deps, constraints={"vin_v": 48, "vout_v": 5,
                                    "iout_a": 3.0})
    assert a["releases"]["comparison_fingerprint"] != \
        b["releases"]["comparison_fingerprint"]
    assert a["cohort_state"]["m4_eligible"] != \
        b["cohort_state"]["m4_eligible"]


# --- Journey C: insufficient comparative evidence ------------------------------

def test_journey_c_success_with_zero_comparable(deps):
    r = _session(deps, constraints={"vin_v": 24, "vout_v": 3.3,
                                    "iout_a": 1.0})
    assert r["partial_result"] is False          # success, not false error
    assert r["comparisons"]["condition_matched_entries"] == []
    assert r["cohort_state"]["m4_eligible"] == 11     # parametric retained
    kinds = {s["kind"] for s in r["investigation_suggestions"]}
    assert "missing_curve_evidence" in kinds
    total_suggested = sum(s["candidates"] for s in
                          r["investigation_suggestions"]
                          if s["kind"] in ("missing_curve_evidence",
                                           "noncomparable_evidence",
                                           "ratings_unknown"))
    assert total_suggested == 13                 # 11 + 1 + 1 from counts


# --- R10 adversarial items -------------------------------------------------------

def test_m4_failure_never_yields_false_empty_result(deps):
    class DeadTransport:
        def decide(self, question):
            raise M4ServiceError("connection_refused", "nothing listening")
    r = _session(deps, transport=DeadTransport())
    assert r["partial_result"] is True
    assert r["failures"][0]["reason"] == "connection_refused"
    assert r["cohort_state"]["m5_candidate_count"] > 0   # cohort preserved
    assert r["comparisons"] == {}


def test_release_mismatch_is_structured_not_silent(deps):
    r = _session(deps, expected_releases={
        "m4_evidence_release": "curve-evidence-bundle-v2-WRONG"})
    assert r["partial_result"] is True
    assert any(f["error"] == "release_mismatch" for f in r["failures"])


def test_crosswalk_no_fuzzy_joins_and_family_never_mapped(deps):
    xw = deps["crosswalk"]
    cohort_opns = {"LM5005", "TPS548C26", "NOSUCHPART"}
    mapping = orchestrator.crosswalk_map(xw, cohort_opns)
    for cid, row in mapping["unmapped"].items():
        assert row["class"] == "family_grain_node"
        assert row["grain"] == "family"
    assert all(row.get("opn", "").upper() in cohort_opns or
               row.get("device", "").upper() in cohort_opns
               for row in mapping["exact"].values())
    assert set(mapping) == {"exact", "ambiguous", "unmapped", "m4_only"}


def test_no_family_opn_double_counting(deps):
    r = _session(deps)
    cs = r["cohort_state"]
    assert cs["identity_unmapped_family_nodes"] >= 1
    assert cs["m5_candidate_count"] == r["session"]["state"]["cohort_count"]
    assert r["session"]["state"]["discovery_grain"] == "opn"


def test_cache_release_keyed_and_bounded(deps):
    cache = orchestrator.SessionCache(max_entries=2)
    r1 = _session(deps, cache=cache)
    assert r1["cache"]["hit"] is False
    r2 = _session(deps, cache=cache)
    assert r2["cache"]["hit"] is True
    assert r2["releases"] == r1["releases"]
    r3 = _session(deps, cache=cache,
                  constraints={"vin_v": 24, "vout_v": 3.3, "iout_a": 1.0})
    assert r3["cache"]["hit"] is False
    assert len(cache._data) <= 2

    class DeadTransport:
        def decide(self, question):
            raise M4ServiceError("timeout", "x")
    # fresh cache: the failure path must miss, and the failure itself must
    # never be stored (a second identical call misses again)
    err_cache = orchestrator.SessionCache(max_entries=8)
    r4 = _session(deps, cache=err_cache, transport=DeadTransport())
    assert r4["partial_result"] is True
    assert r4["cache"]["hit"] is False
    r5 = _session(deps, cache=err_cache, transport=DeadTransport())
    assert r5["cache"]["hit"] is False     # failure was never cached
    assert err_cache.hits == 0


def test_session_state_serializable_and_reproducible(deps):
    from harness.search import session as session_mod
    r = _session(deps)
    state = r["session"]["state"]
    restored = session_mod.SessionState.from_dict(json.loads(
        json.dumps(state)))
    assert restored.category == "power"
    assert restored.state_id() == \
        session_mod.SessionState.from_dict(state).state_id()


def test_deterministic_replay_frozen(deps):
    r1 = _session(deps)
    r2 = _session(deps)
    strip = lambda r: {k: v for k, v in r.items()  # noqa: E731
                       if k not in ("timings_ms", "total_ms", "cache")}
    assert json.dumps(strip(r1), sort_keys=True, default=str) == \
        json.dumps(strip(r2), sort_keys=True, default=str)


def test_question_contract_validated():
    with pytest.raises(M4ServiceError) as e:
        m4_client.validate_question({"requirements": {}})
    assert e.value.reason == "invalid_request"
    q = orchestrator.build_m4_question(
        {"vin_v": 48, "vout_v": 5, "iout_a": 0.5}, {"metric": "efficiency"})
    m4_client.validate_question(q)     # canonical shape passes
    assert q["requirements"]["vin_min"] == 48.0
    assert q["operating_point"] == {"x": 0.5, "unit": "A"}


def test_http_transport_structured_failures():
    t = m4_client.HttpTransport("http://127.0.0.1:1")   # nothing listens
    with pytest.raises(M4ServiceError) as e:
        t.decide({"requirements": {"vin_min": 1, "vout": 1, "iout_min": 1},
                  "conditions": {}, "operating_point": {"x": 1, "unit": "A"},
                  "phenomenon": "efficiency_vs_load"})
    assert e.value.reason in ("connection_refused", "connection_error",
                              "timeout")


def test_no_filesystem_paths_leak(deps):
    r = _session(deps)
    blob = json.dumps(r, default=str)
    assert "/Volumes/" not in blob
    assert "/Users/" not in blob


def test_api_feature_flag_off_preserves_existing_behavior(monkeypatch,
                                                           tmp_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from harness.search import api
    monkeypatch.delenv("SEARCH_ENABLE_ENGINEERING_SESSION", raising=False)
    monkeypatch.setattr(api, "SEARCH_DB", str(tmp_path / "absent.db"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(
            base + "/v1/discovery/engineering-session",
            data=json.dumps({"category": "power"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("expected 404 when flag off")
        except urllib.error.HTTPError as e:
            assert e.code == 404        # route does not exist when off
    finally:
        httpd.shutdown()
