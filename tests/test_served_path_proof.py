"""DW-TRUTH-FUNNEL-02 — served-path proof (issue #5 phase 2), corrected.

Required negative contracts, all deterministic and offline:
  * wrong OPN or manufacturer fails identity;
  * source sha or quote mismatch fails;
  * fold/cube identity mismatch fails;
  * committed-but-undeployed bundle is NOT deployed;
  * missing viewer artifact is not clickable;
  * static artifact present but not claim-bound is not clickable;
  * missing source / unknown release authorization -> SOURCE_MISSING/UNKNOWN,
    never zero;
  * two-run reproducibility (identical except observation metadata).

Owner-required correction tests (C1–C5):
  * C1a  same-document / different-claim collision: fold match must NOT succeed
         on doc-sha alone;
  * C2a  export-vs-FE divergence: field mismatch -> DIVERGENT, not bundled;
  * C2b  unconstructible claim key (slug-shaped OPN / missing fields) -> UNKNOWN;
  * C3a  corrupted cube bytes (fingerprint check FAILS loudly);
  * C4a  verbatim/normalisation classification of failed quotes;
  * C5a  non-verbatim quotation: source link FAILS.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "spp", ROOT / "scripts/served_path_proof.py")
spp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spp)


# ---------------------------------------------------------------------------
# offline fixtures
# ---------------------------------------------------------------------------
def make_pdf(path: Path, page_texts):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return spp.sha256_file(path)


def place_in_cas(cas_root, page_texts):
    pdf = Path(cas_root) / "tmp.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    make_pdf(pdf, page_texts)
    sha = spp.sha256_file(pdf)
    dest = Path(cas_root) / sha[:2] / (sha + ".pdf")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf.read_bytes())
    pdf.unlink()
    return sha


def fe_row(**kw):
    base = {"axis": "vds", "op": "min_rating", "opn": "PART-1",
            "doc_sha256": "0" * 64, "symbol": "VDS", "symbol_norm": "vds",
            "rating_class": "rated_limit", "condition_norm": "",
            "value": "40", "unit": "V", "quote": "Vds 40 V", "page": 1,
            "match": "exact", "value_si": 40.0, "unit_si": "v"}
    base.update(kw)
    return base


def write_export(root: Path, rows, fold_fold="strata->served",
                 cube_sha="cubesha", parts=None, cube_body=None):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "compare_ready.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    (root / "fold_manifest.json").write_text(json.dumps(
        {"fold": fold_fold, "cube": {"sha256": cube_sha}}))
    if cube_body is not None:
        (root / "cube.json").write_bytes(cube_body)
    if parts is not None:
        with (root / "parts_catalog.jsonl").open("w") as fh:
            for p in parts:
                fh.write(json.dumps(p) + "\n")
    return root


def write_bundle(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    return path


def base_ctx(tmp_path, **over):
    ctx = {
        "catalog": {"part-1": "Acme"},
        "export_opns": {"PART-1": "PART-1"},
        "export_rows_by_opn": {"part-1": [fe_row()]},
        "export_claim_keys": {"part-1": {spp._key_tuple(spp.build_claim_key(fe_row())[0]): fe_row()}},
        "exp_shas": {"0" * 64},
        "cas": [],
        "fold": {"fold": "strata->served", "cube": {"sha256": "cubesha"}},
        "cube_sha": "cubesha",
        "fe_exact": set(),
        "fe_by_evidence": set(),
        "deployed_sha": None,
        "fe_commit": "fecommit",
        "evidence": {},
        "resolve_probe": "unknown",
        "approval_record": False,
        "fe_rows_by_sha": {},
    }
    ctx.update(over)
    return ctx


# ---------------------------------------------------------------------------
# 1. wrong OPN / manufacturer fails identity
# ---------------------------------------------------------------------------
def test_wrong_opn_fails_identity(tmp_path):
    row = fe_row(opn="TOTALLY-DIFFERENT")
    ctx = base_ctx(tmp_path, catalog={"part-1": "Acme"},
                   export_opns={"PART-1": "PART-1"})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["identity"]["join"] == spp.UNKNOWN
    assert rec["identity"]["class"] == spp.SOURCE_MISSING


def test_wrong_manufacturer_catalog_miss_fails_identity(tmp_path):
    row = fe_row(opn="PART-1")
    ctx = base_ctx(tmp_path, catalog={})  # catalog empty -> not in catalog
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["identity"]["in_catalog"] is False
    assert rec["identity"]["class"] == spp.SOURCE_MISSING


def test_exact_identity_verified_only_when_in_catalog():
    assert spp.classify_identity(spp.IDENT_EXACT, True) == spp.VERIFIED
    assert spp.classify_identity(spp.IDENT_EXACT, False) == spp.SOURCE_MISSING
    assert spp.classify_identity(spp.IDENT_PARTIAL, True) == spp.HELD


# ---------------------------------------------------------------------------
# 2. source sha / quote mismatch fails
# ---------------------------------------------------------------------------
def test_source_quote_mismatch_fails(tmp_path):
    cas = tmp_path / "cas"
    sha = place_in_cas(cas, ["Output current 250 mA max"])
    row = fe_row(doc_sha256=sha, quote="Output current 999 mA max", page=1)
    ctx = base_ctx(tmp_path, cas=[str(cas)], exp_shas={sha})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["source"]["quote_ok"] is False
    assert rec["source"]["class"] == spp.FAILED


def test_source_sha_missing_is_source_missing_not_zero(tmp_path):
    cas = tmp_path / "cas"
    cas.mkdir(parents=True)
    row = fe_row(doc_sha256="a" * 64, quote="anything")
    ctx = base_ctx(tmp_path, cas=[str(cas)], exp_shas={"a" * 64})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["source"]["doc_state"] == "absent"
    assert rec["source"]["class"] == spp.SOURCE_MISSING


def test_source_quote_match_verifies(tmp_path):
    cas = tmp_path / "cas"
    sha = place_in_cas(cas, ["Vds 40 V max"])
    row = fe_row(doc_sha256=sha, quote="Vds 40 V", page=1)
    ctx = base_ctx(tmp_path, cas=[str(cas)], exp_shas={sha})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["source"]["class"] == spp.VERIFIED


# ---------------------------------------------------------------------------
# 3. C1a — fold must match CLAIM identity, NOT doc sha (collision test)
# ---------------------------------------------------------------------------
def test_fold_doc_sha_only_collision_does_not_verify(tmp_path):
    """Same doc sha, DIFFERENT claim (axis/value/page) -> fold must NOT be
    VERIFIED on doc-sha overlap alone."""
    sha = "0" * 64
    fe = fe_row(opn="PART-1", doc_sha256=sha, axis="vds", value="40", page=1)
    exp_other_claim = fe_row(opn="PART-1", doc_sha256=sha, axis="id_cont",
                             value="7.4", page=3)
    ctx = base_ctx(
        tmp_path,
        export_opns={"PART-1": "PART-1"},
        export_rows_by_opn={"part-1": [exp_other_claim]},
        exp_shas={sha},
    )
    rec = spp.audit_claim(json.dumps(fe, sort_keys=True), fe, "doc_sha_overlap", ctx)
    assert rec["fold"]["class"] != spp.FOLD_VERIFIED
    assert rec["fold"]["class"] == spp.FOLD_IDENTITY_MISMATCH
    assert spp.R_FIELD_MISMATCH in rec["fold"]["detail"]["reasons"]
    assert spp.R_LOCATOR_MISMATCH in rec["fold"]["detail"]["reasons"]
    # the doc-sha overlap is recorded only as a weak, separate signal
    assert rec["fold"]["weak_signals"]["doc_sha_in_export"] is True


def test_fold_doc_sha_only_no_identity_row_is_not_verified():
    """Doc sha present, but NO export row for this OPN -> DOC_SHA_ONLY, never
    VERIFIED."""
    cls, detail = spp.compare_fold_claim(
        {"opn": "SLUG-OPN", "axis": "vds", "value": "40", "unit": "V",
         "conditions": "", "page": 1, "doc_sha256": "0" * 64},
        [fe_row(opn="OTHER", doc_sha256="0" * 64)])
    assert cls == spp.FOLD_DOC_SHA_ONLY
    assert cls != spp.FOLD_VERIFIED
    assert detail["reason"] == spp.R_ROW_ABSENT


def test_fold_full_claim_identity_verifies():
    claim = {"opn": "PART-1", "axis": "vds", "value": "40", "unit": "V",
             "conditions": "", "page": 1, "doc_sha256": "0" * 64}
    cls, detail = spp.compare_fold_claim([claim][0], [fe_row(opn="PART-1")])
    assert cls == spp.FOLD_VERIFIED


def test_fold_value_mismatch_reason_recorded():
    claim = {"opn": "PART-1", "axis": "vds", "value": "41", "unit": "V",
             "conditions": "", "page": 1, "doc_sha256": "0" * 64}
    cls, detail = spp.compare_fold_claim(claim, [fe_row(opn="PART-1")])
    assert cls == spp.FOLD_IDENTITY_PARTIAL
    assert spp.R_VALUE_MISMATCH in detail["reasons"]


# ---------------------------------------------------------------------------
# 3b. legacy fold/cube ladder retained (regression reference)
# ---------------------------------------------------------------------------
def test_fold_cube_mismatch_fails():
    assert spp.classify_fold_legacy(True, True, True) == spp.VERIFIED
    assert spp.classify_fold_legacy(True, False, True) == spp.FAILED
    assert spp.classify_fold_legacy(True, True, False) == spp.FAILED
    assert spp.classify_fold_legacy(False, True, True) == "ABSENT"


def test_fold_cube_mismatch_flagged(tmp_path):
    row = fe_row()
    ctx = base_ctx(tmp_path, cube_sha="DIFFERENT-CUBE")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["fold"]["export_cube_sha256"] == "DIFFERENT-CUBE"


# ---------------------------------------------------------------------------
# 4. C2a/C2b — independent FE intersection comparison
# ---------------------------------------------------------------------------
def test_bundle_divergence_is_not_bundled():
    """Key present both sides, fields differ -> DIVERGENT, never VERIFIED."""
    fe = {"opn": "PART-1", "axis": "vds", "value": "40", "unit": "V",
          "doc_sha256": "0" * 64, "page": 1}
    exp = {"opn": "PART-1", "axis": "id_cont", "value": "7.4", "unit": "A",
           "doc_sha256": "0" * 64, "page": 3}
    fk, _ = spp.build_claim_key(fe)
    ek, _ = spp.build_claim_key(exp)
    status, detail = spp.compare_claim_keys(ek, fk)
    assert status == "divergent"
    assert "axis" in detail["field_diffs"]
    assert "value" in detail["field_diffs"]


def test_bundle_identical_key_matches():
    row = fe_row()
    k1, _ = spp.build_claim_key(row)
    k2, _ = spp.build_claim_key(dict(row))
    status, _ = spp.compare_claim_keys(k1, k2)
    assert status == "match"


def test_bundle_unconstructible_slug_opn_is_unknown(tmp_path):
    slug = "infineon-psoc-6-mcu-cyb0644xxzi-s2d44-datasheet-datasheet"
    row = fe_row(opn=slug, doc_sha256="0" * 64)
    key, reason = spp.build_claim_key(row)
    assert key is None
    assert reason == "opn_slug_shaped"
    ctx = base_ctx(tmp_path, export_opns={}, export_rows_by_opn={},
                   export_claim_keys={})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["bundle"]["class"] == spp.BUNDLE_UNKNOWN


def test_bundle_unconstructible_missing_field_is_unknown(tmp_path):
    row = fe_row(opn="PART-1", doc_sha256="0" * 64)
    del row["unit"]
    key, reason = spp.build_claim_key(row)
    assert key is None and reason == "unit_missing"


def test_bundle_absent_when_key_constructible_but_not_in_export(tmp_path):
    """FE key constructible; export has keys for the identity but none equal and
    the export never has this exact key -> DIVERGENT (keys exist); if the export
    has no key at all for the identity -> UNKNOWN."""
    row = fe_row(opn="PART-1", doc_sha256="0" * 64)
    ctx = base_ctx(tmp_path, export_opns={"PART-1": "PART-1"},
                   export_rows_by_opn={}, export_claim_keys={})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["bundle"]["class"] == spp.BUNDLE_UNKNOWN


def test_audit_bundle_divergent_on_same_sha_different_claim(tmp_path):
    sha = "0" * 64
    fe = fe_row(opn="PART-1", doc_sha256=sha, axis="vds", value="40", page=1)
    exp = fe_row(opn="PART-1", doc_sha256=sha, axis="id_cont", value="7.4", page=3)
    ek = spp.build_claim_key(exp)[0]
    ctx = base_ctx(
        tmp_path,
        export_claim_keys={"part-1": {spp._key_tuple(ek): exp}},
        export_rows_by_opn={"part-1": [exp]},
    )
    rec = spp.audit_claim(json.dumps(fe, sort_keys=True), fe, "doc_sha_overlap", ctx)
    assert rec["bundle"]["class"] == spp.BUNDLE_DIVERGENT
    assert rec["bundle"]["class"] != spp.BUNDLE_VERIFIED


def test_audit_bundle_verified_on_exact_key_match(tmp_path):
    row = fe_row(opn="PART-1", doc_sha256="0" * 64)
    k, _ = spp.build_claim_key(row)
    ctx = base_ctx(
        tmp_path,
        export_claim_keys={"part-1": {spp._key_tuple(k): row}},
        export_rows_by_opn={"part-1": [row]},
    )
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["bundle"]["class"] == spp.BUNDLE_VERIFIED


# ---------------------------------------------------------------------------
# 4b. committed-but-undeployed is NOT deployed
# ---------------------------------------------------------------------------
def test_committed_but_undeployed_is_not_deployed():
    assert spp.classify_deployed("not_deployed") == spp.HELD
    assert spp.classify_deployed("deployed") == spp.VERIFIED
    assert spp.classify_deployed("unknown") == spp.UNKNOWN


def test_deploy_state_from_sha_mismatch(tmp_path):
    row = fe_row()
    ctx = base_ctx(tmp_path, deployed_sha="othecommit", fe_commit="fecommit")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["deployed"]["state"] == "not_deployed"
    assert rec["deployed"]["class"] == spp.HELD


def test_no_deployment_records_is_unknown(tmp_path):
    row = fe_row()
    ctx = base_ctx(tmp_path, deployed_sha=None)
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["deployed"]["class"] == spp.UNKNOWN


# ---------------------------------------------------------------------------
# 5. missing viewer artifact not clickable
# ---------------------------------------------------------------------------
def test_missing_artifact_not_clickable(tmp_path):
    row = fe_row(doc_sha256="b" * 64, page=5)
    ctx = base_ctx(tmp_path, evidence={}, resolve_probe="served")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["resolve"]["artifact_bound"] is False
    assert rec["resolve"]["class"] == spp.MISSING


# ---------------------------------------------------------------------------
# 6. static artifact present but not claim-bound -> not clickable
# ---------------------------------------------------------------------------
def test_unbound_static_artifact_not_clickable(tmp_path):
    doc_sha = "c" * 64
    row = fe_row(doc_sha256=doc_sha, page=5)
    ctx = base_ctx(tmp_path, evidence={doc_sha: {7}}, resolve_probe="served")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["resolve"]["artifact_bound"] is False
    assert rec["resolve"]["class"] == spp.UNBOUND


def test_bound_but_gated_is_unknown(tmp_path):
    doc_sha = "d" * 64
    row = fe_row(doc_sha256=doc_sha, page=5)
    ctx = base_ctx(tmp_path, evidence={doc_sha: {5}}, resolve_probe="gated")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["resolve"]["artifact_bound"] is True
    assert rec["resolve"]["class"] == spp.UNKNOWN


def test_bound_and_served_is_clickable(tmp_path):
    doc_sha = "e" * 64
    row = fe_row(doc_sha256=doc_sha, page=5)
    ctx = base_ctx(tmp_path, evidence={doc_sha: {5}}, resolve_probe="served")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["resolve"]["class"] == spp.VERIFIED


# ---------------------------------------------------------------------------
# 7. missing source / unknown authorization -> never zero
# ---------------------------------------------------------------------------
def test_unknown_authorization_is_unknown_never_no():
    assert spp.classify_authorized(False) == spp.UNKNOWN
    assert spp.classify_authorized(True) == spp.VERIFIED
    row = fe_row()
    ctx = {"catalog": {}, "export_opns": {}, "export_rows_by_opn": {},
           "export_claim_keys": {}, "exp_shas": set(), "cas": [],
           "fold": {"fold": "f", "cube": {"sha256": "s"}}, "cube_sha": "s",
           "fe_exact": set(), "fe_by_evidence": set(), "deployed_sha": None,
           "fe_commit": None, "evidence": {}, "resolve_probe": "unknown",
           "approval_record": False, "fe_rows_by_sha": {}}
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "byte_exact", ctx)
    assert rec["authorized"]["class"] == spp.UNKNOWN


# ---------------------------------------------------------------------------
# 8. C3a — corrupted cube bytes FAIL the fingerprint check loudly
# ---------------------------------------------------------------------------
def test_cube_fingerprint_verifies_actual_bytes(tmp_path):
    good = b'{"cells": []}'
    root = tmp_path / "export"
    write_export(root, [fe_row()], cube_sha=spp.sha256_bytes(good),
                 cube_body=good)
    fold = json.loads((root / "fold_manifest.json").read_text())
    fp = spp.verify_cube_fingerprint(root, fold)
    assert fp["present"] is True
    assert fp["measured_sha256"] == spp.sha256_bytes(good)
    assert fp["match"] is True


def test_cube_fingerprint_corrupted_bytes_fails(tmp_path):
    good = b'{"cells": []}'
    root = tmp_path / "export"
    # manifest claims the good sha, but the file on disk is corrupted
    write_export(root, [fe_row()], cube_sha=spp.sha256_bytes(good),
                 cube_body=b'{"cells": [CORRUPTED]}')
    fold = json.loads((root / "fold_manifest.json").read_text())
    fp = spp.verify_cube_fingerprint(root, fold)
    assert fp["present"] is True
    assert fp["measured_sha256"] != fp["manifest_cube_sha256"]
    assert fp["match"] is False
    assert fp["reason"]


def test_cube_fingerprint_missing_file_not_match(tmp_path):
    root = tmp_path / "export"
    write_export(root, [fe_row()], cube_sha="deadbeef")
    fold = json.loads((root / "fold_manifest.json").read_text())
    fp = spp.verify_cube_fingerprint(root, fold)
    assert fp["present"] is False
    assert fp["match"] is False


def test_compare_ready_no_recorded_digest_is_recorded_not_invented(tmp_path):
    p = tmp_path / "compare_ready.jsonl"
    p.write_text("{}\n")
    rec = spp.verify_recorded_digest(p, None)
    assert rec["recorded_sha256"] is None
    assert rec["measured_sha256"] is None
    assert rec["verified"] is False
    assert rec["reason"] == "no recorded digest exists"


def test_compare_ready_recorded_digest_verified(tmp_path):
    p = tmp_path / "compare_ready.jsonl"
    p.write_text("{}\n")
    rec = spp.verify_recorded_digest(p, spp.sha256_file(p))
    assert rec["verified"] is True
    rec2 = spp.verify_recorded_digest(p, "0" * 64)
    assert rec2["verified"] is False


# ---------------------------------------------------------------------------
# 9. C4a — quote-failure classification (never repairs the claim)
# ---------------------------------------------------------------------------
def test_classify_quote_failure_strict_verbatim_not_a_failure():
    disp, _ = spp.classify_quote_failure("Vds 40 V", "the Vds 40 V limit")
    assert disp == spp.VERIFIED


def test_classify_quote_failure_extraction_artifact_pipe():
    disp, ev = spp.classify_quote_failure(
        "RthJA | - - | 40 | C/W", "RthJA - - 40 C/W thermal")
    assert disp == spp.EXTRACTION_ARTIFACT
    assert ev["level"] in ("L1", "L2", "L3", "L3b")


def test_classify_quote_failure_defect_when_tokens_absent():
    disp, ev = spp.classify_quote_failure(
        "RthJA 999 K/W totally_absent_token_xyz", "no such content here")
    assert disp == spp.QUOTE_DEFECT
    assert ev["tokens_present"] < ev["tokens_total"]


def test_classify_quote_failure_unknown_when_no_page_text():
    disp, ev = spp.classify_quote_failure("alpha beta gamma delta", None)
    assert disp == spp.UNKNOWN
    assert ev["reason"]


def test_classify_quote_failure_token_reorder_is_extraction_artifact():
    disp, _ = spp.classify_quote_failure(
        "alpha beta gamma delta", "gamma alpha beta delta")
    assert disp == spp.EXTRACTION_ARTIFACT


def test_verify_quote_normalized_documents_level():
    d = spp.verify_quote_normalized("A | B | C", "A B C")
    assert d["strict_verbatim"] is True
    assert d["level"] in ("L1", "L2", "L3", "L3b")


# ---------------------------------------------------------------------------
# 10. C5a — non-verbatim quotation: source link FAILS
# ---------------------------------------------------------------------------
def test_non_verbatim_quote_source_link_fails(tmp_path):
    cas = tmp_path / "cas"
    sha = place_in_cas(cas, ["The output current is 250 mA"])
    row = fe_row(doc_sha256=sha, quote="output current is 999 mA", page=1)
    ctx = base_ctx(tmp_path, cas=[str(cas)], exp_shas={sha})
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["source"]["quote_ok"] is False
    assert rec["source"]["class"] == spp.FAILED


# ---------------------------------------------------------------------------
# byte-exact / claim-key intersection / cohort
# ---------------------------------------------------------------------------
def test_byte_exact_intersection_measured():
    a = ['{"x":1}', '{"x":2}']
    b = ['{"x":2}', '{"x":3}']
    assert spp.byte_exact_intersection(a, b) == ['{"x":2}']


def test_claim_key_intersection_measured():
    exp = [fe_row(opn="P1", doc_sha256="1" * 64)]
    fe = [fe_row(opn="P1", doc_sha256="1" * 64)]
    el = [json.dumps(r, sort_keys=True) for r in exp]
    fl = [json.dumps(r, sort_keys=True) for r in fe]
    assert len(spp.claim_key_intersection(el, fl)) == 1
    # same doc sha, different claim -> NOT an exact claim-key intersection
    fe2 = [fe_row(opn="P1", doc_sha256="1" * 64, value="41")]
    fl2 = [json.dumps(r, sort_keys=True) for r in fe2]
    assert len(spp.claim_key_intersection(el, fl2)) == 0


def test_byte_exact_zero_triggers_documented_shortfall(tmp_path):
    exp_rows = [fe_row(opn="E1", doc_sha256="1" * 64)]
    fe_rows = [fe_row(opn="F1", doc_sha256="1" * 64)]  # same doc, different row
    er = [json.dumps(r, sort_keys=True) for r in exp_rows]
    fr = [json.dumps(r, sort_keys=True) for r in fe_rows]
    cohort, meta = spp.build_cohort(er, fr, spp.export_identity_opns(er), 10,
                                    exp_shas=spp.export_doc_shas(er))
    assert meta["byte_exact"] == 0
    assert meta["claim_key_exact"] == 0
    assert meta["shortfall"] is True
    assert meta["selection"] == "doc_sha_overlap"
    assert len(cohort) == 1


def test_claim_key_exact_meets_floor_uses_claim_key(tmp_path):
    rows = [fe_row(opn=f"P{i}", value=str(i)) for i in range(12)]
    lines = [json.dumps(r, sort_keys=True) for r in rows]
    cohort, meta = spp.build_cohort(lines, lines,
                                    spp.export_identity_opns(lines), 10)
    assert meta["selection"] == "claim_key_exact"
    assert meta["shortfall"] is False
    assert all(sel == "claim_key_exact" for _, _, sel in cohort)


def test_export_doc_shas():
    lines = [json.dumps(fe_row(doc_sha256="a" * 64), sort_keys=True),
             json.dumps(fe_row(doc_sha256="b" * 64), sort_keys=True)]
    assert spp.export_doc_shas(lines) == {"a" * 64, "b" * 64}


# ---------------------------------------------------------------------------
# deployments loader
# ---------------------------------------------------------------------------
def test_deployments_empty_list_is_unknown(tmp_path):
    p = tmp_path / "dep.json"
    p.write_text("[]")
    recs, sha = spp.load_deployments(p)
    assert recs == [] and sha is None


def test_deployments_missing_file_is_unknown(tmp_path):
    recs, sha = spp.load_deployments(tmp_path / "nope.json")
    assert recs == [] and sha is None


def test_deployments_picks_newest_sha(tmp_path):
    p = tmp_path / "dep.json"
    p.write_text(json.dumps([
        {"id": 2, "sha": "newest", "ref": "main"},
        {"id": 1, "sha": "older", "ref": "main"},
    ]))
    recs, sha = spp.load_deployments(p)
    assert sha == "newest"


# ---------------------------------------------------------------------------
# two-run reproducibility (identical except observation metadata)
# ---------------------------------------------------------------------------
def test_two_run_reproducibility(tmp_path):
    exp = tmp_path / "export"
    export_rows = [fe_row(opn="P1", doc_sha256="1" * 64),
                   fe_row(opn="P2", doc_sha256="1" * 64)]
    write_export(exp, export_rows, cube_sha="cubesha",
                 parts=[{"part_number": "P1", "vendor": "Acme"},
                        {"part_number": "P2", "vendor": "Acme"}])
    bundle = tmp_path / "fe" / "compare_ready_spine.jsonl"
    write_bundle(bundle, [fe_row(opn="P1", doc_sha256="1" * 64),
                          fe_row(opn="P2", doc_sha256="1" * 64)])
    out1 = tmp_path / "o1"
    out2 = tmp_path / "o2"
    common = dict(export_root=exp, fe_bundle=bundle,
                  parts_catalog=exp / "parts_catalog.jsonl", cas_roots=[],
                  deployments_json=None, evidence_dir=None, fe_commit="fe",
                  fe_repo=None, min_cohort=10, resolve_probe="unknown",
                  approval_record=False)
    spp.run(out_dir=out1, now="2026-01-01T00:00:00+00:00", host="h1", **common)
    spp.run(out_dir=out2, now="2026-01-02T00:00:00+00:00", host="h2", **common)
    d1 = json.loads((out1 / "served_path_proof_v1.json").read_text())
    d2 = json.loads((out2 / "served_path_proof_v1.json").read_text())
    d1.pop("observation")
    d2.pop("observation")
    assert d1 == d2
    # disposition artifact is also deterministic except observation
    q1 = json.loads((out1 / "quote_failure_disposition_v1.json").read_text())
    q2 = json.loads((out2 / "quote_failure_disposition_v1.json").read_text())
    q1.pop("observation")
    q2.pop("observation")
    assert q1 == q2