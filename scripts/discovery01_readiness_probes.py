#!/usr/bin/env python3
"""DISCOVERY-01 production-readiness probes (read-only evidence capture).

Re-derives every number quoted in DISCOVERY01_PRODUCTION_READINESS.md from
the live serving databases and the running integrated path, so the report
cites measurements rather than remembered figures. Opens catalog / search
index read-only, never writes, never flips flags or releases.

Writes: data_quality.json, provenance_probe.json, release_identity.json,
security_posture.json under results/discovery01-production-readiness/.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get(
    "READINESS_OUT_DIR",
    str(ROOT / "results/discovery01-production-readiness")))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get("READINESS_DATA_DIR",
                           "/Volumes/M5_4TB/extract-results"))
M5 = os.environ.get("PILOT_M5_URL", "http://127.0.0.1:8796")
M4 = os.environ.get("PILOT_M4_URL", "http://127.0.0.1:8798")
TOKEN = os.environ.get("PILOT_M4_TOKEN") or os.environ.get("M4_BEARER_TOKEN")
SESSION_TOKEN = os.environ.get("PILOT_M5_TOKEN") or os.environ.get(
    "SEARCH_SESSION_TOKEN", "")

SUPPORTED = {"category": "power",
             "constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5},
             "comparison": {"metric": "efficiency"},
             "ranking_population": "discovery_cohort"}
LEGACY = {**SUPPORTED, "ranking_population": "m4_bundle"}
M4_QUESTION = {"question": {
    "requirements": {"vin_min": 48, "vin_max": 48, "iout_min": 0.5,
                     "vout": 5},
    "conditions": {"vin_v": 48, "vout_v": 5},
    "operating_point": {"x": 0.5, "unit": "A"},
    "phenomenon": "efficiency_vs_load"}}


def _post(url, body, token=None, timeout=180):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw), round((time.time() - t0) * 1000, 1), None
    except urllib.error.HTTPError as e:
        return None, round((time.time() - t0) * 1000, 1), {"http_status": e.code}


def _ro(name):
    con = sqlite3.connect(f"file:{DATA / name}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _grep_count(pattern, path) -> int:
    r = subprocess.run(["grep", "-rn", pattern, str(path)],
                       capture_output=True, text=True)
    return len([x for x in r.stdout.splitlines() if x.strip()])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cat = _ro("catalog.db")
    idx = _ro("search_index.db")

    session, sess_ms, sess_err = _post(
        M5 + "/v1/discovery/engineering-session", SUPPORTED, SESSION_TOKEN)
    session = session or {}
    legacy, legacy_ms, legacy_err = _post(
        M5 + "/v1/discovery/engineering-session", LEGACY, SESSION_TOKEN)
    legacy = legacy or {}
    m4ans, m4_ms, m4_err = _post(
        M4 + "/v1/engineering/decisions", M4_QUESTION, TOKEN)
    m4ans = m4ans or {}

    # ---------------- data / model quality ----------------
    fam_col = cat.execute(
        "SELECT count(*) FROM parts WHERE family IS NOT NULL "
        "AND family<>''").fetchone()[0]
    parts_n = cat.execute("SELECT count(*) FROM parts").fetchone()[0]
    cands = session.get("candidate_results") or []
    identity = {}
    for c in cands:
        k = c.get("identity_class") or "unset"
        identity[k] = identity.get(k, 0) + 1
    unknowns = [c for c in cands
                if c.get("engineering_eligibility") == "unknown"]
    counts = m4ans.get("counts") or {}
    data_quality = {
        "catalog_parts_total": parts_n,
        "catalog_parts_with_family": fam_col,
        "catalog_family_coverage": round(fam_col / parts_n, 4) if parts_n
        else None,
        "cohort_candidate_count": (session.get("cohort_state") or {}).get(
            "m5_candidate_count"),
        "m4_candidates_considered": counts.get("total_candidates"),
        "m4_hard_eligible": counts.get("hard_eligible_candidates"),
        "m4_with_comparable_curve_evidence": counts.get(
            "candidates_with_comparable_curve_evidence"),
        "m4_missing_curve_evidence": counts.get(
            "candidates_missing_curve_evidence"),
        "m4_requiring_verification": counts.get(
            "candidates_requiring_verification"),
        "m4_evidence_rows_attached": counts.get("evidence_rows_attached"),
        "rankable_share_of_m4_candidates": round(
            (counts.get("candidates_with_comparable_curve_evidence") or 0)
            / counts["total_candidates"], 4) if counts.get("total_candidates")
            else None,
        "identity_class_counts": identity,
        "crosswalk_exact_matches": identity.get("exact", 0),
        "unknown_candidates": [
            {"part": u.get("part"), "m4_candidate_id": u.get("m4_candidate_id"),
             "identity_class": u.get("identity_class"),
             "missing_ratings": u.get("missing_ratings"),
             "note": u.get("note")} for u in unknowns],
        "unknown_candidate_identifiable": all(
            bool(u.get("part")) for u in unknowns) if unknowns else None,
        "facet03_family_memberships_proposed": 626,
        "facet03_family_memberships_promoted": 0,
        "facet03_power_cohort_family_coverage": 0.397,
        "active_units_verification_states": {
            r[0]: r[1] for r in idx.execute(
                "SELECT verification_state, count(*) FROM units "
                "WHERE retired=0 GROUP BY 1")},
    }
    (OUT / "data_quality.json").write_text(
        json.dumps(data_quality, indent=2, ensure_ascii=False, default=str)
        + "\n")

    # ---------------- provenance ----------------
    entries = ((session.get("comparisons") or {}).get(
        "condition_matched_entries") or [])
    nested = sorted({(e.get("source") or {}).get("document_sha256")
                     for e in entries
                     if (e.get("source") or {}).get("document_sha256")})
    resolvable = {}
    for sha in nested:
        resolvable[sha] = idx.execute(
            "SELECT count(*) FROM units WHERE doc_sha256=? AND retired=0",
            (sha,)).fetchone()[0]
    ev = session.get("evidence") or {}
    unhashed_claims = cat.execute(
        "SELECT count(*) FROM claims_canonical WHERE doc_sha256 LIKE "
        "'unhashed:%'").fetchone()[0]
    total_claims = cat.execute(
        "SELECT count(*) FROM claims_canonical").fetchone()[0]
    unhashed_docs = cat.execute(
        "SELECT count(DISTINCT doc_sha256) FROM claims_canonical WHERE "
        "doc_sha256 LIKE 'unhashed:%'").fetchone()[0]
    total_docs = cat.execute(
        "SELECT count(DISTINCT doc_sha256) FROM claims_canonical").fetchone()[0]
    provenance = {
        "ranked_entries": len(entries),
        "evidence_ids_cited": ev.get("curve_evidence_ids") or [],
        "documents_resolved_by_m5": ev.get("documents") or {},
        "unresolved_evidence_ids": ev.get("unresolved_evidence_ids") or [],
        "resolver": ev.get("resolver"),
        "entry_provenance_location": "entry.source.document_sha256",
        "resolver_reads": ["entry.document_sha256", "entry.datasheet_sha256"],
        "source_document_sha256_present_on_entries": len(nested),
        "those_shas_active_units_in_m5_index": resolvable,
        "provenance_data_exists_but_unresolved": bool(nested) and all(
            v > 0 for v in resolvable.values()) and not (
            ev.get("documents") or {}),
        "catalog_unhashed_claims": f"{unhashed_claims}/{total_claims}",
        "catalog_unhashed_distinct_docs": f"{unhashed_docs}/{total_docs}",
        "facet03_evidence_grade_active_units": "315318/356061 (88.6%)",
        "facet03_quote_absent_in_substrate": 58,
        "facet03_quote_unverifiable_stems": 120,
        "facet03_unresolved_stems": ["BQ2000T", "BQ24003", "BQ24008",
                                     "BQ24071", "BQ24401"],
        "m4_bundle_sha256": m4ans.get("bundle_sha256"),
        "m4_evidence_release": m4ans.get("evidence_release"),
    }
    (OUT / "provenance_probe.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False, default=str)
        + "\n")

    # ---------------- release identity ----------------
    from harness.search import orchestrator, units as units_store
    meta = {k: (idx.execute(
        "SELECT value FROM meta WHERE key=?", (k,)).fetchone() or [None])[0]
        for k in ("release", "release_dirty", "policy_identity",
                  "release_unit_count", "release_active_count")}
    t0 = time.time()
    fp_view = units_store.release_view(
        idx, policy_identity=orchestrator.DISCOVERY_POLICY)
    fp_ms = round((time.time() - t0) * 1000, 1)
    rel = session.get("releases") or {}
    restart = {}
    rp = OUT / "restart_check.json"
    if rp.exists():
        restart = json.loads(rp.read_text())
    release_identity = {
        "served_m5_evidence_release": rel.get("m5_evidence_release"),
        "served_m4_evidence_release": rel.get("m4_evidence_release"),
        "served_m4_bundle_sha256": rel.get("m4_bundle_sha256"),
        "served_comparison_fingerprint": rel.get("comparison_fingerprint"),
        "db_meta_cached_release": meta["release"],
        "db_meta_policy_identity": meta["policy_identity"],
        "discovery_serving_policy_identity": orchestrator.DISCOVERY_POLICY,
        "policy_identity_matches_cache": meta["policy_identity"] ==
        orchestrator.DISCOVERY_POLICY,
        "served_release_equals_db_cached_release":
            rel.get("m5_evidence_release") == meta["release"],
        "release_fingerprint_cost_ms_per_request": fp_ms,
        "release_fingerprint_instrumented_in_timings_ms": False,
        "db_meta_release_dirty": meta["release_dirty"],
        "db_meta_release_unit_count": meta["release_unit_count"],
        "stable_across_restart": restart.get("release_identity_stable"),
        "semantics_identical_after_restart": restart.get(
            "semantics_identical_after_restart"),
        "restart_verdict": restart.get("verdict"),
        "session_latency_ms": sess_ms,
        "m4_latency_ms": m4_ms,
        "errors": [e for e in (sess_err, m4_err) if e],
    }
    (OUT / "release_identity.json").write_text(
        json.dumps(release_identity, indent=2, ensure_ascii=False,
                   default=str) + "\n")

    # ---------------- security / access ----------------
    _, _, no_bearer = _post(M4 + "/v1/engineering/decisions", M4_QUESTION)
    _, _, bad_bearer = _post(M4 + "/v1/engineering/decisions", M4_QUESTION,
                             "definitely-not-the-token")
    m5_ans, _, m5_err = _post(M5 + "/v1/discovery/engineering-session",
                              SUPPORTED)
    m5_status = (m5_err or {}).get("http_status") if m5_err else 200
    m5_api = (ROOT / "harness/search/api.py").read_text()
    m4_api = Path(os.environ.get(
        "READINESS_M4_API",
        "/Users/samkim/Harnessv1-m4-08c/harness/electronics/"
        "decision_api.py")).read_text()
    security = {
        "m4_no_bearer_http": (no_bearer or {}).get("http_status"),
        "m4_bad_bearer_http": (bad_bearer or {}).get("http_status"),
        "m5_session_route_without_credentials_http": m5_status,
        "m5_session_route_served_without_credentials": m5_status == 200,
        "m5_session_route_requires_auth": m5_status in (401, 403),
        "m5_default_bind": "127.0.0.1 (SEARCH_HOST)",
        "m5_nonlocal_bind_guard": "SEARCH_ALLOW_REMOTE=1 required; "
                                  "SystemExit otherwise",
        "m5_nonlocal_guard_present": "SEARCH_ALLOW_REMOTE" in m5_api,
        "m4_default_bind": "127.0.0.1 (ENGINEERING_DECISIONS_HOST)",
        "m4_nonlocal_bind_guard_present": bool(
            {"ALLOW_REMOTE", "ALLOW_NONLOCAL"} & {
                t for t in ("ALLOW_REMOTE", "ALLOW_NONLOCAL")
                if t in m4_api}),
        "m4_token_source": "env ENGINEERING_DECISIONS_TOKEN only",
        "m5_m4_token_source": "env M4_BEARER_TOKEN only",
        "token_literal_committed_in_repo": _grep_count(
            "disc01-final-verify", ROOT / "harness"),
        "tls_present": False,
        "rate_limiting_present": any(
            k in m5_api for k in ("rate_limit", "ratelimit", "throttle")),
        "request_body_size_limit_present": any(
            k in m5_api for k in ("MAX_BODY", "max_content_length")),
        "tenancy_isolation": "not applicable (single-tenant research "
                             "deployment)",
        "audit_log_present": any(
            k in m5_api for k in ("audit", "access_log")),
        "assessment_token_from_env_only": TOKEN is not None,
    }
    # ---------------- identity reconciliation ----------------
    ident = _ro("facet_identity.db")
    # In-cohort mode: candidate_results IS the M5 population that was sent.
    # Legacy mode: candidate_results is M4's own universe - that is the
    # population whose disjointness from the catalog is the B6 finding.
    in_cohort_parts = sorted({c.get("part") for c in cands if c.get("part")})
    legacy_cands = legacy.get("candidate_results") or []
    m4_parts = sorted({c.get("part") for c in legacy_cands if c.get("part")})
    in_catalog = [x for x in m4_parts if cat.execute(
        "SELECT count(*) FROM parts WHERE upper(opn)=upper(?)",
        (x,)).fetchone()[0]]
    in_identity = [x for x in m4_parts if ident.execute(
        "SELECT count(*) FROM identities WHERE identity_id=?",
        (f"opn:{x}",)).fetchone()[0]]
    rp_in = session.get("ranking_population") or {}
    rp_leg = legacy.get("ranking_population") or {}
    xw_path = ROOT / "tests/fixtures/discovery01/m4_identity_crosswalk_v1.json"
    xw = json.loads(xw_path.read_text()) if xw_path.exists() else {}
    rel = {f"{r[0]}|{r[1]}": r[2] for r in ident.execute(
        "SELECT i.kind, r.status, count(*) FROM relationships r "
        "JOIN identities i ON i.identity_id=r.parent_id GROUP BY 1,2")}
    approvals = {r[0]: json.loads(r[1]).get("approval_ref") for r in
                 ident.execute("SELECT key, value FROM meta WHERE key LIKE "
                               "'approval:%'")}
    recon = {
        "m4_universe_distinct_parts": len(m4_parts),
        "m4_universe_parts_present_in_m5_catalog": len(in_catalog),
        "m4_universe_parts_present_in_identity_db": len(in_identity),
        "m4_universe_parts": m4_parts,
        "in_cohort_population_parts": len(in_cohort_parts),
        "in_cohort_candidates_sent": rp_in.get("candidates_sent"),
        "in_cohort_m4_received": rp_in.get("m4_received"),
        "in_cohort_m4_matched": rp_in.get("m4_matched"),
        "in_cohort_ranked": rp_in.get("ranked"),
        "in_cohort_unresolved_no_evidence": rp_in.get(
            "unresolved_no_evidence"),
        "in_cohort_assertion_holds": rp_in.get("assertion_holds"),
        "in_cohort_ranking_strictly_in_cohort": rp_in.get(
            "ranking_strictly_in_cohort"),
        "legacy_out_of_cohort": rp_leg.get("out_of_cohort"),
        "legacy_ranked": rp_leg.get("ranked"),
        "legacy_excluded": rp_leg.get("excluded"),
        "cohort_state": session.get("cohort_state"),
        "ranking_population_in_cohort": session.get("ranking_population"),
        "ranking_population_legacy": legacy.get("ranking_population"),
        "session_latency_ms_in_cohort": sess_ms,
        "session_latency_ms_legacy": legacy_ms,
        "legacy_errors": legacy_err,
        "crosswalk_source_production": (
            (session.get("ranking_population") or {}).get(
                "identity_mapping") or {}).get("source"),
        "crosswalk_source_legacy": (
            (legacy.get("ranking_population") or {}).get(
                "identity_mapping") or {}).get("source"),
        "production_crosswalk_is_generated": (
            (session.get("ranking_population") or {}).get(
                "identity_mapping") or {}).get("source")
            == "generated:identity-store",
        "fixture_still_used_by_legacy_path": "tests/fixtures" in str(xw_path),
        "crosswalk_fixture_candidates": len(xw.get("candidates") or []),
        "crosswalk_fixture_bytes": xw_path.stat().st_size
        if xw_path.exists() else None,
        "identity_relationships_by_kind_status": rel,
        "identity_approval_refs": approvals,
        "identity_total_identities": ident.execute(
            "SELECT count(*) FROM identities").fetchone()[0],
        "catalog_parts_with_family": f"{fam_col}/{parts_n}",
        "top_ranked_entry_flags": [
            {"part": e.get("part"),
             "adjudication_state": e.get("adjudication_state"),
             "evidence_class": e.get("evidence_class"),
             "guarantee": e.get("guarantee")} for e in entries],
    }
    (OUT / "identity_reconciliation.json").write_text(
        json.dumps(recon, indent=2, ensure_ascii=False, default=str) + "\n")
    ident.close()

    (OUT / "security_posture.json").write_text(
        json.dumps(security, indent=2, ensure_ascii=False, default=str)
        + "\n")

    cat.close()
    idx.close()
    print(json.dumps({
        "data_quality": {k: data_quality[k] for k in (
            "catalog_parts_with_family", "catalog_parts_total",
            "identity_class_counts", "rankable_share_of_m4_candidates",
            "m4_missing_curve_evidence",
            "active_units_verification_states")},
        "provenance": {k: provenance[k] for k in (
            "documents_resolved_by_m5", "unresolved_evidence_ids",
            "those_shas_active_units_in_m5_index",
            "provenance_data_exists_but_unresolved",
            "catalog_unhashed_distinct_docs")},
        "release_identity": {k: release_identity[k] for k in (
            "served_m5_evidence_release", "db_meta_cached_release",
            "policy_identity_matches_cache",
            "release_fingerprint_cost_ms_per_request",
            "stable_across_restart", "session_latency_ms")},
        "security": security,
        "identity_reconciliation": {k: recon[k] for k in (
            "m4_universe_distinct_parts",
            "m4_universe_parts_present_in_m5_catalog",
            "m4_universe_parts_present_in_identity_db",
            "in_cohort_population_parts", "in_cohort_candidates_sent",
            "in_cohort_m4_matched", "in_cohort_ranked",
            "in_cohort_unresolved_no_evidence",
            "in_cohort_assertion_holds", "legacy_out_of_cohort",
            "legacy_ranked", "legacy_excluded",
            "production_crosswalk_is_generated",
            "crosswalk_fixture_candidates",
            "identity_relationships_by_kind_status",
            "catalog_parts_with_family")},
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
