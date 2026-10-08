"""Provenance serving gate + curve-lane integration tests
(P1/P2 directives 2026-10-08)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.search import indexer, query, units

CURVES = Path(__file__).parent / "fixtures/gold/search_fixture_curves.json"
CORPUS = Path(__file__).parent / "fixtures/gold/search_fixture_corpus.jsonl"


def test_evidence_grade_rules():
    real = "f" * 64
    assert units.evidence_grade(doc_sha256=real, page=3,
                                locator={"kind": "text_span"}) == \
        "evidence_grade"
    assert units.evidence_grade(doc_sha256=real, page=None,
                                locator={"kind": "image_region",
                                         "bbox": [1, 2, 3, 4]}) == \
        "evidence_grade"
    assert units.evidence_grade(doc_sha256=real, page=None,
                                locator={"kind": "text_span"}) == \
        "discovery_only"
    assert units.evidence_grade(doc_sha256="unhashed:stem", page=3,
                                locator={"kind": "text_span"}) == \
        "discovery_only"


def test_burn_wave_units_are_discovery_only(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    catalog = indexer.catalog_from_corpus_jsonl(CORPUS)
    merged = list(indexer.claim_units(catalog))
    for unit in merged:
        assert unit["doc_sha256"].startswith("unhashed:")
    units.replace_document(
        con, [u for u in merged if u["doc_sha256"] ==
              "unhashed:infineon-IPF039N08NF2S"])
    got = con.execute("SELECT * FROM units LIMIT 1").fetchone()
    parsed = units.row_to_unit(got)
    assert parsed["evidence_grade"] == "discovery_only"


def test_evidence_id_survives_reextraction(tmp_path):
    kwargs = dict(doc_sha256="f" * 64, grain="claim",
                  locator={"kind": "text_span", "quote": "80"},
                  text_repr="x = 80", vendor="v")
    v1 = units.make_unit(extraction_version="v1", **kwargs)
    v2 = units.make_unit(extraction_version="v2", **kwargs)
    assert v1["unit_id"] != v2["unit_id"]       # extraction identity moves
    assert v1["evidence_id"] == v2["evidence_id"]  # evidence identity stays


def test_structured_payload_roundtrip(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    unit = units.make_unit(
        doc_sha256="f" * 64, grain="figure",
        locator={"kind": "figure", "figure_index": 1},
        text_repr="efficiency curve", extraction_version="v1",
        structured={"schema": "harness.search-curve-figure.v1",
                    "series": [{"name": "s", "points": [[1, 2]]}]})
    units.replace_document(con, [unit])
    got = units.get_unit(con, unit["unit_id"])
    assert got["structured"]["series"][0]["points"] == [[1, 2]]


def test_curve_units_shape():
    out = list(indexer.curve_units([CURVES]))
    assert len(out) == 2
    eff = out[0]
    assert eff["grain"] == "figure"
    assert eff["page"] == 7
    assert eff["vendor"] == "texas_instruments"
    assert eff["category"] == "power"
    assert "Efficiency vs Output Current" in eff["text_repr"]
    assert "VIN = 12 V VOUT = 5 V" in eff["text_repr"]
    assert eff["applicability"] == [{"scope": "opn", "value": "TESTBUCK5",
                                     "coverage_kind": "primary"}]
    assert eff["structured"]["schema"] == "harness.search-curve-figure.v1"
    assert eff["structured"]["digitization_quality"][
        "x_fit_residual_pct_of_span"] == 0.012
    assert len(eff["structured"]["series"][0]["points"]) == 5
    # real document hash + printed page -> evidence-grade material
    assert units.unit_evidence_grade(eff) == "evidence_grade"
    assert eff["evidence_id"].startswith("ev-")


def test_curve_units_reject_hash_named_artifacts(tmp_path):
    record = json.loads(CURVES.read_text())
    record["source_artifact"] = (
        "0cef2db3d1a1b022d13de380a3038cf9f3a2c5155bc12cbd44daae708eaba175"
        ".pdf")
    path = tmp_path / "hash_named.json"
    path.write_text(json.dumps(record))
    out = list(indexer.curve_units([path]))
    assert len(out) == 2
    assert all(u["applicability"] == [] for u in out)  # never minted parts
    assert not any("0CEF2DB3" in u["text_repr"] for u in out)


def test_index_curves_idempotent(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    first = indexer.index_curves(con, CURVES.parent)
    again = indexer.index_curves(con, CURVES.parent)
    assert first["units"] == again["units"] == 2
    assert con.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 2


def test_envelope_marks_discovery_only_and_gates_verified(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    real = units.make_unit(
        doc_sha256="e" * 64, grain="claim",
        locator={"kind": "text_span", "quote": "90"},
        text_repr="REALPART efficiency = 90 %", extraction_version="v1",
        page=2, vendor="ti.com", ident=["REALPART"],
        verification_state="supported",
        verification_source="adjudication_ledger")
    placeholder = units.make_unit(
        doc_sha256="unhashed:stem-PHANTOM", grain="claim",
        locator={"kind": "text_span", "quote": "91"},
        text_repr="PHANTOM efficiency = 91 %", extraction_version="v1",
        vendor="ti.com", ident=["PHANTOM"],
        verification_state="supported",
        verification_source="adjudication_ledger")
    units.replace_document(con, [real])
    units.replace_document(con, [placeholder])

    both = query.search(con, "efficiency", limit=10,
                        with_interpretations=False)
    grades = {u["text_repr"].split()[0]: u["evidence_grade"]
              for u in both["units"]}
    assert grades["REALPART"] == "evidence_grade"
    assert grades["PHANTOM"] == "discovery_only"
    assert both["provenance"]["returned_evidence_grade"] >= 1
    assert both["provenance"]["returned_discovery_only"] >= 1
    assert all(u["evidence_id"].startswith("ev-") for u in both["units"])

    # P1 hard gate: a verified-evidence bar excludes discovery-only material
    gated = query.search(con, "efficiency", limit=10,
                         filters={"min_verification": "supported"},
                         with_interpretations=False)
    names = [u["text_repr"].split()[0] for u in gated["units"]]
    assert "REALPART" in names and "PHANTOM" not in names

    strict = query.search(con, "efficiency", limit=10,
                          filters={"evidence_grade": "evidence_grade"},
                          with_interpretations=False)
    assert all(u["evidence_grade"] == "evidence_grade"
               for u in strict["units"])


def test_corroboration_is_a_separate_signal(tmp_path):
    """source_count/conflict_class surface as corroboration metadata —
    never as verification (review policy 2026-10-08)."""
    catalog = indexer.catalog_from_corpus_jsonl(CORPUS)
    claims = list(indexer.claim_units(catalog))
    vds = [u for u in claims if "VDS = 80 V" in u["text_repr"]][0]
    corrob = vds["structured"]["corroboration"]
    assert corrob["source_count"] == 2
    assert corrob["conflict_class"] is None
    assert "not verification" in corrob["note"]
    assert vds["verification_state"] == "unverified"

    conflicted = [u for u in claims if "VDSS = 750 V" in u["text_repr"]][0]
    corrob2 = conflicted["structured"]["corroboration"]
    assert corrob2["conflict_class"] == "unresolved"
    assert corrob2["source_count"] == 1

    # a claim without consensus rows carries no corroboration at all
    plain = [u for u in claims if "QG = 54 nC" in u["text_repr"]][0]
    assert plain.get("structured") is None

    con = units.connect(str(tmp_path / "s.db"))
    units.replace_document(con, [vds])
    r = query.search(con, "VDS 80 V", limit=3, with_interpretations=False)
    hit = [u for u in r["units"] if u["text_repr"].startswith("IPF039")][0]
    assert hit["structured"]["corroboration"]["source_count"] == 2
    assert hit["verification_state"] == "unverified"


def test_vector_scores_numpy_and_python_agree(tmp_path):
    import numpy as np
    con = units.connect(str(tmp_path / "s.db"))
    base = units.make_unit(
        doc_sha256="d" * 64, grain="claim",
        locator={"kind": "text_span", "quote": "x"},
        text_repr="target unit", extraction_version="v1")
    other = units.make_unit(
        doc_sha256="d" * 63 + "e", grain="claim",
        locator={"kind": "text_span", "quote": "y"},
        text_repr="other unit", extraction_version="v1")
    units.replace_document(con, [base])
    units.replace_document(con, [other])
    units.attach_vector(con, base["unit_id"], "t", [1.0, 0.0])
    units.attach_vector(con, other["unit_id"], "t", [0.0, 1.0])
    from harness.search import query as q
    numpy_scores = q._vector_scores(con, [0.9, 0.1], 10)
    assert numpy_scores[base["unit_id"]] > numpy_scores[other["unit_id"]]
    # parity with the pure-python fallback
    rows = con.execute(
        "SELECT v.unit_id, v.vec FROM vectors v JOIN units u ON"
        " u.unit_id = v.unit_id AND u.retired=0").fetchall()
    py = {r["unit_id"]: units.cosine([0.9, 0.1],
                                     units.unpack_vector(r["vec"]))
          for r in rows}
    for uid, score in numpy_scores.items():
        assert abs(score - py[uid]) < 1e-5


def test_release_identity_tracks_policy(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    unit = units.make_unit(
        doc_sha256="c" * 64, grain="claim",
        locator={"kind": "text_span", "quote": "z"},
        text_repr="policy probe", extraction_version="v1")
    units.replace_document(con, [unit])
    a = units.index_release(con, policy_identity="policy-A")
    b = units.index_release(con, policy_identity="policy-A")  # cached
    assert b["release"] == a["release"]
    assert b["policy_identity"] == "policy-A"
    c = units.index_release(con, policy_identity="policy-B")
    assert c["release"] != a["release"]  # changed system, changed identity
    assert c["vector_identity"].startswith("brutefloat32-v1;models=none")


def test_make_embedder_wire_format():
    import json as _json
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = _json.loads(self.rfile.read(
                int(self.headers["Content-Length"])))
            data = [{"object": "embedding", "index": i,
                     "embedding": [float(i), 1.0]}
                    for i in range(len(body["input"]))]
            data.reverse()  # server may return out of order
            payload = _json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        from harness.search.query import make_embedder
        embed = make_embedder(
            f"http://127.0.0.1:{httpd.server_address[1]}/v1/embeddings")
        vectors = embed(["a", "b", "c"])
        assert vectors == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]
    finally:
        httpd.shutdown()


def test_weight_swap_purges_stale_vectors(tmp_path, monkeypatch):
    """Same model NAME with different served weights must purge the old
    vector space and change the release identity (observed 2026-10-08:
    e10b swapped checkpoints under bge-m3-cr-tapes-v1)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from search_index import attach_vectors
    from harness.search import query as q

    unit = units.make_unit(
        doc_sha256="9" * 64, grain="claim",
        locator={"kind": "text_span", "quote": "w"},
        text_repr="weight probe unit", extraction_version="v1")
    con = units.connect(str(tmp_path / "s.db"))
    units.replace_document(con, [unit])

    def fake_embedder(weights):
        def embed(texts):
            return [[weights, 1.0 - weights] for _ in texts]
        return embed

    monkeypatch.setattr(q, "make_embedder",
                        lambda url, model="m", timeout=30.0:
                        fake_embedder(0.25))
    first = attach_vectors(con, "http://fake", "same-name-v1")
    assert first["attached"] == 1 and first["purged_stale_weights"] == 0
    fp1 = first["fingerprint"]

    monkeypatch.setattr(q, "make_embedder",
                        lambda url, model="m", timeout=30.0:
                        fake_embedder(0.75))
    second = attach_vectors(con, "http://fake", "same-name-v1")
    assert second["purged_stale_weights"] == 1     # old space dropped
    assert second["attached"] == 1                 # re-embedded everything
    assert second["fingerprint"] != fp1            # identity moved
    identity = units.vector_identity(con)
    assert f"fp={second['fingerprint']}" in identity

    # and a no-op run with unchanged weights skips re-embedding
    third = attach_vectors(con, "http://fake", "same-name-v1")
    assert third["purged_stale_weights"] == 0
    assert third["attached"] == 0


def test_curve_query_returns_conditions_and_uncertainty(tmp_path):
    con = units.connect(str(tmp_path / "s.db"))
    indexer.index_curves(con, CURVES.parent)
    r = query.search(con, "efficiency vs output current 12 V input",
                     limit=5, filters={"grain": "figure"},
                     with_interpretations=False)
    assert r["units"]
    top = r["units"][0]
    assert top["evidence_grade"] == "evidence_grade"
    slim = top["structured"]
    assert slim["schema"] == "harness.search-curve-figure.v1"
    assert slim["series"][0]["condition"] == "VIN = 12 V VOUT = 5 V fsw = 500 kHz"
    assert "point_count" in slim["series"][0]          # slimmed in envelope
    assert "points" not in slim["series"][0]
    full = units.get_unit(con, top["unit_id"])
    assert len(full["structured"]["series"][0]["points"]) == 5  # full kept
    assert full["structured"]["digitization_quality"]
