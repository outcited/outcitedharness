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
    j = journey(0.5)
    ans, raw, lat, err = session(j)
    ev = (ans or {}).get("evidence") or {}
    entries = ((ans or {}).get("comparisons") or {}).get(
        "condition_matched_entries") or []
    cited = ev.get("curve_evidence_ids") or []
    docs = ev.get("documents") or {}
    unresolved = ev.get("unresolved_evidence_ids") or []
    entry_shas = sorted({(e.get("source") or {}).get("document_sha256")
                         for e in entries
                         if (e.get("source") or {}).get("document_sha256")})
    out = {
        "blocker": "B2",
        "check": "M4 nested source provenance resolves through the M5 index",
        "before": {"documents_resolved": 0, "unresolved_evidence_ids": 4,
                   "cited_evidence_ids": 4,
                   "source": "results/discovery01-production-readiness/"
                             "provenance_probe.json"},
        "after": {
            "cited_evidence_ids": cited,
            "documents_resolved": sorted(docs.keys()),
            "documents_resolved_count": len(docs),
            "unresolved_evidence_ids": unresolved,
            "entry_source_sha256": entry_shas,
            "every_cited_id_resolved": bool(cited) and not unresolved,
            "sample_document_view": (
                {k: docs[k] for k in list(docs)[:1]} if docs else {}),
            "per_evidence_id_resolution": {
                eid: {k: v.get(k) for k in (
                    "resolved", "reason", "resolution_path",
                    "document_sha256", "sha_path", "page_1based",
                    "figure_index", "series_index", "caption",
                    "source_artifact", "indexed_units", "index_pages",
                    "evidence_grades", "verification_states",
                    "m4_page_present_in_index", "parts")}
                for eid, v in (ev.get("evidence_ids") or {}).items()},
            "resolution_summary": ev.get("resolution"),
            "precedence": ev.get("precedence"),
            "provenance_conflicts": ev.get("provenance_conflicts"),
            "latency_ms": lat,
            "semantic_sha256": semantic(raw),
        },
        "errors": [err] if err else [],
        "pass": bool(cited) and not unresolved and len(docs) >= len(
            entry_shas),
    }
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


CHECKS = {"b3": check_b3, "b2": check_b2, "b4": check_b4}


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
