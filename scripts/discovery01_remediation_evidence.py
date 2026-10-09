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


CHECKS = {"b3": check_b3, "b2": check_b2}


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
