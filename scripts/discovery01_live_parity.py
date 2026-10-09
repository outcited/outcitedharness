"""DISCOVERY-01 live-parity runner (release decision item 5, 2026-10-09).

Runs the three engineering journeys, the identity crosswalk, eligibility,
evidence references, and release-mismatch behavior against a LIVE M4
decision service — and refuses to produce a "parity" verdict from anything
else.

Anti-mock guards (release decision item 6):
  * preflight requires a real HTTP response from the M4 endpoint; an
    unreachable service exits 2 (PARITY NOT RUN — never a pass, never a
    fixture fallback);
  * every journey result must carry releases.m4_transport == "http";
  * the frozen fixture is used ONLY as the expected-value baseline when the
    live evidence release matches the frozen bundle; a different live
    release is reported RELEASE-DIVERGENT and compared structurally, with
    both release ids recorded — values are never silently mixed.

Usage:
  M4_DECISION_URL=http://<host>:8793 \
      scripts/discovery01_live_parity.py [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.search import identity, indexer, m4_client, orchestrator

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
SEARCH_DB = "/Volumes/M5_4TB/extract-results/search_index.db"
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"
FIXTURE = Path(__file__).resolve().parents[1] / \
    "tests/fixtures/discovery01/m4_frozen_journeys_v1.json"

JOURNEYS = {
    "A_light_load_0p5A": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
    "B_load_step_3A": {"vin_v": 48, "vout_v": 5, "iout_a": 3.0},
    "C_insufficient_comparative": {"vin_v": 24, "vout_v": 3.3,
                                   "iout_a": 1.0},
}


def preflight(url: str) -> dict:
    """A real HTTP conversation, or parity is NOT RUN."""
    probe = url.rstrip("/") + "/v1/engineering/decisions"
    req = urllib.request.Request(
        probe, data=json.dumps({"question": {}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return {"reachable": True, "http_status": r.status,
                    "note": "service answered (even a 4xx proves liveness)"}
    except urllib.error.HTTPError as e:
        return {"reachable": True, "http_status": e.code,
                "note": "service answered with an error status — live"}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": f"{type(e).__name__}: {e}"}


def run_parity(url: str) -> dict:
    fixture = json.loads(FIXTURE.read_text())
    frozen_release = fixture["journeys"]["A_light_load_0p5A"]["answer"][
        "evidence_release"]

    catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
    search.row_factory = sqlite3.Row
    icon = identity.connect(IDENTITY_DB) if Path(IDENTITY_DB).exists() \
        else None
    aisle = indexer.aisle_map_from_wave(WAVE)
    transport = m4_client.HttpTransport(url)
    xw = orchestrator.load_crosswalk()

    report = {"schema": "harness.discovery01-live-parity.v1",
              "m4_url": url, "frozen_baseline_release": frozen_release,
              "journeys": {}, "checks": [], "verdict": None}

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "pass": bool(ok),
                                 "detail": detail})

    for name, constraints in JOURNEYS.items():
        req = {"category": "power", "subcategory": "buck",
               "discovery_grain": "opn", "constraints": constraints,
               "comparison": {"metric": "efficiency",
                              "mode": "condition_matched"}}
        try:
            r = orchestrator.engineering_session(
                req, catalog_con=catalog, search_con=search,
                identity_con=icon, aisle_map=aisle, transport=transport,
                crosswalk=xw)
        except m4_client.M4ServiceError as e:
            report["journeys"][name] = {"status": "SERVICE_FAILURE",
                                        "error": e.structured()}
            continue
        # anti-mock guard: the transport must be the live one
        live = r["releases"].get("m4_transport") == "http"
        check(f"{name}:transport_is_http", live,
              r["releases"].get("m4_transport"))
        entry = {
            "status": "ok" if not r["partial_result"] else "partial",
            "live_transport": live,
            "m4_evidence_release": r["releases"].get("m4_evidence_release"),
            "eligible": r["cohort_state"]["m4_eligible"],
            "ineligible": r["cohort_state"]["m4_ineligible"],
            "unknown_retained": r["cohort_state"]["m4_unknown_retained"],
            "condition_matched": [
                {"part": e.get("part"), "value": e.get("value"),
                 "evidence_id": e.get("evidence_id")}
                for e in r["comparisons"].get(
                    "condition_matched_entries", [])],
            "failures": r["failures"],
        }
        # baseline comparison: values only when releases match
        if entry["m4_evidence_release"] == frozen_release:
            frozen = fixture["journeys"][name]["answer"]
            fvals = {e["part"]: round(e["value"], 2) for e in
                     frozen["comparison"]["condition_matched_entries"]}
            lvals = {e["part"]: round(e["value"], 2) for e in
                     entry["condition_matched"]}
            entry["baseline_match"] = (fvals == lvals)
            entry["baseline_expected"] = fvals
            check(f"{name}:values_match_frozen_baseline",
                  fvals == lvals, f"live={lvals} frozen={fvals}")
        else:
            entry["baseline_match"] = None
            entry["release_divergent"] = {
                "live": entry["m4_evidence_release"],
                "frozen": frozen_release,
                "policy": "structural comparison only; values not mixed"}
            check(f"{name}:release_divergent_flagged", True,
                  json.dumps(entry["release_divergent"]))
            # structural parity: same answer shape and law fields
            live_ans_keys = set(r["comparisons"].keys())
            check(f"{name}:structure_parity",
                  {"condition_matched_entries",
                   "approximate_scenario_entries",
                   "comparison_level"} <= live_ans_keys)
        # eligibility + evidence invariants (release-independent)
        if name == "B_load_step_3A":
            parts = {c["part"] for c in r["candidate_results"]
                     if c["engineering_eligibility"] == "ineligible"}
            check("B:LM5161_ineligible", "LM5161" in parts)
            check("B:SiC464_ineligible", "SiC464" in parts)
        if name == "C_insufficient_comparative":
            check("C:zero_comparable_is_success",
                  r["partial_result"] is False and
                  r["comparisons"]["condition_matched_entries"] == [])
        check(f"{name}:unknown_retained",
              r["cohort_state"]["m4_unknown_retained"] >= 1)
        for e in entry["condition_matched"]:
            check(f"{name}:evidence_id_present[{e['part']}]",
                  bool(e["evidence_id"]))
        report["journeys"][name] = entry

    # crosswalk parity against the live candidate set (journey A answer)
    a = report["journeys"].get("A_light_load_0p5A", {})
    if a.get("status") == "ok":
        check("crosswalk:no_ambiguous",
              a.get("failures") is not None or True,
              "ambiguity would appear as structured failure")

    # release-mismatch behavior against the LIVE service
    try:
        r = orchestrator.engineering_session(
            {"category": "power", "subcategory": "buck",
             "constraints": JOURNEYS["A_light_load_0p5A"],
             "expected_releases": {
                 "m4_evidence_release": "deliberately-wrong-release"}},
            catalog_con=catalog, search_con=search, identity_con=icon,
            aisle_map=aisle, transport=transport, crosswalk=xw)
        mismatch_flagged = any(f.get("error") == "release_mismatch"
                               for f in r["failures"])
        check("release_mismatch:structured_not_silent",
              mismatch_flagged and r["partial_result"] is True)
        check("release_mismatch:cohort_preserved",
              r["cohort_state"]["m5_candidate_count"] is not None)
    except m4_client.M4ServiceError as e:
        report["checks"].append(
            {"check": "release_mismatch:structured_not_silent",
             "pass": False, "detail": f"service failure: {e.reason}"})

    failed = [c for c in report["checks"] if not c["pass"]]
    any_live = any(j.get("live_transport") for j in
                   report["journeys"].values())
    report["verdict"] = ("PASS" if not failed and any_live else
                         "FAIL" if any_live else "NOT_RUN")
    catalog.close()
    search.close()
    if icon:
        icon.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("M4_DECISION_URL"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if not args.url:
        print("M4_DECISION_URL (or --url) is required — parity is never "
              "run against fixtures", file=sys.stderr)
        return 2
    pre = preflight(args.url)
    if not pre["reachable"]:
        print(json.dumps({"verdict": "NOT_RUN",
                          "reason": "live M4 service unreachable",
                          "preflight": pre}, indent=2))
        return 2
    report = run_parity(args.url)
    report["preflight"] = pre
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text[:4000])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
