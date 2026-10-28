"""DW-TRUTH-FUNNEL-02 — served-path proof (issue #5 phase 2).

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
                 cube_sha="cubesha", parts=None):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "compare_ready.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    (root / "fold_manifest.json").write_text(json.dumps(
        {"fold": fold_fold, "cube": {"sha256": cube_sha}}))
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
# 3. fold / cube identity mismatch fails
# ---------------------------------------------------------------------------
def test_fold_cube_mismatch_fails():
    assert spp.classify_fold(True, True, True) == spp.VERIFIED
    assert spp.classify_fold(True, False, True) == spp.FAILED
    assert spp.classify_fold(True, True, False) == spp.FAILED
    assert spp.classify_fold(False, True, True) == "ABSENT"


def test_fold_cube_mismatch_flagged(tmp_path):
    row = fe_row()
    ctx = base_ctx(tmp_path, cube_sha="DIFFERENT-CUBE")
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "doc_sha_overlap", ctx)
    assert rec["fold"]["class"] == spp.FAILED
    assert rec["fold"]["export_cube_sha256"] == "DIFFERENT-CUBE"


# ---------------------------------------------------------------------------
# 4. committed-but-undeployed is NOT deployed
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
    # evidence dir has a DIFFERENT page for this doc -> not bound to this claim
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
    # a manifest alone must NOT authorize
    row = fe_row()
    ctx = {"catalog": {}, "export_opns": {}, "exp_shas": set(), "cas": [],
           "fold": {"fold": "f", "cube": {"sha256": "s"}}, "cube_sha": "s",
           "fe_exact": set(), "fe_by_evidence": set(), "deployed_sha": None,
           "fe_commit": None, "evidence": {}, "resolve_probe": "unknown",
           "approval_record": False}
    rec = spp.audit_claim(json.dumps(row, sort_keys=True), row, "byte_exact", ctx)
    assert rec["authorized"]["class"] == spp.UNKNOWN


# ---------------------------------------------------------------------------
# byte-exact intersection / cohort
# ---------------------------------------------------------------------------
def test_byte_exact_intersection_measured():
    a = ['{"x":1}', '{"x":2}']
    b = ['{"x":2}', '{"x":3}']
    assert spp.byte_exact_intersection(a, b) == ['{"x":2}']


def test_byte_exact_zero_triggers_documented_shortfall(tmp_path):
    exp_rows = [fe_row(opn="E1", doc_sha256="1" * 64)]
    fe_rows = [fe_row(opn="F1", doc_sha256="1" * 64)]  # same doc, different row
    er = [json.dumps(r, sort_keys=True) for r in exp_rows]
    fr = [json.dumps(r, sort_keys=True) for r in fe_rows]
    cohort, meta = spp.build_cohort(er, fr, spp.export_doc_shas(er), 10)
    assert meta["byte_exact"] == 0
    assert meta["shortfall"] is True
    assert meta["selection"] == "doc_sha_overlap"
    assert len(cohort) == 1


def test_byte_exact_meets_floor_uses_byte_exact(tmp_path):
    rows = [fe_row(opn=f"P{i}", value=str(i)) for i in range(12)]
    lines = [json.dumps(r, sort_keys=True) for r in rows]
    cohort, meta = spp.build_cohort(lines, lines, spp.export_doc_shas(lines), 10)
    assert meta["selection"] == "byte_exact"
    assert meta["shortfall"] is False
    assert all(sel == "byte_exact" for _, _, sel in cohort)


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
    export_rows = [fe_row(opn="P1", doc_sha256="1" * 64), fe_row(opn="P2", doc_sha256="1" * 64)]
    write_export(exp, export_rows, cube_sha="cubesha",
                 parts=[{"part_number": "P1", "vendor": "Acme"},
                        {"part_number": "P2", "vendor": "Acme"}])
    bundle = tmp_path / "fe" / "compare_ready_spine.jsonl"
    # same doc sha so the fallback cohort picks these two
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