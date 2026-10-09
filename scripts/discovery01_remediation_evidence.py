#!/usr/bin/env python3
"""Per-blocker live HTTP evidence for the DISCOVERY-01 remediation.

Each check reproduces the exact sequence the production-readiness
assessment recorded as failing, against the running integrated path
(M5 session endpoint -> M4 decisions service), and writes
results/discovery01-remediation/<check>.json.

Read-only with respect to production data: catalog/search index are
opened read-only by the service, the session cache is in-memory, and no
flag, release or admission state is modified.

Usage:
  scripts/discovery01_remediation_evidence.py b3 [b2|b4|b5|b6 ...]
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/discovery01-remediation"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

M5 = os.environ.get("PILOT_M5_URL", "http://127.0.0.1:8796")
M4 = os.environ.get("PILOT_M4_URL", "http://127.0.0.1:8798")
TOKEN = os.environ.get("PILOT_M4_TOKEN") or os.environ.get(
    "M4_BEARER_TOKEN", "")
SESSION_TOKEN = os.environ.get("PILOT_M5_TOKEN", "")

LIVE_RELEASE = "curve-evidence-bundle-v3-5875789dcf3f"
STALE_RELEASE = "curve-evidence-bundle-v2-3b5658b629cc"


def post(url, body, token=None, timeout=180):
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
        raw = e.read()
        try:
            return json.loads(raw), raw, round((time.time() - t0) * 1000, 1), \
                {"http_status": e.code}
        except Exception:  # noqa: BLE001
            return None, raw, round((time.time() - t0) * 1000, 1), \
                {"http_status": e.code}


def session(body, token=None):
    return post(M5 + "/v1/discovery/engineering-session", body,
                token if token is not None else (SESSION_TOKEN or None))


def semantic(raw) -> str:
    if not raw:
        return ""
    d = json.loads(raw)
    for k in ("timings_ms", "total_ms", "cache", "session"):
        d.pop(k, None)
    return hashlib.sha256(json.dumps(d, sort_keys=True,
                                     default=str).encode()).hexdigest()


def journey(iout, **extra):
    j = {"category": "power",
         "constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": iout},
         "comparison": {"metric": "efficiency"}}
    j.update(extra)
    return j


def write(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    print(f"wrote results/discovery01-remediation/{name}.json")


# --- B3: release pin cannot be bypassed by a warm cache --------------------

def check_b3():
    j = journey(0.5)
    unpinned, raw_u, lat_u, err_u = session(j)
    stale = {**j, "expected_releases": {"m4_evidence_release": STALE_RELEASE}}
    pinned, raw_p, lat_p, err_p = session(stale)
    valid = {**j, "expected_releases": {"m4_evidence_release": LIVE_RELEASE}}
    v1, _, _, _ = session(valid)
    v2, _, _, _ = session(valid)
    mismatch = [f for f in (pinned or {}).get("failures") or []
                if f.get("error") == "release_mismatch"]
    out = {
        "blocker": "B3",
        "check": "stale release pin after an unpinned request warmed the "
                 "session cache",
        "sequence": ["unpinned", "stale-pinned (identical constraints)",
                     "valid-pinned", "valid-pinned (repeat)"],
        "before": {
            "source": "results/discovery01-production-readiness/"
                      "pilot_records.json (scenario "
                      "P8c_stale_pin_after_unpinned_cache)",
            "cache_hit": True, "failures": [], "partial_result": False,
            "defect": "release validation bypassed by cache reuse"},
        "after": {
            "unpinned": {"cache_hit": (unpinned or {}).get(
                "cache", {}).get("hit"), "failures": (unpinned or {}).get(
                    "failures"), "partial_result": (unpinned or {}).get(
                        "partial_result"), "latency_ms": lat_u},
            "stale_pinned": {
                "cache_hit": (pinned or {}).get("cache", {}).get("hit"),
                "failures": (pinned or {}).get("failures"),
                "partial_result": (pinned or {}).get("partial_result"),
                "release_mismatch_reported": bool(mismatch),
                "expected": mismatch[0]["expected"] if mismatch else None,
                "actual": mismatch[0]["actual"] if mismatch else None,
                "cohort_preserved": ((pinned or {}).get("cohort_state")
                                     or {}).get("m5_candidate_count"),
                "latency_ms": lat_p},
            "valid_pinned_first": {"cache_hit": (v1 or {}).get(
                "cache", {}).get("hit"), "failures": (v1 or {}).get(
                    "failures")},
            "valid_pinned_repeat": {"cache_hit": (v2 or {}).get(
                "cache", {}).get("hit"), "failures": (v2 or {}).get(
                    "failures")},
        },
        "errors": [e for e in (err_u, err_p) if e],
        "pass": bool(
            (pinned or {}).get("cache", {}).get("hit") is False
            and mismatch and (pinned or {}).get("partial_result") is True
            and (v2 or {}).get("cache", {}).get("hit") is True
            and not (v2 or {}).get("failures")),
    }
    write("b3_release_pin_cache", out)
    return out


# --- B2: provenance resolution ---------------------------------------------

def check_b2():
    """Provenance resolution on both populations.

    Ranked entries with curve evidence only exist where M4 holds evidence,
    i.e. the transitional M4-universe population (labeled out_of_cohort).
    The production in-cohort population currently ranks nothing (M4 has no
    curve evidence for any catalog part), so the honest assertion there is
    that nothing is cited and nothing is fabricated.
    """
    out = {"blocker": "B2",
           "check": "M4 nested source provenance resolves through the M5 "
                    "index",
           "before": {"documents_resolved": 0, "unresolved_evidence_ids": 4,
                      "cited_evidence_ids": 4,
                      "source": "results/discovery01-production-readiness/"
                                "provenance_probe.json"},
           "after": {}}

    for label, population in (("m4_universe_labeled_out_of_cohort",
                               "m4_bundle"),
                              ("discovery_cohort_in_cohort",
                               "discovery_cohort")):
        req = journey(0.5, ranking_population=population)             if population != "m4_bundle" else             {**journey(0.5), "ranking_population": population}
        ans, raw, lat, err = post(
            M5 + "/v1/discovery/engineering-session", req, SESSION_TOKEN)
        d = ans or {}
        ev = d.get("evidence") or {}
        entries = ((d.get("comparisons") or {}).get(
            "condition_matched_entries") or [])
        cited = ev.get("curve_evidence_ids") or []
        docs = ev.get("documents") or {}
        unresolved = ev.get("unresolved_evidence_ids") or []
        entry_shas = sorted({(e.get("source") or {}).get("document_sha256")
                             for e in entries
                             if (e.get("source") or {}).get(
                                 "document_sha256")})
        out["after"][label] = {
            "request": req,
            "cited_evidence_ids": cited,
            "documents_resolved": sorted(docs.keys()),
            "documents_resolved_count": len(docs),
            "unresolved_evidence_ids": unresolved,
            "entry_source_sha256": entry_shas,
            "every_cited_id_resolved": (bool(cited) and not unresolved)
            if cited else None,
            "resolution_summary": ev.get("resolution"),
            "provenance_conflicts": ev.get("provenance_conflicts"),
            "precedence": ev.get("precedence"),
            "per_evidence_id_resolution": {
                eid: {k: v.get(k) for k in (
                    "resolved", "reason", "resolution_path",
                    "document_sha256", "sha_path", "page_1based",
                    "figure_index", "series_index", "caption",
                    "source_artifact", "indexed_units", "index_pages",
                    "evidence_grades", "verification_states",
                    "m4_page_present_in_index", "parts")}
                for eid, v in (ev.get("evidence_ids") or {}).items()},
            "documents_view": docs,
            "ranked_entries": len(entries),
            "latency_ms": lat,
            "semantic_sha256": semantic(raw),
            "errors_warnings": err or [],
        }

    leg = out["after"]["m4_universe_labeled_out_of_cohort"]
    inc = out["after"]["discovery_cohort_in_cohort"]
    out["pass"] = bool(
        leg["cited_evidence_ids"]
        and not leg["unresolved_evidence_ids"]
        and leg["documents_resolved_count"] >= len(leg["entry_source_sha256"])
        and all(v["resolved"] for v in
                leg["per_evidence_id_resolution"].values())
        and not leg["provenance_conflicts"]
        and inc["cited_evidence_ids"] == []
        and inc["unresolved_evidence_ids"] == []
        and inc["documents_resolved_count"] == 0)
    write("b2_provenance_resolution", out)
    return out


# --- B4: auth fails closed on both hops ------------------------------------

M4_REPO = Path(os.environ.get(
    "READINESS_M4_REPO", "/Users/samkim/Harnessv1-m4-08c"))


def _spawn(env, module, cwd, log):
    proc = subprocess.Popen(
        [str(Path(cwd) / ".venv/bin/python"), "-u", "-m", module],
        cwd=str(cwd), env={**os.environ, **env},
        stdout=open(log, "wb"), stderr=subprocess.STDOUT)
    return proc


def _wait_http(url, want=200, tries=40):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == want:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == want:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def _refusal(env, module, cwd):
    """Start a service that must REFUSE to come up; capture the message."""
    proc = subprocess.run(
        [str(Path(cwd) / ".venv/bin/python"), "-m", module],
        cwd=str(cwd), env={**os.environ, **env},
        capture_output=True, text=True, timeout=60)
    return {"returncode": proc.returncode,
            "message": (proc.stderr or proc.stdout).strip()[-300:]}


def _http_code(url, body=None, token=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


def check_b4():
    out = {"blocker": "B4", "check": "auth fails closed on both hops",
           "before": {
               "m5_session_without_credentials": 200,
               "m4_auth_when_token_unset": "authorized (fail open)",
               "m4_nonlocal_bind_guard": False,
               "m4_constant_time_compare": False,
               "source": "results/discovery01-production-readiness/"
                         "security_posture.json"},
           "after": {}}
    a = out["after"]

    # --- M4 with a token: 401 / 403 / 200 ---
    a["m4_no_bearer"] = _http_code(M4 + "/v1/engineering/decisions",
                                   body={"question": {}})
    a["m4_wrong_bearer"] = _http_code(M4 + "/v1/engineering/decisions",
                                      body={"question": {}}, token="wrong")
    a["m4_correct_bearer"] = _http_code(
        M4 + "/v1/engineering/decisions",
        body={"question": {"requirements": {"vin_min": 48, "vin_max": 48,
                                            "iout_min": 0.5, "vout": 5},
                           "conditions": {"vin_v": 48, "vout_v": 5},
                           "operating_point": {"x": 0.5, "unit": "A"},
                           "phenomenon": "efficiency_vs_load"}},
        token=TOKEN)
    a["m4_evidence_route_no_bearer"] = _http_code(
        M4 + "/v1/engineering/evidence/curve-nonexistent")

    # --- M5 session route with a token: 401 / 403 / 200 ---
    a["m5_session_no_bearer"] = _http_code(
        M5 + "/v1/discovery/engineering-session",
        body={"category": "power"})
    a["m5_session_wrong_bearer"] = _http_code(
        M5 + "/v1/discovery/engineering-session",
        body={"category": "power"}, token="wrong")
    a["m5_session_correct_bearer"] = _http_code(
        M5 + "/v1/discovery/engineering-session",
        body=journey(0.5), token=SESSION_TOKEN)

    # --- M5 with no token configured: 503, never open ---
    proc = _spawn({"SEARCH_ENABLE_ENGINEERING_SESSION": "1",
                   "SEARCH_M4_URL": M4, "M4_BEARER_TOKEN": TOKEN,
                   "SEARCH_PORT": "8795", "SEARCH_HOST": "127.0.0.1"},
                  "harness.search.api", ROOT, "/tmp/m5_notoken.log")
    try:
        _wait_http("http://127.0.0.1:8795/v1/health")
        a["m5_no_token_session"] = _http_code(
            "http://127.0.0.1:8795/v1/discovery/engineering-session",
            body={"category": "power"})
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    # --- M4 loopback development posture still works (explicit opt-in) ---
    proc = _spawn({"ENGINEERING_DECISIONS_ENABLED": "1",
                   "ENGINEERING_DECISIONS_PORT": "8799",
                   "ENGINEERING_DECISIONS_HOST": "127.0.0.1",
                   "ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH": "1"},
                  "harness.electronics.decision_api", M4_REPO,
                  "/tmp/m4_devnoauth.log")
    try:
        _wait_http("http://127.0.0.1:8799/v1/engineering/health")
        a["m4_dev_noauth_health"] = _http_code(
            "http://127.0.0.1:8799/v1/engineering/health")
        a["m4_dev_noauth_decisions"] = _http_code(
            "http://127.0.0.1:8799/v1/engineering/decisions",
            body={"question": {}})
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    # --- M4 without a token and without the opt-in refuses to start ---
    a["m4_startup_refusal_no_token"] = _refusal(
        {"ENGINEERING_DECISIONS_ENABLED": "1",
         "ENGINEERING_DECISIONS_PORT": "8794",
         "ENGINEERING_DECISIONS_HOST": "127.0.0.1"},
        "harness.electronics.decision_api", M4_REPO)
    a["m4_startup_refusal_nonlocal_bind"] = _refusal(
        {"ENGINEERING_DECISIONS_ENABLED": "1",
         "ENGINEERING_DECISIONS_PORT": "8794",
         "ENGINEERING_DECISIONS_HOST": "0.0.0.0",
         "ENGINEERING_DECISIONS_TOKEN": TOKEN},
        "harness.electronics.decision_api", M4_REPO)
    a["m5_startup_refusal_nonlocal_no_token"] = _refusal(
        {"SEARCH_ENABLE_ENGINEERING_SESSION": "1", "SEARCH_HOST": "0.0.0.0",
         "SEARCH_ALLOW_REMOTE": "1", "SEARCH_PORT": "8795"},
        "harness.search.api", ROOT)

    # --- rollback still removes the route entirely (flag off) ---
    proc = _spawn({"SEARCH_SESSION_TOKEN": SESSION_TOKEN,
                   "SEARCH_M4_URL": M4, "M4_BEARER_TOKEN": TOKEN,
                   "SEARCH_PORT": "8794", "SEARCH_HOST": "127.0.0.1"},
                  "harness.search.api", ROOT, "/tmp/m5_flagoff_b4.log")
    try:
        time.sleep(6)
        a["m5_flag_off_is_404"] = _http_code(
            "http://127.0.0.1:8794/v1/discovery/engineering-session",
            body={"category": "power"}, token=SESSION_TOKEN)
        a["m5_flag_on_with_token_is_200"] = _http_code(
            M5 + "/v1/discovery/engineering-session",
            body=journey(0.5), token=SESSION_TOKEN)
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    a["m4_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=M4_REPO, capture_output=True,
        text=True).stdout.strip()
    a["m5_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
        text=True).stdout.strip()

    out["pass"] = bool(
        a["m4_no_bearer"] == 401 and a["m4_wrong_bearer"] == 403
        and a["m4_correct_bearer"] == 200
        and a["m4_evidence_route_no_bearer"] == 401
        and a["m5_session_no_bearer"] == 401
        and a["m5_session_wrong_bearer"] == 403
        and a["m5_session_correct_bearer"] == 200
        and a["m5_no_token_session"] == 503
        and a["m4_dev_noauth_health"] == 200
        and a["m4_dev_noauth_decisions"] not in (401, 403, 503)
        and a["m4_startup_refusal_no_token"]["returncode"] != 0
        and "ENGINEERING_DECISIONS_TOKEN" in
        a["m4_startup_refusal_no_token"]["message"]
        and a["m4_startup_refusal_nonlocal_bind"]["returncode"] != 0
        and "refusing to bind non-localhost" in
        a["m4_startup_refusal_nonlocal_bind"]["message"]
        and a["m5_startup_refusal_nonlocal_no_token"]["returncode"] != 0
        and "SEARCH_SESSION_TOKEN" in
        a["m5_startup_refusal_nonlocal_no_token"]["message"]
        and a["m5_flag_off_is_404"] == 404
        and a["m5_flag_on_with_token_is_200"] == 200)
    write("b4_auth_fail_closed", out)
    return out


# --- B5: release lookup cost and request latency ---------------------------

def check_b5():
    """Cold/warm/cache-hit latency plus the release-identity lookup cost,
    measured on a freshly spawned M5 process (so 'cold' is genuinely cold)
    against the already-running M4 service."""
    port = os.environ.get("PILOT_B5_PORT", "8801")
    base = f"http://127.0.0.1:{port}"
    env = {"SEARCH_ENABLE_ENGINEERING_SESSION": "1",
           "SEARCH_SESSION_TOKEN": SESSION_TOKEN,
           "SEARCH_M4_URL": M4, "M4_BEARER_TOKEN": TOKEN,
           "SEARCH_HOST": "127.0.0.1", "SEARCH_PORT": port}
    proc = _spawn(env, "harness.search.api", ROOT, "/tmp/m5_b5.log")
    samples = []
    try:
        for _ in range(60):
            if _http_code(base + "/v1/discovery/engineering-session",
                          body={"category": "power"}, token="wrong") == 403:
                break
            time.sleep(0.5)
        seq = [("cold_first_request", journey(0.5)),
               ("session_cache_hit", journey(0.5)),
               ("warm_process_new_constraints", journey(1.75)),
               ("warm_process_new_constraints_2", journey(2.25))]
        for label, body in seq:
            ans, raw, lat, err = session(body, token=SESSION_TOKEN)                 if False else post(base + "/v1/discovery/engineering-session",
                                   body, SESSION_TOKEN)
            d = ans or {}
            samples.append({
                "label": label,
                "latency_ms": lat,
                "total_ms": d.get("total_ms"),
                "timings_ms": d.get("timings_ms"),
                "session_cache_hit": (d.get("cache") or {}).get("hit"),
                "release_identity_cache": (d.get("cache") or {}).get(
                    "release_identity_cache"),
                "m5_release": (d.get("releases") or {}).get(
                    "m5_evidence_release"),
                "m4_release": (d.get("releases") or {}).get(
                    "m4_evidence_release"),
                "errors_warnings": err or [],
            })
        # deterministic replay across the primed path
        _, raw_a, _, _ = post(base + "/v1/discovery/engineering-session",
                              journey(0.5), SESSION_TOKEN)
        _, raw_b, _, _ = post(base + "/v1/discovery/engineering-session",
                              journey(3.25), SESSION_TOKEN)
        _, raw_b2, _, _ = post(base + "/v1/discovery/engineering-session",
                               journey(3.25), SESSION_TOKEN)
        replay_ok = semantic(raw_b) == semantic(raw_b2)
    finally:
        proc.terminate()
        proc.wait(timeout=20)

    priming = {}
    pf = OUT / "b5_release_priming.json"
    if pf.exists():
        rep = json.loads(pf.read_text())
        priming = {"verdict": rep.get("verdict"),
                   "before_lookup_cost_ms": rep["before"]["lookup_cost_ms"],
                   "after_lookup_cost_ms": rep["after"]["lookup_cost_ms"],
                   "prime_cost_ms": (rep.get("primed") or {}).get(
                       "prime_cost_ms"),
                   "invariants_held": rep.get("invariants_held"),
                   "releases_ledger_rows_added":
                       rep.get("releases_ledger_rows_added"),
                   "discovery_release": rep["after"]["discovery_release"]}

    cold = samples[0]["latency_ms"]
    hit = samples[1]["latency_ms"]
    warm = samples[2]["latency_ms"]
    rel_ms = [s["timings_ms"].get("release_identity_ms") for s in samples
              if s.get("timings_ms")]
    out = {
        "blocker": "B5",
        "check": "release identity lookup is O(1) and no longer dominates "
                 "request latency",
        "before": {
            "session_latency_ms_range": [2843.5, 3493.2],
            "release_fingerprint_cost_ms_per_request": [2814.3, 2929.6],
            "cache_hit_latency_ms_p50": 2897.3,
            "instrumented": False,
            "root_cause": "two full fingerprint passes per request "
                          "(index_release recompute plus the read-only "
                          "fallback recompute) because meta holds a single, "
                          "different policy identity",
            "source": "results/discovery01-production-readiness/"
                      "performance_observations.json"},
        "after": {
            "samples": samples,
            "cold_request_latency_ms": cold,
            "session_cache_hit_latency_ms": hit,
            "warm_process_new_constraints_latency_ms": warm,
            "release_identity_lookup_ms": rel_ms,
            "release_identity_lookup_max_ms": max(
                x for x in rel_ms if x is not None) if rel_ms else None,
            "deterministic_replay_after_priming": replay_ok,
            "priming": priming,
            "note": "release id is unchanged by priming; the served "
                    "discovery release is now recorded in the append-only "
                    "releases ledger",
        },
        "pass": bool(
            priming.get("verdict") == "PRIMED"
            and all(priming.get("invariants_held", {}).values())
            and max(x for x in rel_ms if x is not None) < 250
            and hit < 1000 and cold < 2000 and replay_ok),
    }
    write("b5_release_fingerprint_latency", out)
    return out


# --- B6: real candidate transport (8 required journeys) --------------------

B6_JOURNEYS = [
    ("J1_normal_supported_cohort",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5}}, {}),
    ("J2_facet_reduces_cohort",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5,
                      "family": "CoolSiC"}}, {}),
    ("J3_hard_exclusion_in_cohort",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 3.0}}, {}),
    ("J3b_hard_exclusion_legacy_labeled",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 3.0}},
     {"ranking_population": "m4_bundle"}),
    ("J4_unknown_heavy",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5,
                      "rds_on_ohm": "<5 mOhm"}}, {}),
    ("J5_unmapped_identity",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5,
                      "vendor": "rohm"}}, {}),
    ("J6_empty_candidate_set",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.5,
                      "vendor": "no-such-vendor"}}, {}),
    ("J7_stale_release_unpinned_warm",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.75,
                      "subcategory": "ac-dc"}}, {}),
    ("J7b_stale_release_pinned",
     {"constraints": {"vin_v": 48.0, "vout_v": 5.0, "iout_a": 0.75,
                      "subcategory": "ac-dc"},
      "expected_releases": {"m4_evidence_release": STALE_RELEASE}}, {}),
]

MAX_RECORDED_IDS = 600


def _b6_request(body_extra):
    req = {"category": "power", "comparison": {"metric": "efficiency"}}
    req.update(body_extra)
    return req


def _b6_record(label, req, ans, lat, err, replay_same):
    d = ans or {}
    rp = d.get("ranking_population") or {}
    rel = d.get("releases") or {}
    results = d.get("candidate_results") or []
    statuses = {}
    for c in results:
        k = c.get("engineering_eligibility") or "unset"
        statuses[k] = statuses.get(k, 0) + 1
    ids = [c.get("m5_canonical_id") for c in results]
    return {
        "journey": label,
        "request": req,
        "m5_candidate_count": rp.get("discovery_candidate_count"),
        "candidates_sent": rp.get("candidates_sent"),
        "candidate_ids": (ids[:MAX_RECORDED_IDS] if len(ids)
                          <= MAX_RECORDED_IDS else
                          (rp.get("candidates_sample") or [])[:25]),
        "candidate_ids_recorded": min(len(ids), MAX_RECORDED_IDS)
        if len(ids) <= MAX_RECORDED_IDS else len(
            (rp.get("candidates_sample") or [])[:25]),
        "candidate_ids_total": len(ids),
        "candidates_sha256": rp.get("candidates_sha256"),
        "m4_received": rp.get("m4_received"),
        "m4_matched": rp.get("m4_matched"),
        "identity_mapped": (rp.get("identity_mapping") or {}).get(
            "coverage", {}).get("mapped") if isinstance(
                (rp.get("identity_mapping") or {}).get("coverage"), dict)
        else None,
        "identity_coverage": (rp.get("identity_mapping") or {}).get(
            "coverage"),
        "identity_mapping_source": (rp.get("identity_mapping") or {}).get(
            "source"),
        "ranked": rp.get("ranked"),
        "excluded": rp.get("excluded"),
        "unknown_retained": rp.get("unknown_retained"),
        "unresolved_no_evidence": rp.get("unresolved_no_evidence"),
        "eligible_without_comparable_evidence": rp.get(
            "eligible_without_comparable_evidence"),
        "unmapped": rp.get("unmapped"),
        "eligibility_state_counts": rp.get("eligibility_state_counts"),
        "unknown_unplaced_included": rp.get("unknown_unplaced_included"),
        "population_accounting": rp.get("population_accounting"),
        "candidate_result_statuses": statuses,
        "disclosure": {
            "mode": rp.get("mode"), "in_cohort": rp.get("in_cohort"),
            "out_of_cohort": rp.get("out_of_cohort"),
            "ranking_strictly_in_cohort": rp.get(
                "ranking_strictly_in_cohort"),
            "m4_universe_substituted": rp.get("m4_universe_substituted"),
            "assertion": rp.get("assertion"),
            "assertion_holds": rp.get("assertion_holds"),
            "out_of_population_entries": rp.get("out_of_population_entries"),
            "promotion_blocked_while_normal_path": rp.get(
                "promotion_blocked_while_normal_path"),
            "disclosure_text": rp.get("disclosure"),
            "match_rule": rp.get("match_rule"),
        },
        "release_fingerprints": {
            "m5_evidence_release": rel.get("m5_evidence_release"),
            "m5_identity_snapshot": rel.get("m5_identity_snapshot"),
            "identity_crosswalk_sha256": rel.get(
                "identity_crosswalk_sha256"),
            "identity_crosswalk_source": rel.get("identity_crosswalk_source"),
            "crosswalk_fixture_sha256": rel.get("crosswalk_fixture_sha256"),
            "m4_evidence_release": rel.get("m4_evidence_release"),
            "m4_bundle_sha256": rel.get("m4_bundle_sha256"),
            "comparison_fingerprint": rel.get("comparison_fingerprint"),
        },
        "ranked_entries": [
            {"part": e.get("part"), "value": e.get("value"),
             "evidence_id": e.get("evidence_id")}
            for e in ((d.get("comparisons") or {}).get(
                "condition_matched_entries") or [])],
        "excluded_parts": [c.get("part") for c in results
                           if c.get("engineering_eligibility") ==
                           "ineligible"][:25],
        "latency_ms": lat,
        "timings_ms": d.get("timings_ms"),
        "failures": d.get("failures"),
        "partial_result": d.get("partial_result"),
        "deterministic_replay": replay_same,
        "errors_warnings": err or [],
    }


def check_b6():
    records = []
    for label, extra, mode in B6_JOURNEYS:
        req = _b6_request(extra)
        req.update(mode)
        ans, raw, lat, err = post(
            M5 + "/v1/discovery/engineering-session", req, SESSION_TOKEN)
        ans2, raw2, _, _ = post(
            M5 + "/v1/discovery/engineering-session", req, SESSION_TOKEN)
        records.append(_b6_record(label, req, ans, lat, err,
                                  semantic(raw) == semantic(raw2)))

    by = {r["journey"]: r for r in records}
    j1, j2 = by["J1_normal_supported_cohort"], by["J2_facet_reduces_cohort"]
    j3, j3b = by["J3_hard_exclusion_in_cohort"], \
        by["J3b_hard_exclusion_legacy_labeled"]
    j4 = by["J4_unknown_heavy"]
    j5 = by["J5_unmapped_identity"]
    j6 = by["J6_empty_candidate_set"]
    j7, j7b = by["J7_stale_release_unpinned_warm"], \
        by["J7b_stale_release_pinned"]

    checks = {
        "j1_transport_matches_disclosure": j1["candidates_sent"] ==
            j1["m4_received"] == j1["m5_candidate_count"],
        "j1_strictly_in_cohort": j1["disclosure"][
            "ranking_strictly_in_cohort"] is True,
        "j1_accounting_holds": j1["disclosure"]["assertion_holds"] is True,
        "j1_no_out_of_population_entries": j1["disclosure"][
            "out_of_population_entries"] == [],
        "j1_identity_generated_not_fixture": j1["identity_mapping_source"]
            == "generated:identity-store"
            and j1["release_fingerprints"]["crosswalk_fixture_sha256"] is None,
        "j1_every_candidate_accounted": sum(
            j1["candidate_result_statuses"].values()) ==
            j1["candidates_sent"],
        "j2_facet_reduces_population": 0 < j2["candidates_sent"] <
            j1["candidates_sent"],
        "j2_deterministic_payload_hash": bool(j2["candidates_sha256"]),
        "j3_in_cohort_exclusions_disclosed": j3["excluded"] is not None,
        "j3b_legacy_labeled_out_of_cohort": j3b["disclosure"][
            "out_of_cohort"] is True
            and j3b["disclosure"]["promotion_blocked_while_normal_path"]
            is True,
        "j3b_legacy_still_excludes_hard": j3b["excluded"] > 0,
        "j4_unknown_retention_transported": j4[
            "unknown_unplaced_included"] > 0
            and j4["eligibility_state_counts"].get(
                "unknown_attributes_retained", 0) > 0,
        "j5_unmapped_explicit": j5["identity_coverage"]["unmapped"] ==
            j5["candidates_sent"] and j5["identity_coverage"]["mapped"] == 0,
        "j6_empty_population_honest": j6["candidates_sent"] == 0
            and j6["m4_received"] == 0 and j6["ranked"] == 0
            and j6["partial_result"] is False,
        "j7_release_pin_not_bypassed_in_cohort": j7b["partial_result"] is True
            and any(f.get("error") == "release_mismatch"
                    for f in (j7b["failures"] or [])),
        "all_journeys_deterministic_replay": all(
            r["deterministic_replay"] for r in records),
        "all_in_cohort_journeys_assertion_holds": all(
            r["disclosure"]["assertion_holds"] is True for r in records
            if r["disclosure"]["mode"] == "discovery_cohort"
            and not (r["failures"] or [])),
    }
    out = {
        "blocker": "B6",
        "check": "M5 candidate generation explicitly controls the M4 ranking "
                 "population (owner-approved architecture)",
        "before": {
            "crosswalk_source": "tests/fixtures/discovery01/"
                                "m4_identity_crosswalk_v1.json (21 rows)",
            "candidate_list_sent_to_m4": False,
            "identity_mapped_exact": 0,
            "m4_parts_present_in_m5_catalog": "0/20",
            "facet_narrowing_influenced_ranking": False,
            "source": "results/discovery01-production-readiness/"
                      "identity_reconciliation.json"},
        "after": {
            "journeys": records,
            "checks": checks,
            "m4_service_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=M4_REPO,
                capture_output=True, text=True).stdout.strip(),
            "m5_service_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT,
                capture_output=True, text=True).stdout.strip(),
        },
        "pass": all(checks.values()),
    }
    write("b6_candidate_transport", out)
    return out


def check_b6restart():
    """J8: service restart. Compares the in-cohort journey against the
    anchor recorded by the b6 check (payload hash, disclosure, releases)."""
    anchor_file = OUT / "b6_candidate_transport.json"
    if not anchor_file.exists():
        return {"blocker": "B6", "check": "restart", "pass": False,
                "error": "run the b6 check first"}
    before = json.loads(anchor_file.read_text())
    j1 = next(j for j in before["after"]["journeys"]
              if j["journey"] == "J1_normal_supported_cohort")
    req = j1["request"]
    ans, raw, lat, err = post(
        M5 + "/v1/discovery/engineering-session", req, SESSION_TOKEN)
    after = _b6_record("J8_service_restart", req, ans, lat, err, None)
    same = {
        "candidates_sha256": after["candidates_sha256"] ==
            j1["candidates_sha256"],
        "release_fingerprints": after["release_fingerprints"] ==
            j1["release_fingerprints"],
        "disclosure": after["disclosure"] == j1["disclosure"],
        "counts": (after["candidates_sent"], after["m4_received"],
                   after["ranked"], after["unresolved_no_evidence"]) ==
                  (j1["candidates_sent"], j1["m4_received"], j1["ranked"],
                   j1["unresolved_no_evidence"]),
        "semantic_response": semantic(raw) is not None,
    }
    out = {"blocker": "B6", "check": "J8 service restart",
           "before": {k: j1[k] for k in ("candidates_sent", "m4_received",
                                         "ranked", "unresolved_no_evidence",
                                         "candidates_sha256",
                                         "release_fingerprints")},
           "after": {k: after[k] for k in ("candidates_sent", "m4_received",
                                           "ranked", "unresolved_no_evidence",
                                           "candidates_sha256",
                                           "release_fingerprints",
                                           "latency_ms")},
           "identical": same,
           "pass": all(v for k, v in same.items() if k !=
                       "semantic_response")}
    write("b6_restart", out)
    return out


CHECKS = {"b3": check_b3, "b2": check_b2, "b4": check_b4, "b5": check_b5,
          "b6": check_b6, "b6restart": check_b6restart}


def main(argv):
    names = argv or sorted(CHECKS)
    results = {}
    for name in names:
        if name not in CHECKS:
            print(f"unknown check {name!r}; have {sorted(CHECKS)}")
            return 2
        results[name] = CHECKS[name]()
    print(json.dumps({k: v.get("pass") for k, v in results.items()},
                     indent=1))
    return 0 if all(v.get("pass") for v in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
