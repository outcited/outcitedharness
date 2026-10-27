#!/usr/bin/env python3
"""P3 gate demo — 10 acceptance items, live against designwins-staging ONLY.

Valid authorization uses the staging-fixture trust dir (issuer
'staging-fixture', environment staging). Nothing here is or claims to be a
production approval. Production city is read-only (snapshot sanity only).

Run: python3 demo/gate_demo.py --base-url http://127.0.0.1:8373
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import finding as F, handoff as H, neo_status as NS
from bridge.gascity_client import GasCityClient

CITY = "designwins-staging"
TRUST = ROOT / "demo" / "trust-staging"
OUT = ROOT / "demo" / "out"


def gate_finding(fid="DW-NEO-GATE-02", summary="gate demo full proof"):
    return {
        "schema": "dw.finding.v1",
        "finding_id": fid,
        "task_id": f"{fid} — gate demo full proof",
        "source_url": "https://github.com/outcited/outcitedharness/pull/52",
        "repository": "outcited/outcitedharness",
        "severity": "info",
        "summary": summary,
        "engineering_impact": "none — P3 gate demo, staging queue only",
        "reproduction": ["gate_demo.py item flow"],
        "expected_behavior": "accepted once with full proof; replays duplicate",
        "regression_tests": {
            "positive": ["tests: test_accepted_with_full_proof"],
            "negative": ["tests: test_forged_issuer_string_refused"]},
        "authorization_state": "authorized",
        "owner": "neo-bridge",
    }


def write_fixture(finding: dict) -> Path:
    TRUST.mkdir(mode=0o700, exist_ok=True)
    art = TRUST / "sam-stagingfixture-gate02.json"
    a = json.loads((TRUST / "sam-stagingfixture-gate02.template.json").read_text())
    a["payload_digest"] = F.digest(finding)
    art.write_text(json.dumps(a, indent=2) + "\n")
    art.chmod(0o600)
    return art


def run(base_url: str) -> int:
    client = GasCityClient(base_url)
    findings_before = [b.get("id") for b in client.beads(CITY, limit=200)[0].get("items", [])
                       if any(str(l).startswith("neo-finding:") for l in (b.get("labels") or []))]
    agents_before = client.city_status(CITY).get("agents")
    results = []

    def check(n, ok, detail):
        results.append((n, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {n}: {detail}")

    # 1 forged approval refused (issuer string alone proves nothing)
    f2 = gate_finding()
    forged = TRUST / "forged.json"
    forged.write_text(json.dumps({**json.loads((TRUST / "sam-stagingfixture-gate02.template.json").read_text()),
                                  "payload_digest": F.digest(f2), "issuer": "sam"}))
    forged.chmod(0o600)
    r = H.submit(f2, forged, CITY, client, OUT / "gate", [TRUST])
    check(1, r["state"] == "refused" and r["refusal"]["reason"] == "unauthorized-issuer",
          f"{r['state']}/{r.get('refusal',{}).get('reason')}")

    # 2 missing digest refused
    nodig = TRUST / "nodigest.json"
    nodig.write_text(json.dumps({k: v for k, v in json.loads(
        (TRUST / "sam-stagingfixture-gate02.template.json").read_text()).items()
        if k != "payload_digest"} | {"payload_digest": None}))
    nodig.chmod(0o600)
    r = H.submit(f2, nodig, CITY, client, OUT / "gate", [TRUST])
    check(2, r["state"] == "refused" and r["refusal"]["reason"] == "invalid-approval",
          f"{r['state']}/{r.get('refusal',{}).get('reason')}")

    # 3 wrong city + wrong project refused
    wrongcity = TRUST / "wrongcity.json"
    a = json.loads((TRUST / "sam-stagingfixture-gate02.template.json").read_text())
    a.update(payload_digest=F.digest(f2), recipient="designwins-orchestrator")
    wrongcity.write_text(json.dumps(a)); wrongcity.chmod(0o600)
    r = H.submit(f2, wrongcity, CITY, client, OUT / "gate", [TRUST])
    ok_city = r["state"] == "refused" and r["refusal"]["reason"] in (
        "unauthorized-recipient", "environment-mismatch")
    wrongproj = TRUST / "wrongproj.json"
    a.update(recipient=CITY, project="not-designwins")
    wrongproj.write_text(json.dumps(a)); wrongproj.chmod(0o600)
    r2 = H.submit(f2, wrongproj, CITY, client, OUT / "gate", [TRUST])
    ok_proj = r2["state"] == "refused" and r2["refusal"]["reason"] == "unauthorized-project"
    check(3, ok_city and ok_proj,
          f"city={r.get('refusal',{}).get('reason')} project={r2.get('refusal',{}).get('reason')}")

    # untrusted path (outside trust root) refused — placement is the trust
    stray = ROOT / "demo" / "stray-artifact.json"
    stray.write_text((TRUST / "sam-stagingfixture-gate02.template.json").read_text().replace(
        "FILLED_BY_GATE_SCRIPT", F.digest(f2)))
    r = H.submit(f2, stray, CITY, client, OUT / "gate", [TRUST])
    check("3b", r["state"] == "refused" and r["refusal"]["reason"] == "untrusted-path",
          f"{r['state']}/{r.get('refusal',{}).get('reason')}")

    # 4 valid staging-fixture authorization accepted (fresh finding => event proof)
    art = write_fixture(f2)
    r4 = H.submit(f2, art, CITY, client, OUT / "gate", [TRUST])
    check(4, r4["state"] == "accepted" and r4["stage"] == "BEAD_ACCEPTED",
          f"state={r4['state']} stage={r4.get('stage')} bead={r4.get('bead_id')} "
          f"event_seq={r4.get('event_proof',{}).get('seq')}")

    # 5 read-back + receipt match
    rb = client.bead(CITY, r4["bead_id"])
    ok5, why = H.verify_readback(rb, f2)
    receipt = json.loads(Path(r4["receipt_path"]).read_text())
    check(5, ok5 and receipt["finding_digest"] == F.digest(f2)
          and receipt["readback"]["id"] == r4["bead_id"]
          and receipt["event_proof"]["subject"] == r4["bead_id"],
          f"readback={why} receipt={Path(r4['receipt_path']).name}")

    # 6 resubmission creates zero duplicate beads (ledger replay, no POST)
    r6 = H.submit(f2, art, CITY, client, OUT / "gate", [TRUST])
    findings_after = [b.get("id") for b in client.beads(CITY, limit=200)[0].get("items", [])
                      if any(str(l).startswith("neo-finding:") for l in (b.get("labels") or []))]
    new = set(findings_after) - set(findings_before)
    check(6, r6["state"] == "duplicate" and r6["bead_id"] == r4["bead_id"]
          and new == {r4["bead_id"]},
          f"{r6['state']}/{r6.get('duplicate_source')} new_beads={sorted(new)}")

    # 7 missing acceptance evidence fails closed: ledger wiped => label dedup
    #    still refuses a second create (restart-safe negative)
    fresh_dir = OUT / "gate-restart"
    r7 = H.submit(f2, art, CITY, client, fresh_dir, [TRUST])
    check(7, r7["state"] == "duplicate" and r7["duplicate_source"] == "label-check",
          f"{r7['state']}/{r7.get('duplicate_source')} (no new POST; offline "
          f"read-back failures covered by unit tests test_failed_readback_never_accepted)")

    # 8 restart/replay preserves state (ledger replay across "restart")
    r8 = H.submit(f2, art, CITY, client, OUT / "gate", [TRUST])
    check(8, r8["state"] == "duplicate" and r8["duplicate_source"] == "approval-ledger",
          f"{r8['state']}/{r8.get('duplicate_source')}")

    # 9 snapshots accurate + sanitized
    snap = NS.build_snapshot(client, CITY)
    san = NS.sanitize_snapshot(snap)
    blob = json.dumps(san)
    ok9 = (r4["bead_id"] in json.dumps(san["findings"])
           and "prompt" not in blob and "description" not in blob
           and san["findings"][0]["stage"] in ("BEAD_ACCEPTED",))
    check(9, ok9, f"staging findings tracked={len(san['findings'])} sanitized=yes "
                  f"stage={san['findings'][0]['stage']}")

    # 10 no unauthorized dispatch: staging agents unchanged, none running
    agents_after = client.city_status(CITY).get("agents")
    sessions = client.sessions(CITY).get("items") or []
    check(10, agents_before == agents_after and not sessions,
          f"agents {agents_before} -> {agents_after}, sessions={len(sessions)} "
          f"(bridge has no dispatch code path; sling is manual-only)")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nGATE RESULT: {len(results)-len(failed)}/{len(results)} passed"
          + (f" — FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    url = sys.argv[sys.argv.index("--base-url") + 1] if "--base-url" in sys.argv \
        else "http://127.0.0.1:8373"
    raise SystemExit(run(url))
