"""DW-TRUTH-FUNNEL-01 — sampled source-to-served audit.

Six required contracts (issue #5), all deterministic and offline:
  * quote mismatch -> verification FAILS
  * wrong device identity -> exact-join FAILS
  * unserved claim -> NOT counted released
  * missing viewer resolver -> NOT clickable
  * missing source -> SOURCE_MISSING/UNKNOWN, never zero
  * determinism: two runs on pinned inputs identical except observation
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ssp", ROOT / "scripts/sampled_source_proof.py")
ssp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssp)


# ---------------------------------------------------------------------------
# helpers / fixtures (fully offline)
# ---------------------------------------------------------------------------
def make_pdf(path: Path, page_texts):
    """Write a real multi-page PDF with a text layer; returns the sha256."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return ssp.sha256_file(path)


def place_in_cas(cas_root, page_texts):
    """Write a real PDF into CAS layout keyed by its own sha256; return sha."""
    pdf = Path(cas_root) / "tmp.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    make_pdf(pdf, page_texts)
    sha = ssp.sha256_file(pdf)
    dest = Path(cas_root) / sha[:2] / (sha + ".pdf")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf.read_bytes())
    pdf.unlink()
    return sha


def served_export(tmp_path, rows, release_id="rel-test"):
    ex = tmp_path / "export"
    ex.mkdir()
    with (ex / "compare_ready.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    (ex / "fold_manifest.json").write_text(json.dumps(
        {"fold": "strata->served", "cube": {"sha256": release_id}}))
    return ssp.load_served_index(ex)


# ---------------------------------------------------------------------------
# 1. quote mismatch -> verification FAILS
# ---------------------------------------------------------------------------
def test_quote_mismatch_fails_verification(tmp_path):
    cas = tmp_path / "cas"
    real = place_in_cas(cas, ["Output current 250 mA max"])
    assert ssp.verify_quote("Output current 250 mA max",
                            "the output current 250 mA max") is True
    assert ssp.verify_quote("Output current 999 mA max",
                            "the output current 250 mA max") is False
    row = {"pdf_sha": real, "page_1based": 1,
           "verbatim": "Output current 999 mA max"}
    rec = ssp.audit_claim(row, {"available": False, "rows": [], "opns": set()},
                          [str(cas)], "rel")
    assert rec["verification"]["status"] == "FAIL"
    assert rec["classification"] == ssp.FAILED


# ---------------------------------------------------------------------------
# 2. wrong device identity -> exact-join FAILS
# ---------------------------------------------------------------------------
def test_wrong_identity_not_exact_join():
    assert ssp.join_identity("STM32F429", "STM32F429") == ssp.EXACT
    assert ssp.join_identity("STM32F429", "STM32F103") == ssp.IDENT_UNKNOWN
    # doc-slug vs opn is not an exact join
    assert ssp.join_identity("ti-sllu158-pdf-ts-1", "SN65HVD23x") != ssp.EXACT


def test_wrong_identity_classifies_held_not_verified(tmp_path):
    cas = tmp_path / "cas"
    sha = place_in_cas(cas, ["VDS = 40 V"])
    served = served_export(tmp_path, [
        {"opn": "OTHER-PART", "doc_sha256": sha, "axis": "vds", "value": "40"}])
    row = {"part_number": "MY-PART", "field": "vds", "value_raw": "40",
           "pdf_sha": sha, "page_1based": 1, "verbatim": "VDS = 40 V"}
    rec = ssp.audit_claim(row, served, [str(cas)], "rel", )
    assert rec["identity_join"]["status"] != ssp.EXACT
    assert rec["classification"] != ssp.VERIFIED


# ---------------------------------------------------------------------------
# 3. unserved claim -> NOT counted released
# ---------------------------------------------------------------------------
def test_unserved_claim_not_released(tmp_path):
    served = served_export(tmp_path, [
        {"opn": "A2", "doc_sha256": "deadbeef", "axis": "x", "value": "1"}])
    got = ssp.served_lookup(served, "A1", "cafebabe", "x", "1")
    assert got is False


def test_served_claim_is_released(tmp_path):
    served = served_export(tmp_path, [
        {"opn": "A1", "doc_sha256": "cafebabe", "axis": "x", "value": "1"}])
    assert ssp.served_lookup(served, "A1", "cafebabe", "x", "1") is True


# ---------------------------------------------------------------------------
# 4. missing viewer resolver -> NOT clickable
# ---------------------------------------------------------------------------
def test_missing_viewer_resolver_not_clickable(tmp_path):
    served = served_export(tmp_path, [
        {"opn": "A1", "doc_sha256": "cafebabe", "axis": "x", "value": "1"}])
    # a SHA-only export is not a link
    assert ssp.viewer_resolvable(served) == ssp.UNKNOWN
    assert ssp.viewer_resolvable(served) != "y"


def test_present_viewer_resolver_is_clickable(tmp_path):
    served = served_export(tmp_path, [
        {"opn": "A1", "doc_sha256": "cafebabe", "viewer": "/doc/cafebabe"}])
    assert ssp.viewer_resolvable(served) == "y"


# ---------------------------------------------------------------------------
# 5. missing source -> SOURCE_MISSING/UNKNOWN, never zero
# ---------------------------------------------------------------------------
def test_missing_source_is_source_missing_never_zero(tmp_path):
    row = {"pdf_sha": "0" * 64, "page_1based": 1, "verbatim": "whatever",
           "part_number": "P", "field": "f", "value_raw": 1}
    rec = ssp.audit_claim(row, {"available": False, "rows": [], "opns": set()},
                          [str(tmp_path / "nocas")], "rel")
    assert rec["classification"] == ssp.SOURCE_MISSING
    assert rec["classification"] != 0
    assert rec["source"]["doc_state"] == "absent"


def test_no_sha_is_source_missing(tmp_path):
    row = {"pdf_sha": None, "page_1based": 1, "verbatim": "x"}
    rec = ssp.audit_claim(row, {"available": False, "rows": [], "opns": set()},
                          [str(tmp_path)], "rel")
    assert rec["classification"] == ssp.SOURCE_MISSING
    assert rec["source"]["doc_state"] == "no_sha"


# ---------------------------------------------------------------------------
# 6. determinism: identical except observation metadata
# ---------------------------------------------------------------------------
def _pinned_inputs(tmp_path):
    cas = tmp_path / "cas"
    sha = place_in_cas(cas, ["VDS = 40 V"])
    power = [{"__lane": "POWER", "pdf_sha": sha, "page_1based": 1,
              "field": "vds", "value_raw": "40", "verbatim": "VDS = 40 V"},
             {"__lane": "POWER", "pdf_sha": "0" * 64, "page_1based": 2,
              "field": "x", "value_raw": 1, "verbatim": "nope"}]
    served = served_export(tmp_path, [
        {"opn": "P", "doc_sha256": sha, "axis": "vds", "value": "40"}])
    return power, served, [str(cas)]


def test_two_runs_identical_except_observation(tmp_path):
    power, served, cas = _pinned_inputs(tmp_path)
    r1 = [ssp.audit_claim(dict(r), served, cas, "rel") for r in power]
    r2 = [ssp.audit_claim(dict(r), served, cas, "rel") for r in power]
    j1 = json.dumps(r1, sort_keys=True, default=str)
    j2 = json.dumps(r2, sort_keys=True, default=str)
    assert j1 == j2


def test_deterministic_sample_is_stable():
    rows = list(range(1000))
    a = ssp.deterministic_sample(rows, 10)
    b = ssp.deterministic_sample(rows, 10)
    assert a == b and len(a) == 10


def test_classify_vocabulary_is_stable():
    assert ssp.classify(True, "present", True, ssp.EXACT) == ssp.VERIFIED
    assert ssp.classify(True, "present", False, ssp.EXACT) == ssp.HELD
    assert ssp.classify(False, "present", True, ssp.EXACT) == ssp.FAILED
    assert ssp.classify(None, "present", False, ssp.EXACT) == ssp.UNKNOWN
    assert ssp.classify(True, "absent", True, ssp.EXACT) == ssp.SOURCE_MISSING