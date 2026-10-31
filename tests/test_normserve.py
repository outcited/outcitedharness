"""NORM-SERVE-01 tests (issue #64 contract).

Positive: ST1S15 proving sample (e2e-st1s15-raw.jsonl) loads, API serves
it with page-level provenance. Negative: missing provenance, failed vault
re-verification, missing rule_version/verified_by — all rejected, never
served.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from harness.normserve.loader import Store, load_rows
from harness.normserve.serve import make_handler
from harness.normserve.vault import verify_sha256

ST1S15_SHA = "82d2ee0cc7dd7c7aa47fed41ab13e9593d37f49d858470b56d50e648a104c222"

def make_rows(sha):
    rows = json.loads(json.dumps(SAMPLE_ROWS).replace(ST1S15_SHA, sha))
    return rows


SAMPLE_ROWS = [
    {"field": "vin_min_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 8, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 2.3, "value_role": "range",
     "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
     "verdict": "printed_row"},
    {"field": "vin_max_v", "method": "deterministic_power_axes_v2_v1",
     "page_1based": 8, "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "characteristics", "unit": "V", "unit_as_printed": "V",
     "value": 5.5, "value_role": "range",
     "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
     "verdict": "printed_row"},
    {"condition_verbatim": None, "field": "vout",
     "method": "deterministic_power_axes_v2_v1", "page_1based": 5,
     "part_number": ST1S15_SHA, "pdf_sha": "",
     "schema": "harness.electronics-power-axes-v2.v1",
     "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
     "table_kind": "output_voltage", "unit": "V", "value_role": "fixed",
     "verbatim": "VOUT | Output voltage | 9 | V",
     "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0},
]

# Non-entailing row (NEO review, merge 47cac640): the old positive sample
# asserted vout=9.0 from the versions-availability line
# "…versions of the ST1S15 available on | 9" — an over-claim the substring
# check cannot fully police. This fixture (claim value absent from the
# quoted row) is the canonical negative for the entailment gate.
NON_ENTAILING_ROW = {
    "condition_verbatim": None, "field": "vout",
    "method": "deterministic_power_axes_v2_v1", "page_1based": 2,
    "part_number": ST1S15_SHA, "pdf_sha": "",
    "schema": "harness.electronics-power-axes-v2.v1",
    "source_url": "https://www.st.com/resource/en/datasheet/st1s15.pdf",
    "table_kind": "characteristics", "unit": "V", "value_role": "fixed",
    "verbatim": "VIN | Operating input voltage range | 2.3 | 5.5 | V",
    "verdict": "printed_row", "vout_max_v": 9.0, "vout_min_v": 9.0,
}


@pytest.fixture()
def vault(tmp_path):
    import hashlib
    root = tmp_path / "vault"
    data = b"%PDF-1.7 ST1S15 fixture"
    sha = hashlib.sha256(data).hexdigest()
    blob = root / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    return str(root), sha


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path / "norm.db"))


def test_vault_reverification(vault):
    root, sha = vault
    ok, detail = verify_sha256(root, sha)
    assert ok, detail
    ok, detail = verify_sha256(root, "f" * 64)
    assert not ok


def test_st1s15_sample_loads_with_vault(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.rejections == [], report.rejections
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    fields = {c["field"]: c for c in claims}
    assert fields["vin_min_v"]["value"] == 2.3
    assert fields["vout"]["value"] == 9.0
    assert fields["vout"]["value_min"] is None and \
        fields["vout"]["value_max"] is None
    assert fields["vin_min_v"]["page"] == 8
    assert "2.3 | 5.5" in fields["vin_min_v"]["verbatim"]


def test_dry_run_writes_nothing(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       dry_run=True, opn_default="ST1S15")
    assert report.loaded == 3 and report.rejections == []
    assert store.claims_for_opn("st1s15") == []


def test_serve_endpoint(store, vault):
    root, sha = vault
    load_rows(store, make_rows(sha), vault_root=root, opn_default="ST1S15")
    import io
    from http.server import ThreadingHTTPServer
    import threading
    import urllib.request

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/v1/opn/st1s15") as r:
            payload = json.loads(r.read())
        assert payload["opn"] == "ST1S15"
        assert payload["count"] == 3
        c = payload["claims"][0]
        for k in ("doc_sha", "page", "verbatim", "verified_by",
                  "rule_version"):
            assert c[k], f"provenance field {k} missing from serve payload"
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/opn/NOPE") as r:
                assert r.status == 404
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


# --- negative tests (mandatory, issue #64 + merge-47cac640 review) ---

def test_persist_without_vault_root_raises(store):
    with pytest.raises(ValueError, match="vault_root is mandatory"):
        load_rows(store, make_rows("a" * 64), opn_default="ST1S15")
    assert store.claims_for_opn("st1s15") == []


def test_non_entailing_verbatim_rejected(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([NON_ENTAILING_ROW]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "entail" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_verbatim(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps(
        [dict(SAMPLE_ROWS[0], verbatim="")]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert len(report.rejections) == 1
    assert "verbatim" in report.rejections[0]["reason"]
    assert store.claims_for_opn("st1s15") == []


def test_reject_missing_page(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["page_1based"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1


def test_reject_bad_sha(store, vault):
    root, sha = vault
    rows = [dict(SAMPLE_ROWS[0], part_number="deadbeef")]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "64-hex" in report.rejections[0]["reason"]


def test_reject_vault_hash_mismatch(store, vault):
    root, real_sha = vault
    sha = "a" * 64
    rows = make_rows(real_sha)
    for r in rows:
        r["part_number"] = sha
    blob = Path(root) / "blobs" / "aa" / "aa" / f"{sha}.pdf"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"different bytes")
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0 and len(report.rejections) == 3
    assert all("mismatch" in r["reason"] for r in report.rejections)


def test_reject_missing_rule_version_and_verified_by(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["schema"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "rule_version" in report.rejections[0]["reason"]
    # verified_by is the evidence-backed verdict — a row with no verdict
    # has nothing vouching for it and is rejected (method alone is NOT
    # verification; it is kept separately as extractor_method).
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    del rows[0]["verdict"]
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 0
    assert "verified_by" in report.rejections[0]["reason"]


def test_extractor_method_recorded_separately(store, vault):
    root, sha = vault
    report = load_rows(store, make_rows(sha), vault_root=root,
                       opn_default="ST1S15")
    assert report.loaded == 3
    claims = store.claims_for_opn("st1s15")
    for c in claims:
        assert c["verified_by"] == "printed_row"
        assert c["extractor_method"] == "deterministic_power_axes_v2_v1"


def test_replay_loads_zero_new_rows(store, vault):
    root, sha = vault
    rows = make_rows(sha)
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 3 and first.duplicates == 0
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0, "replay must not create new rows"
    assert replay.duplicates == 3
    assert replay.rejections == []
    assert len(store.claims_for_opn("st1s15")) == 3


def test_unique_key_null_safe(store, vault):
    root, sha = vault
    rows = [json.loads(json.dumps(
        SAMPLE_ROWS[0]).replace(ST1S15_SHA, sha))]
    rows[0].pop("value_role", None)
    rows[0].pop("condition_verbatim", None)
    first = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert first.loaded == 1
    replay = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert replay.loaded == 0 and replay.duplicates == 1, \
        "NULL value_role/condition_verbatim must dedupe (null-safe UNIQUE)"
    assert len(store.claims_for_opn("st1s15")) == 1


def test_reject_no_opn_and_no_default(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps([SAMPLE_ROWS[0]]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root)
    assert report.loaded == 0 and len(report.rejections) == 1
    assert "opn" in report.rejections[0]["reason"]


def test_rejections_never_swallowed(store, vault):
    root, sha = vault
    rows = json.loads(json.dumps(
        [dict(SAMPLE_ROWS[0], verbatim=""),
         SAMPLE_ROWS[1]]).replace(ST1S15_SHA, sha))
    report = load_rows(store, rows, vault_root=root, opn_default="ST1S15")
    assert report.loaded == 1
    assert len(report.rejections) == 1
    assert report.rejections[0]["index"] == 0
    assert report.summary()["rejected"] == 1
