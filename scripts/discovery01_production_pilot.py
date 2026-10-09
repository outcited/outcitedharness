#!/usr/bin/env python3
"""DISCOVERY-01 production-readiness pilot (assessment only).

Drives real discovery requests through the INTEGRATED path (M5 session
endpoint -> M4 08C service over HTTP) and records, per scenario: request,
release ids, result, ranked candidates, exclusions, unknowns,
approximation status, interpolation status, provenance, latency,
errors/warnings, and a deterministic-replay comparison.

Nothing here mutates production state: the session cache is in-memory,
evidence bundles and the catalog/search/identity databases are opened
read-only, and the pilot never flips feature flags, releases, or
admission state. Each scenario uses distinct constraints so results
reflect live computation rather than a cache hit; P8b deliberately
re-issues an identical request to characterise cache-hit behaviour.

Modes:
  (default)        run scenarios P1-P8 and write the pre-restart anchor
  --post-restart   re-issue the anchor request and compare semantics
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/discovery01-production-readiness"
ANCHOR = OUT / "restart_anchor.json"

M5 = os.environ.get("PILOT_M5_URL", "http://127.0.0.1:8796")
M4 = os.environ.get("PILOT_M4_URL", "http://127.0.0.1:8798")
TOKEN = os.environ.get("PILOT_M4_TOKEN") or os.environ.get(
    "M4_BEARER_TOKEN", "")


def _post(url, body, token=None, timeout=120):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw), raw, round((time.time() - t0) * 1000, 1), None
    except urllib.error.HTTPError as e:
        return None, e.read(), round((time.time() - t0) * 1000, 1), \
            {"http_status": e.code}
    except Exception as e:  # noqa: BLE001 - transport failure is data here
        return None, b"", round((time.time() - t0) * 1000, 1), \
            {"transport_error": f"{type(e).__name__}: {e}"}


def _session(body, token=TOKEN):
    return _post(M5 + "/v1/discovery/engineering-session", body, token)


def _semantic(raw) -> str:
    """Replay hash over semantics only: wall-clock timings, cache and
    session metadata are excluded so determinism is measurable."""
    if not raw:
        return ""
    d = json.loads(raw)
    for key in ("timings_ms", "total_ms", "cache", "session"):
        d.pop(key, None)
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _record(name, body, ans, raw, latency, err, replay_raw=None):
    d = ans or {}
    comp = d.get("comparisons") or {}
    entries = comp.get("condition_matched_entries") or []
    approx = comp.get("approximate_scenario_entries")
    cands = d.get("candidate_results") or []
    ev = d.get("evidence") or {}
    rel = d.get("releases") or {}
    identity = {}
    for c in cands:
        k = c.get("identity_class") or "unset"
        identity[k] = identity.get(k, 0) + 1
    interp_ok = bool(entries) and all(
        (e.get("interpolation") or {}).get("supporting_points")
        for e in entries)
    return {
        "scenario": name,
        "request": body,
        "result": "ok" if d else ("error" if err else "empty"),
        "release_ids": {
            "m5_evidence_release": rel.get("m5_evidence_release"),
            "m5_identity_snapshot": rel.get("m5_identity_snapshot"),
            "m4_evidence_release": rel.get("m4_evidence_release"),
            "m4_bundle_sha256": rel.get("m4_bundle_sha256"),
            "m4_decision_contract": rel.get("m4_decision_contract"),
            "m4_transport": rel.get("m4_transport"),
            "comparison_fingerprint": rel.get("comparison_fingerprint"),
        },
        "comparison_level": comp.get("comparison_level"),
        "ranked_candidates": [
            {"part": e.get("part"), "value": e.get("value"),
             "evidence_id": e.get("evidence_id")} for e in entries],
        "exclusions": [
            {"part": c.get("part"), "identity_class": c.get("identity_class"),
             "rules": [(x.get("rule"), x.get("rated"), x.get("required"),
                        x.get("reason"))
                       for x in (c.get("explanation") or [])]}
            for c in cands
            if c.get("engineering_eligibility") == "ineligible"],
        "unknowns": [
            {"part": c.get("part"), "m4_candidate_id": c.get("m4_candidate_id"),
             "identity_class": c.get("identity_class"),
             "missing_ratings": c.get("missing_ratings"),
             "note": c.get("note")}
            for c in cands
            if c.get("engineering_eligibility") == "unknown"],
        "identity_class_counts": identity,
        "approximation_status": (
            "labeled_separate" if approx is not None else "absent"),
        "approximate_entries": len(approx or []),
        "interpolation_status": (
            "disclosed" if interp_ok else
            "not_applicable" if not entries else "missing"),
        "provenance": {
            "curve_evidence_ids": ev.get("curve_evidence_ids") or [],
            "documents_resolved": sorted((ev.get("documents") or {}).keys()),
            "unresolved_evidence_ids": ev.get("unresolved_evidence_ids") or [],
            "source_document_sha256": sorted({
                (e.get("source") or {}).get("document_sha256")
                for e in entries if (e.get("source") or {}).get(
                    "document_sha256")}),
            "evidence_policy": d.get("evidence_policy"),
        },
        "cohort_state": d.get("cohort_state"),
        "cache": d.get("cache"),
        "failures": d.get("failures"),
        "partial_result": d.get("partial_result"),
        "timings_ms": d.get("timings_ms"),
        "total_ms": d.get("total_ms"),
        "latency_ms": latency,
        "errors_warnings": err or [],
        "semantic_sha256": _semantic(raw),
        "replay_semantic_sha256": _semantic(replay_raw) if replay_raw else None,
        "deterministic_replay": (
            _semantic(raw) == _semantic(replay_raw)) if replay_raw else None,
    }


def _journey(iout, **extra):
    j = {"category": "power",
         "constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": iout},
         "comparison": {"metric": "efficiency"}}
    j.update(extra)
    return j


def _run_scenarios():
    records = []

    j1 = _journey(0.5)
    a, raw, lat, err = _session(j1)
    _, raw2, _, _ = _session(j1)          # immediate replay
    records.append(_record("P1_normal_supported_ranking", j1, a, raw, lat,
                           err, raw2))

    j2 = _journey(3.0)
    a, raw, lat, err = _session(j2)
    records.append(_record("P2_hard_exclusion", j2, a, raw, lat, err))

    j3 = _journey(0.75)
    a, raw, lat, err = _session(j3)
    records.append(_record("P3_unknown_retention", j3, a, raw, lat, err))

    j4 = _journey(0.5, comparison={"metric": "efficiency",
                                   "approximate": True})
    a, raw, lat, err = _session(j4)
    records.append(_record("P4_approximate_scenario", j4, a, raw, lat, err))

    j5 = _journey(0.3)
    a, raw, lat, err = _session(j5)
    records.append(_record("P5_interpolation", j5, a, raw, lat, err))

    j6 = {"category": "power",
          "constraints": {"vin_v": 24.0, "vout_v": 3.3, "iout_a": 1.0},
          "comparison": {"metric": "efficiency"}}
    a, raw, lat, err = _session(j6)
    records.append(_record("P6_no_defensible_comparison", j6, a, raw, lat,
                           err))

    _, raw401, lat401, err401 = _post(
        M4 + "/v1/engineering/decisions", {"question": {}}, token=None)
    _, raw403, lat403, err403 = _post(
        M4 + "/v1/engineering/decisions", {"question": {}}, token="wrong")
    records.append({
        "scenario": "P7_auth_failure",
        "request": {"direct_m4_without_bearer": True,
                    "direct_m4_with_bad_bearer": True},
        "result": "ok",
        "auth_contract": {"no_bearer_http": (err401 or {}).get("http_status"),
                          "bad_bearer_http": (err403 or {}).get("http_status"),
                          "m5_session_route_auth": "none (localhost-only)"},
        "latency_ms": round(lat401 + lat403, 1),
        "errors_warnings": [e for e in (err401, err403) if e],
    })

    stale = {"m4_evidence_release": "curve-evidence-bundle-v2-3b5658b629cc"}
    j8 = _journey(0.55, expected_releases=stale)
    a, raw, lat, err = _session(j8)
    records.append(_record("P8a_stale_release_cache_miss", j8, a, raw, lat,
                           err))
    a, raw, lat, err = _session(j8)       # identical request -> cache hit
    rec = _record("P8b_stale_release_cache_hit", j8, a, raw, lat, err)
    rec["note"] = ("re-issue of P8a: characterises whether release pinning "
                   "survives a session-cache hit")
    records.append(rec)

    # P8c: sequence-dependent cache-hit probe. P1 (unpinned) populated the
    # session cache for these exact constraints; re-issuing the SAME
    # constraints with a stale release pin characterises whether pinning
    # survives a cache hit. Orchestrator caches only failure-free results
    # (orchestrator.py:406) but the cache key omits expected_releases
    # (orchestrator.py:259).
    j8c = _journey(0.5, expected_releases=stale)
    a, raw, lat, err = _session(j8c)
    rec = _record("P8c_stale_pin_after_unpinned_cache", j8c, a, raw, lat, err)
    rec["note"] = (
        "depends on P1 having cached the unpinned result for identical "
        "constraints; cache hit with empty failures means the stale pin "
        "was silently ignored")
    rec["defect_detected"] = bool(
        (rec.get("cache") or {}).get("hit") and not rec.get("failures"))
    records.append(rec)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pilot_records.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False, default=str) + "\n")
    ANCHOR.write_text(json.dumps({
        "request": j1, "semantic_sha256": records[0]["semantic_sha256"],
        "release_ids": records[0]["release_ids"],
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2) + "\n")
    return records


def _post_restart():
    anchor = json.loads(ANCHOR.read_text())
    a, raw, lat, err = _session(anchor["request"])
    rec = _record("P9_service_restart", anchor["request"], a, raw, lat, err)
    same = rec["semantic_sha256"] == anchor["semantic_sha256"]
    out = {
        "scenario": "P9_service_restart",
        "pre_restart_semantic_sha256": anchor["semantic_sha256"],
        "post_restart_semantic_sha256": rec["semantic_sha256"],
        "release_identity_stable": (
            rec["release_ids"] == anchor["release_ids"]),
        "release_ids_post_restart": rec["release_ids"],
        "semantics_identical_after_restart": same,
        "ranked_candidates_post_restart": rec["ranked_candidates"],
        "latency_ms": lat,
        "errors_warnings": err or [],
        "verdict": "PASS" if (same and
                              rec["release_ids"] == anchor["release_ids"])
        else "FAIL",
    }
    (OUT / "restart_check.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n")
    return out


def _perf():
    """Latency profile: one cold (cache-miss) request, then repeats that
    hit the in-memory session cache, with the orchestrator's own timing
    breakdown. Read-only; no state mutation."""
    body = _journey(1.25)
    samples = []
    for i in range(6):
        a, raw, lat, err = _session(body)
        d = a or {}
        samples.append({
            "request_index": i,
            "latency_ms": lat,
            "cache_hit": (d.get("cache") or {}).get("hit"),
            "timings_ms": d.get("timings_ms"),
            "total_ms": d.get("total_ms"),
            "errors_warnings": err or [],
        })
    miss = [s for s in samples if not s["cache_hit"]]
    hit = [s for s in samples if s["cache_hit"]]

    def _stats(xs):
        v = sorted(x["latency_ms"] for x in xs)
        if not v:
            return None
        return {"n": len(v), "min_ms": v[0], "p50_ms": v[len(v) // 2],
                "max_ms": v[-1]}

    out = {
        "request": body,
        "cache_miss": _stats(miss),
        "cache_hit": _stats(hit),
        "samples": samples,
        "cohort_build_ms_on_miss": (
            miss[0]["timings_ms"] or {}).get("cohort_build_ms") if miss
            else None,
        "m4_decision_ms_on_miss": (
            miss[0]["timings_ms"] or {}).get("m4_decision_ms") if miss
            else None,
        "cohort_candidate_count": None,
    }
    a, _, _, _ = _session(_journey(1.5))
    out["cohort_candidate_count"] = ((a or {}).get("cohort_state") or {}).get(
        "m5_candidate_count")
    (OUT / "performance_observations.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n")
    return out


def main() -> int:
    if "--perf" in sys.argv:
        print(json.dumps(_perf(), indent=1))
        return 0
    if "--post-restart" in sys.argv:
        print(json.dumps(_post_restart(), indent=1))
        return 0
    recs = _run_scenarios()
    summary = {
        "scenarios": [r["scenario"] for r in recs],
        "latencies_ms": {r["scenario"]: r.get("latency_ms") for r in recs},
        "deterministic_replay": {
            r["scenario"]: r.get("deterministic_replay") for r in recs
            if "deterministic_replay" in r},
        "P1_ranked": recs[0]["ranked_candidates"],
        "P1_identity_classes": recs[0]["identity_class_counts"],
        "P1_provenance_unresolved": recs[0]["provenance"][
            "unresolved_evidence_ids"],
        "P1_documents_resolved": recs[0]["provenance"]["documents_resolved"],
        "P2_excluded": [e["part"] for e in recs[1]["exclusions"]],
        "P3_unknowns": recs[2]["unknowns"],
        "P4_approx_entries": recs[3]["approximate_entries"],
        "P5_interpolation": recs[4]["interpolation_status"],
        "P6_ranked_empty": recs[5]["ranked_candidates"] == [],
        "P7_auth": recs[6]["auth_contract"],
        "P8a_failures": recs[7]["failures"],
        "P8a_partial": recs[7]["partial_result"],
        "P8b_failures": recs[8]["failures"],
        "P8b_partial": recs[8]["partial_result"],
        "P8b_cache": recs[8]["cache"],
        "P8c_cache": recs[9]["cache"],
        "P8c_failures": recs[9]["failures"],
        "P8c_partial": recs[9]["partial_result"],
        "P8c_defect_detected": recs[9]["defect_detected"],
        "timings_breakdown_P1": recs[0].get("timings_ms"),
        "total_ms_by_scenario": {r["scenario"]: r.get("total_ms")
                                 for r in recs},
    }
    (OUT / "pilot_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
