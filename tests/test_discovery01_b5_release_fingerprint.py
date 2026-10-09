"""B5 regression — release identity must be O(1) in steady state.

Assessment evidence (release_identity.json / performance_observations.json):
every session request spent ~2.9 s inside ``units.release_view``
re-fingerprinting all 443,604 unit rows, because the single-row ``meta``
release cache holds ONE policy identity (``hybrid-v1.1(...)``) while
DISCOVERY-01 serves ``discovery01-v1(...)``. The cost was paid even on
session-cache hits and was absent from ``timings_ms``.

Contract pinned here:
  * a policy-keyed release cache makes steady-state lookup O(1) — the
    fingerprint is not recomputed when a valid row exists;
  * different policies never share a fingerprint;
  * index mutation (units replaced, retired, or vectors attached)
    invalidates the cached row, so the id never lies;
  * a read-only serving connection consults the row but never writes;
  * the release id stays deterministic and restart-safe;
  * the legacy meta path is unchanged when no policy identity is given;
  * the served view keeps its shape, and the session reports the lookup
    cost and honest cache-hit timings.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.search import units as units_store

POLICY_A = "discovery01-v1(test-policy-a)"
POLICY_B = "hybrid-v1.1(test-policy-b)"

_NEEDED = ("unit_id", "doc_sha256", "grain", "locator", "text_repr",
           "extraction_version", "created_at")


def _insert_unit(con, unit_id, doc="a" * 64, retired=0):
    """One valid unit row; the locator varies so the (doc, grain, locator,
    extraction_version) uniqueness law holds."""
    con.execute(
        "INSERT INTO units (unit_id, doc_sha256, grain, page, locator, "
        "text_repr, extraction_version, verification_state, retired, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (unit_id, doc, "claim", 1, json.dumps({"row_header": unit_id}),
         f"rds on 1 milliohm ({unit_id})", "v1", "unverified", retired, 1.0))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "idx.db"
    con = units_store.connect(str(path))
    for i in range(5):
        _insert_unit(con, f"u{i}")
    con.commit()
    yield path
    con.close()


def _ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _counting_fingerprint(monkeypatch):
    """Wrap units._fingerprint so recomputation is observable."""
    calls = {"n": 0}
    real = units_store._fingerprint

    def wrapper(con, policy_identity=""):
        calls["n"] += 1
        return real(con, policy_identity)

    monkeypatch.setattr(units_store, "_fingerprint", wrapper)
    return calls


def _prime(path, policy):
    con = units_store.connect(str(path))
    try:
        return units_store.policy_release(con, policy, persist=True)
    finally:
        con.close()


# --- steady state is O(1) ---------------------------------------------------

def test_b5_primed_lookup_does_not_recompute_the_fingerprint(db, monkeypatch):
    primed = _prime(db, POLICY_A)
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        for _ in range(5):
            view = units_store.release_view(con, policy_identity=POLICY_A)
            assert view["release"] == primed["release"]
    finally:
        con.close()
    assert calls["n"] == 0, "steady-state lookup re-fingerprinted the index"


def test_b5_priming_persists_a_policy_keyed_row(db):
    _prime(db, POLICY_A)
    con = _ro(db)
    try:
        rows = con.execute(
            "SELECT policy_identity, release, dirty FROM release_cache"
        ).fetchall()
    finally:
        con.close()
    assert [(r["policy_identity"], r["dirty"]) for r in rows] == \
        [(POLICY_A, 0)]


def test_b5_priming_records_the_release_in_the_ledger(db):
    primed = _prime(db, POLICY_A)
    con = _ro(db)
    try:
        rows = con.execute("SELECT release, fingerprint FROM releases"
                           ).fetchall()
    finally:
        con.close()
    assert primed["release"] in [r["release"] for r in rows]


# --- policies never share a fingerprint -------------------------------------

def test_b5_different_policies_get_different_release_ids(db):
    a = _prime(db, POLICY_A)
    b = _prime(db, POLICY_B)
    assert a["release"] != b["release"]
    assert a["policy_identity"] == POLICY_A
    assert b["policy_identity"] == POLICY_B


def test_b5_priming_one_policy_does_not_disturb_another(db, monkeypatch):
    a = _prime(db, POLICY_A)
    _prime(db, POLICY_B)
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        again = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert again["release"] == a["release"]
    assert calls["n"] == 0


def test_b5_legacy_meta_release_is_not_overwritten_by_policy_priming(db):
    con = units_store.connect(str(db))
    try:
        legacy = units_store.index_release(con)
        meta_before = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta")}
    finally:
        con.close()
    _prime(db, POLICY_A)
    con = _ro(db)
    try:
        meta_after = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta")}
    finally:
        con.close()
    assert meta_after.get("release") == meta_before.get("release") == \
        legacy["release"]
    assert meta_after.get("policy_identity") == \
        meta_before.get("policy_identity")


# --- invalidation is correct ------------------------------------------------

def test_b5_replacing_a_document_invalidates_the_cached_release(db,
                                                               monkeypatch):
    before = _prime(db, POLICY_A)
    con = units_store.connect(str(db))
    try:
        unit = units_store.make_unit(
            doc_sha256="b" * 64, grain="claim",
            locator={"row_header": "new"}, text_repr="new claim text",
            extraction_version="v1")
        units_store.replace_document(con, [unit], doc_sha256="b" * 64)
    finally:
        con.close()
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        after = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert calls["n"] == 1, "a dirty cache row was served as valid"
    assert after["release"] != before["release"]


def test_b5_retiring_a_document_invalidates_the_cached_release(db,
                                                              monkeypatch):
    before = _prime(db, POLICY_A)
    con = units_store.connect(str(db))
    try:
        units_store.retire_document(con, "a" * 64, "superseded")
    finally:
        con.close()
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        after = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert calls["n"] == 1
    assert after["release"] != before["release"]


def test_b5_attaching_a_vector_invalidates_the_cached_release(db,
                                                             monkeypatch):
    """vector_identity is part of the fingerprint, so vector writes must
    invalidate too (the legacy meta path never did)."""
    before = _prime(db, POLICY_A)
    con = units_store.connect(str(db))
    try:
        units_store.attach_vector(con, "u0", "bge-test-v1", [0.1, 0.2, 0.3])
    finally:
        con.close()
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        after = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert calls["n"] == 1
    assert after["release"] != before["release"]


def test_b5_repriming_after_mutation_is_stable_again(db, monkeypatch):
    _prime(db, POLICY_A)
    con = units_store.connect(str(db))
    try:
        units_store.retire_document(con, "a" * 64, "superseded")
    finally:
        con.close()
    reprime = _prime(db, POLICY_A)
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        view = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert view["release"] == reprime["release"]
    assert calls["n"] == 0


# --- determinism, restart safety, read-only safety --------------------------

def test_b5_release_id_is_deterministic_without_priming(db, monkeypatch):
    calls = _counting_fingerprint(monkeypatch)
    con = _ro(db)
    try:
        unprimed = units_store.release_view(con, policy_identity=POLICY_A)
    finally:
        con.close()
    assert calls["n"] == 1
    assert unprimed["release"] == _prime(db, POLICY_A)["release"]


def test_b5_release_survives_reopen_restart_safe(db, monkeypatch):
    primed = _prime(db, POLICY_A)
    calls = _counting_fingerprint(monkeypatch)
    for _ in range(2):                      # simulates process restarts
        con = _ro(db)
        try:
            view = units_store.release_view(con, policy_identity=POLICY_A)
        finally:
            con.close()
        assert view["release"] == primed["release"]
    assert calls["n"] == 0


def test_b5_read_only_connection_never_writes(db):
    _prime(db, POLICY_A)
    before = Path(db).stat().st_size
    con = _ro(db)
    try:
        units_store.release_view(con, policy_identity=POLICY_A)
        # an unprimed policy must also be served without writing
        units_store.release_view(con, policy_identity="unprimed-policy")
    finally:
        con.close()
    rows = _ro(db).execute("SELECT count(*) FROM release_cache").fetchone()[0]
    assert rows == 1, "a read-only connection persisted a cache row"
    assert Path(db).stat().st_size >= before


def test_b5_view_shape_is_preserved_for_consumers(db):
    view = _prime(db, POLICY_A)
    for key in ("release", "schema", "unit_count", "active_count",
                "policy_identity", "vector_identity"):
        assert key in view
    assert view["schema"] == units_store.SEARCH_SCHEMA
    assert view["unit_count"] == 5 and view["active_count"] == 5
    assert view["release"].startswith("search-release-v1-")


def test_b5_legacy_path_unchanged_without_a_policy(db, monkeypatch):
    calls = _counting_fingerprint(monkeypatch)
    con = units_store.connect(str(db))
    try:
        first = units_store.release_view(con)          # computes + caches
        second = units_store.release_view(con)         # meta cache hit
    finally:
        con.close()
    assert first["release"] == second["release"]
    assert calls["n"] == 1
    con = _ro(db)
    try:
        meta = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta")}
    finally:
        con.close()
    assert meta["release"] == first["release"]
    assert meta["release_dirty"] == "0"


# --- session-level observability -------------------------------------------

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"
JOURNEYS = Path(__file__).parent / "fixtures/discovery01/" \
    "m4_frozen_journeys_v1.json"
CROSSWALK = Path(__file__).parent / "fixtures/discovery01/" \
    "m4_identity_crosswalk_v1.json"


@pytest.mark.skipif(not (Path(CATALOG).exists() and Path(SEARCH_DB).exists()
                         and Path(JOURNEYS).exists()),
                    reason="live M5 stores or frozen M4 fixtures unavailable")
def test_b5_session_reports_release_lookup_cost_and_honest_hit_timings():
    from harness.search import identity, indexer, m4_client, orchestrator
    catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    search.row_factory = sqlite3.Row
    icon = identity.connect(IDENTITY_DB) if Path(IDENTITY_DB).exists() \
        else None
    req = {"category": "power", "subcategory": "buck",
           "constraints": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
           "comparison": {"metric": "efficiency",
                          "mode": "condition_matched"}}
    deps = {"catalog_con": catalog, "search_con": search,
            "identity_con": icon,
            "aisle_map": indexer.aisle_map_from_wave(WAVE),
            "transport": m4_client.FrozenTransport(JOURNEYS),
            "crosswalk": orchestrator.load_crosswalk(CROSSWALK)}
    cache = orchestrator.SessionCache(max_entries=4)
    try:
        miss = orchestrator.engineering_session(req, cache=cache, **deps)
        hit = orchestrator.engineering_session(req, cache=cache, **deps)
    finally:
        catalog.close()
        search.close()
        if icon:
            icon.close()

    assert miss["cache"]["hit"] is False
    assert miss["timings_ms"]["release_identity_ms"] < 250, (
        "release lookup must be a primed O(1) read, got "
        f"{miss['timings_ms']['release_identity_ms']}ms")
    assert "release_identity_ms" in miss["timings_ms"]
    assert isinstance(miss["timings_ms"]["release_identity_ms"], float)

    assert hit["cache"]["hit"] is True
    timings = hit["timings_ms"]
    # a hit must not present the original miss's stage timings as its own
    assert "cached_stage_timings_ms" in timings
    assert timings["cached_stage_timings_ms"] == miss["timings_ms"]
    assert "release_identity_ms" in timings
    assert "note" in timings


def test_b5_read_only_routes_do_not_write_release_metadata(tmp_path,
                                                          monkeypatch):
    """An unauthenticated health probe must not recompute-and-persist the
    release under an empty policy identity (which clobbered the stored
    policy and invalidated other policies' caches)."""
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from harness.search import api

    path = tmp_path / "idx.db"
    con = units_store.connect(str(path))
    for i in range(3):
        _insert_unit(con, f"h{i}")
    con.commit()
    con.close()

    # a different policy owns the persisted meta release, as in production
    other = _prime(path, POLICY_B)
    con = units_store.connect(str(path))
    try:
        units_store.index_release(con, policy_identity=POLICY_B)
        meta_before = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta")}
        rows_before = con.execute(
            "SELECT count(*) FROM release_cache").fetchone()[0]
    finally:
        con.close()
    assert meta_before["policy_identity"] == POLICY_B

    monkeypatch.setattr(api, "SEARCH_DB", str(path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for route in ("/v1/health", "/v1/release"):
            with urllib.request.urlopen(base + route, timeout=30) as r:
                body = json.loads(r.read())
                assert r.status == 200
                assert body["release"].startswith("search-release-v1-")
                assert body["unit_count"] == 3
    finally:
        httpd.shutdown()
        httpd.server_close()

    con = units_store.connect(str(path))
    try:
        meta_after = {r["key"]: r["value"] for r in con.execute(
            "SELECT key, value FROM meta")}
        rows_after = con.execute(
            "SELECT count(*) FROM release_cache").fetchone()[0]
        still_valid = units_store.policy_release(
            con, POLICY_B)["release_cache"]
    finally:
        con.close()
    assert meta_after == meta_before, "a read route rewrote release metadata"
    assert rows_after == rows_before
    assert still_valid == "hit", "a read route invalidated another policy"
    assert other["release"] == meta_before["release"]
