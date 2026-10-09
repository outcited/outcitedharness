"""B3 regression — release pinning must survive session-cache reuse.

Discovered by the production-readiness assessment
(results/discovery01-production-readiness/pilot_records.json, scenario
P8c): an unpinned request populated the session cache, and a subsequent
request pinning a STALE release hit that entry, returning
``failures: []`` and ``partial_result: false`` — i.e. release validation
was bypassed by cache reuse, contradicting
``test_release_mismatch_is_structured_not_silent`` and the recorded action
"stale decision data never silently used".

Root cause: ``SessionCache.key`` did not include ``expected_releases``.
These tests pin the corrected behaviour: the pin is part of key identity,
canonicalisation is order-insensitive, unpinned and pinned requests never
collide, and a stale pin is reported structurally even on a warm cache.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from harness.search import identity, m4_client, orchestrator

FIXTURES = Path(__file__).parent / "fixtures/discovery01"
JOURNEYS = FIXTURES / "m4_frozen_journeys_v1.json"
CROSSWALK = FIXTURES / "m4_identity_crosswalk_v1.json"

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"

LIVE_RELEASE = "curve-evidence-bundle-v3-5875789dcf3f"
STALE_RELEASE = "curve-evidence-bundle-v2-3b5658b629cc"

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
    yield {"catalog_con": catalog, "search_con": search,
           "identity_con": icon, "aisle_map": aisle,
           "transport": m4_client.FrozenTransport(JOURNEYS),
           "crosswalk": orchestrator.load_crosswalk(CROSSWALK)}
    catalog.close()
    search.close()
    if icon:
        icon.close()


_DEP_KEYS = {"transport", "cache", "crosswalk", "catalog_con", "search_con",
             "identity_con", "aisle_map"}


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


# --- the assessment's P8c sequence, as a regression --------------------------

def test_b3_stale_pin_cannot_reuse_unpinned_cache_entry(deps):
    cache = orchestrator.SessionCache(max_entries=8)

    unpinned = _session(deps, cache=cache)
    assert unpinned["cache"]["hit"] is False
    assert unpinned["failures"] == []
    assert unpinned["partial_result"] is False

    pinned_stale = _session(
        deps, cache=cache,
        expected_releases={"m4_evidence_release": STALE_RELEASE})

    # the warm unpinned entry must NOT be reused to answer a stale pin
    assert pinned_stale["cache"]["hit"] is False, \
        "stale pin was answered from an unpinned cache entry (B3)"
    assert pinned_stale["partial_result"] is True
    assert any(f["error"] == "release_mismatch"
               for f in pinned_stale["failures"])
    mismatch = next(f for f in pinned_stale["failures"]
                    if f["error"] == "release_mismatch")
    assert mismatch["expected"] == STALE_RELEASE
    assert mismatch["actual"] == LIVE_RELEASE
    # cohort preserved, per the existing contract
    assert pinned_stale["cohort_state"]["m5_candidate_count"] == \
        unpinned["cohort_state"]["m5_candidate_count"] > 0


def test_b3_stale_pin_still_reported_when_cache_is_warm_from_valid_pin(deps):
    cache = orchestrator.SessionCache(max_entries=8)
    valid = _session(deps, cache=cache,
                     expected_releases={"m4_evidence_release": LIVE_RELEASE})
    assert valid["failures"] == []
    warm = _session(deps, cache=cache,
                    expected_releases={"m4_evidence_release": LIVE_RELEASE})
    assert warm["cache"]["hit"] is True
    assert warm["failures"] == []

    stale = _session(deps, cache=cache,
                     expected_releases={"m4_evidence_release":
                                        STALE_RELEASE})
    assert stale["cache"]["hit"] is False
    assert stale["partial_result"] is True
    assert any(f["error"] == "release_mismatch" for f in stale["failures"])


def test_b3_valid_pin_may_hit_and_is_separate_from_unpinned(deps):
    cache = orchestrator.SessionCache(max_entries=8)
    unpinned = _session(deps, cache=cache)
    pinned = _session(deps, cache=cache,
                      expected_releases={"m4_evidence_release":
                                         LIVE_RELEASE})
    assert pinned["cache"]["hit"] is False, \
        "unpinned and valid-pinned requests must not share a cache entry"
    assert pinned["failures"] == []
    assert pinned["partial_result"] is False
    assert pinned["releases"] == unpinned["releases"]

    again = _session(deps, cache=cache,
                     expected_releases={"m4_evidence_release":
                                        LIVE_RELEASE})
    assert again["cache"]["hit"] is True
    assert again["releases"] == pinned["releases"]
    assert again["failures"] == []


def test_b3_failed_pin_result_is_never_cached(deps):
    cache = orchestrator.SessionCache(max_entries=8)
    pin = {"m4_evidence_release": STALE_RELEASE}
    r1 = _session(deps, cache=cache, expected_releases=pin)
    r2 = _session(deps, cache=cache, expected_releases=pin)
    assert r1["cache"]["hit"] is False
    assert r2["cache"]["hit"] is False, "a partial/failed result was cached"
    assert cache.hits == 0
    assert r2["partial_result"] is True


# --- canonicalisation of the pin itself -------------------------------------

def test_b3_pin_canonicalisation_is_order_and_shape_insensitive():
    a = {"m4_evidence_release": LIVE_RELEASE,
         "m5_evidence_release": "search-release-v1-3f8b74ece642"}
    b = {"m5_evidence_release": "search-release-v1-3f8b74ece642",
         "m4_evidence_release": LIVE_RELEASE}
    assert orchestrator.canonical_release_pin(a) == \
        orchestrator.canonical_release_pin(b)


def test_b3_absent_empty_and_none_pins_canonicalise_identically():
    absent = orchestrator.canonical_release_pin(None)
    assert absent == orchestrator.canonical_release_pin({})
    assert absent == orchestrator.canonical_release_pin(
        {"m4_evidence_release": None})


def test_b3_different_pins_produce_different_canonical_values():
    assert orchestrator.canonical_release_pin(
        {"m4_evidence_release": LIVE_RELEASE}) != \
        orchestrator.canonical_release_pin(
            {"m4_evidence_release": STALE_RELEASE})
    assert orchestrator.canonical_release_pin(
        {"m4_evidence_release": LIVE_RELEASE}) != \
        orchestrator.canonical_release_pin(None)


def test_b3_cache_key_identity_includes_the_pin():
    common = dict(category="power", subcategory="buck", grain="opn",
                  constraints={"vin_v": 48}, comparison={"metric":
                                                         "efficiency"},
                  identity_snapshot="identity-snapshot-v1-x",
                  evidence_release="search-release-v1-y",
                  evidence_policy="machine_verified_advisory",
                  transport="frozen")
    unpinned = orchestrator.SessionCache.key(
        **common,
        release_pin=orchestrator.canonical_release_pin(None))
    pinned = orchestrator.SessionCache.key(
        **common,
        release_pin=orchestrator.canonical_release_pin(
            {"m4_evidence_release": LIVE_RELEASE}))
    stale = orchestrator.SessionCache.key(
        **common,
        release_pin=orchestrator.canonical_release_pin(
            {"m4_evidence_release": STALE_RELEASE}))
    assert len({unpinned, pinned, stale}) == 3


def test_b3_release_pin_semantics_unchanged_for_cold_requests(deps):
    """The pre-existing cold-path contract must not regress."""
    r = _session(deps, expected_releases={"m4_evidence_release":
                                          STALE_RELEASE})
    assert r["partial_result"] is True
    assert any(f["error"] == "release_mismatch" for f in r["failures"])
    assert r["comparisons"]["condition_matched_entries"], \
        "decision data is still returned alongside the structured failure"
