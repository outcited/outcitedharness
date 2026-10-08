"""Search API tests (PRD-SEARCH-01 R4, deliverable 3)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from harness.search import api, indexer, units

CORPUS = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"


@pytest.fixture()
def server(tmp_path):
    db = tmp_path / "search.db"
    con = units.connect(str(db))
    catalog = indexer.catalog_from_corpus_jsonl(CORPUS)
    merged = list(indexer.claim_units(catalog)) + \
        list(indexer.family_units(catalog))
    grouped: dict[tuple[str, str], list[dict]] = {}
    for unit in merged:
        grouped.setdefault(
            (unit["doc_sha256"], unit["extraction_version"]), []).append(unit)
    for (doc, version), group in grouped.items():
        units.replace_document(con, group, doc_sha256=doc,
                               extraction_version=version)
    con.close()

    api.SEARCH_DB = str(db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def _post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_health_and_release(server):
    status, health = _get(server, "/v1/health")
    assert status == 200 and health["ok"]
    assert health["release"].startswith("search-release-v1-")
    assert health["unit_count"] > 0
    status, release = _get(server, "/v1/release")
    assert status == 200 and release["schema"]


def test_search_roundtrip(server):
    status, body = _post(server, "/v1/search", {
        "query": "low sleep current microcontroller", "limit": 5})
    assert status == 200
    assert body["schema"] == "harness.search-response.v1"
    assert body["query"]["original"] == "low sleep current microcontroller"
    assert body["query"]["interpretations"]
    assert body["provenance"]["release"]
    assert body["qualification"] == "none"
    assert body["units"]


def test_search_with_filters_and_no_interpretations(server):
    status, body = _post(server, "/v1/search", {
        "query": "72 MHz", "limit": 5, "interpretations": False,
        "filters": {"vendor": "st.com"}})
    assert status == 200
    assert body["query"]["interpretations"] == []
    assert all(u["vendor"] == "st.com" for u in body["units"])


def test_search_validation(server):
    try:
        _post(server, "/v1/search", {"query": "   "})
        raise AssertionError("expected 422")
    except urllib.error.HTTPError as e:
        assert e.code == 422
    try:
        _post(server, "/v1/nope", {"query": "x"})
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_unit_fetch_by_id(server):
    _, body = _post(server, "/v1/search",
                    {"query": "rdson 3.9", "limit": 1})
    unit_id = body["units"][0]["unit_id"]
    status, unit = _get(server, f"/v1/units/{unit_id}")
    assert status == 200
    assert unit["unit"]["unit_id"] == unit_id
    assert unit["qualification"] == "none"
    try:
        _get(server, "/v1/units/" + "0" * 32)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404
