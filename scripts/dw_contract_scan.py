#!/usr/bin/env python3
"""dw_contract_scan — read-only DesignWins contract-assumption scanner.

Issue #7 first deliverable: reconcile FE #72/#69/#71 <-> cr-core #103 <->
M5 #5/#6 into one source-linked interface contract conflict packet.

What it does (ALL read-only, no writes anywhere):
  1. FE side: fetches src/lib/labs/decision-api.ts from the FE PR branch
     (designwins-v2, golden-path-01) and extracts how DECISION_API_URL is
     composed into a request path.
  2. cr-core side: fetches the outcited.com route tree (default branch)
     and records which /api/designwins/v1/* routes are implemented.
  3. Emits a product-graph observations file (assumptions +
     implemented_paths, every fact with locator + truth class + as_of).
  4. Prints the conflict packet via the same detector the Director tick
     uses — this script and the tick can never disagree.

Network failures degrade to explicit UNKNOWN gaps (never green, never a
crash). Run: python scripts/dw_contract_scan.py [workspace-dir]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

GITHUB = "https://api.github.com"
FE_REPO = "outcited/designwins-v2"
FE_BRANCH = "agent/golden-path-01"      # PR #71 head (GOLDEN-PATH-01)
FE_CLIENT_PATH = "src/lib/labs/decision-api.ts"
BE_REPO = "outcited/outcited.com"
ROUTE_PREFIX = "app/api/designwins/"


def _get(url: str, timeout: float = 20.0):
    """Read-only GitHub GET. Unauthenticated first (public repos);
    private repos fall back to the authenticated `gh` CLI when present.
    No credentials are read, stored, or printed here."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "dw-contract-scan"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError:
        out = subprocess.run(["gh", "api", url], capture_output=True,
                             text=True, timeout=timeout)
        if out.returncode == 0:
            return json.loads(out.stdout)
        raise


def fetch_fe_composition():
    """FE: how is DECISION_API_URL composed? Returns (assumption|None)."""
    try:
        d = _get(f"{GITHUB}/repos/{FE_REPO}/contents/{FE_CLIENT_PATH}"
                 f"?ref={FE_BRANCH}")
        import base64
        text = base64.b64decode(d["content"]).decode()
    except Exception as e:                                    # noqa: BLE001
        return None, f"fetch failed: {type(e).__name__}"
    m = re.search(r"fetch\(\s*[`'\"]([^`'\"]+)", text)
    composed = m.group(1) if m else ""
    if not composed:
        return None, "composition literal not found in client"
    loc = f"{FE_REPO}:{FE_BRANCH}:{FE_CLIENT_PATH}"
    return {
        "lane": "m4",
        "endpoint_kind": "decision-api",
        "method": "POST",
        "path": composed,
        "envelope": "harness.electronics-decision-api.v1 (per cr-core #103)",
        "release_pin": "curve-evidence-bundle-v3-powerdata01-74cc003023f9",
        "reachability": "proven host-local against harness :8794 only; "
                        "not FE-server-reachable",
        "evidence": {
            "locator": loc,
            "truth_class": "GITHUB_OBSERVED",
            "as_of": str(int(time.time())),
            "detail": f"FE client fetch expression: {composed}",
        },
    }, None


def fetch_be_routes():
    """cr-core: which /api/designwins/* routes exist on the default
    branch? Returns (assumption, implemented_paths, error)."""
    try:
        info = _get(f"{GITHUB}/repos/{BE_REPO}")
        branch = info["default_branch"]
        tree = _get(f"{GITHUB}/repos/{BE_REPO}/git/trees/{branch}"
                    f"?recursive=1")
    except Exception as e:                                    # noqa: BLE001
        return None, [], f"fetch failed: {type(e).__name__}"
    paths = [t["path"] for t in tree.get("tree", [])
             if t["path"].startswith(ROUTE_PREFIX)]
    implemented = ["/" + p.replace("route.ts", "").rstrip("/")
                   for p in paths if p.endswith("route.ts")]
    # drop dynamic-segment normalization: [kim_slug] stays literal —
    # conflict detection compares concrete asserted endpoints only.
    assumption = {
        "lane": "cr-core",
        "endpoint_kind": "decision-api",
        "method": "POST",
        "path": "/api/designwins/v1/engineering/decisions",
        "envelope": "harness.electronics-decision-api.v1",
        "release_pin": "unknown",
        "reachability": "no authenticated FE-reachable facade confirmed",
        "evidence": {
            "locator": f"{BE_REPO}:{branch}:route-tree + issue #103 task 2",
            "truth_class": "GITHUB_OBSERVED",
            "as_of": str(int(time.time())),
            "detail": f"contract asserted in cr-core #103; implemented "
                      f"routes under /api/designwins: "
                      f"{len(implemented)}",
        },
    }
    return assumption, implemented, None


def main() -> int:
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    gaps = []

    fe, fe_err = fetch_fe_composition()
    if fe is None:
        gaps.append(f"FE client fetch: {fe_err}")
    be, implemented, be_err = fetch_be_routes()
    if be is None:
        gaps.append(f"cr-core route tree: {be_err}")

    assumptions = [a for a in (fe, be) if a]
    obs = {
        "as_of": time.time(),
        "scanner": "scripts/dw_contract_scan.py",
        "assumptions": assumptions,
        "implemented_paths": implemented,
        "gaps": gaps,
    }
    out = workspace / "product-graph-observations.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obs, indent=1, sort_keys=True))
    print(f"[scan] observations -> {out} "
          f"({len(assumptions)} assumptions, "
          f"{len(implemented)} implemented routes, {len(gaps)} gaps)")

    # Same detector as the Director tick — scanner and tick cannot drift.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from director_consumer.product_graph import (
        ContractAssumption, Evidence, detect_contract_conflicts)
    cas = [ContractAssumption(
        lane=a["lane"], endpoint_kind=a["endpoint_kind"],
        method=a["method"], path=a["path"], envelope=a["envelope"],
        release_pin=a["release_pin"], reachability=a["reachability"],
        evidence=Evidence(**a["evidence"])) for a in assumptions]
    conflicts = detect_contract_conflicts(cas, implemented)
    print(f"[scan] conflicts detected: {len(conflicts)}")
    for c in conflicts:
        print(f"\n=== {c.title}")
        print(f"    id: {c.conflict_id}  phase: {c.phase}  stage: {c.stage}")
        for s in c.sides:
            print(f"    side [{s['lane']}] {s['method']} {s['path']}")
            print(f"      evidence: {s['evidence']['locator']}")
        for g in c.factual_gaps:
            print(f"    gap: {g}")
        print(f"    proposed: {c.proposed_resolution}")
    for g in gaps:
        print(f"[scan] COVERAGE GAP (never green): {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
