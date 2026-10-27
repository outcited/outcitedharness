#!/usr/bin/env python3
"""Controlled end-to-end acceptance test (Phase 5 gate 1).

Legs:
  L1 reviewed finding          — validate dw.finding.v1 fixture            [offline]
  L2 authorized Gas City bead  — handoff.submit + acceptance receipt        [live, staging]
  L3 agent execution           — REQUIRES --live-dispatch + Sam artifact    [gated]
  L4 tested PR                 — agent pushes branch, CI runs               [gated: gh token]
  L5 independent verification  — re-read bead + PR checks via GitHub API    [read-only]

Defaults run L1+L2+L5(read-back leg) exactly as demonstrated 2026-10-27.
L3/L4 REFUSE without --live-dispatch AND a dispatch approval artifact; they
print the exact operator commands instead (nothing executes, nothing merges).

Usage:
  python3 demo/acceptance_e2e.py --base-url http://127.0.0.1:8373
  python3 demo/acceptance_e2e.py --live-dispatch --dispatch-approval <artifact>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import finding as F, handoff as H          # noqa: E402
from bridge.gascity_client import GasCityClient        # noqa: E402

CITY_DEFAULT = "designwins-staging"   # production requires --city + artifact naming it
TRUST = Path(__file__).resolve().parent / "trust-staging"


def _ensure_staging_fixture(finding: dict) -> Path:
    import json
    TRUST.mkdir(mode=0o700, exist_ok=True)
    art = TRUST / "sam-stagingfixture-e2e.json"
    art.write_text(json.dumps({
        "schema": "dw.approval.gascity-ingest.v1", "environment": "staging",
        "project": "designwins", "action": "gascity-ingest",
        "recipient": CITY_DEFAULT, "task_ref": finding["task_id"],
        "issuer": "staging-fixture",
        "authorization_ref": "e2e acceptance fixture (staging-only, not production approval)",
        "expires_at": int(time.time()) + 7200, "max_actions": 1,
        "spend_scope": "none", "max_spend_usd": 0,
        "payload_digest": F.digest(finding)}, indent=2))
    art.chmod(0o600)
    return art


def leg1(finding: dict) -> str:
    gaps = F.validate(finding)
    assert not gaps, gaps
    print(f"L1 PASS  finding {finding['finding_id']} valid, digest {F.digest(finding)[:16]}")
    return F.digest(finding)


def leg2(finding: dict, city: str, client: GasCityClient, out: Path) -> dict:
    art = _ensure_staging_fixture(finding)
    r = H.submit(finding, art, city, client, out, [TRUST])
    assert r["state"] in ("accepted", "accepted-persisted", "duplicate"), r
    assert r.get("readback") and r["readback"]["id"] == r["bead_id"]
    print(f"L2 PASS  bead {r['bead_id']} state={r['state']} stage={r.get('stage')} "
          f"receipt={Path(r['receipt_path']).name}")
    return r


def leg3_gated(finding: dict, city: str, args) -> None:
    bead_ref = finding["finding_id"]
    if not (args.live_dispatch and args.dispatch_approval):
        print("L3 GATED agent execution NOT run. Exact operator sequence:")
        print(f"     1) Sam authors dispatch artifact for {bead_ref} (city {city})")
        print(f"     2) On the mini: gc -C ~/<city-dir> sling <bead-id> <rig>")
        print("     3) Agent session executes the bead in its worktree")
        return
    da = json.loads(Path(args.dispatch_approval).read_text())
    assert da.get("issuer") == "sam" and da.get("action") == "gascity-dispatch", da
    assert da.get("task_ref") == finding["task_id"], "dispatch artifact/task mismatch"
    print(f"L3 PASS  dispatch authorized by {args.dispatch_approval}; "
          "sling remains a manual operator command on the mini (no auto-dispatch exists)")


def leg4_gated(finding: dict, args) -> None:
    print("L4 GATED tested PR — requires (a) gh auth fixed on the mini, "
          "(b) L3 executed. Agent-side sequence (operator-visible):")
    print("     polecat/engineer pushes polecat/<name>/<issue> branch -> gh pr create")
    print("     refinery/merge-queue pack runs test gates (merge_queue.run_tests)")
    print("     CI receipt captured by L5 below once the PR exists")


def leg5(finding: dict, city: str, client: GasCityClient, receipt: dict) -> None:
    b = client.bead(city, receipt["bead_id"])
    assert b["id"] == receipt["bead_id"] and b.get("status") in ("open", "in_progress", "closed")
    labels = b.get("labels") or []
    assert any(str(l).startswith("neo-finding:") for l in labels), labels
    print(f"L5 PASS  independent read-back: bead {b['id']} status={b['status']} "
          f"labels={labels[:2]}…")
    pr = (finding.get("verification") or {}).get("pr")
    if pr:
        print(f"L5 would verify PR #{pr} checks via GitHub API (gh token present here)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8373")
    ap.add_argument("--city", default=CITY_DEFAULT)
    ap.add_argument("--finding", default=str(ROOT / "demo/DW-NEO-DEMO-01.finding.json"))
    ap.add_argument("--live-dispatch", action="store_true")
    ap.add_argument("--dispatch-approval", default="")
    args = ap.parse_args()

    finding = json.loads(Path(args.finding).read_text())
    out = ROOT / "demo" / "out"
    client = GasCityClient(args.base_url)
    print(f"acceptance e2e @ {args.base_url} city={args.city} "
          f"live-dispatch={args.live_dispatch}")
    t0 = time.time()
    leg1(finding)
    receipt = leg2(finding, args.city, client, out)
    leg3_gated(finding, args.city, args)
    leg4_gated(finding, args)
    leg5(finding, args.city, client, receipt)
    print(f"E2E COMPLETE in {time.time()-t0:.1f}s — L1/L2/L5 demonstrated, L3/L4 gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
