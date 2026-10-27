#!/usr/bin/env python3
"""Isolated live demo: one authorized finding -> designwins-staging queue.

Authorization: task brief 2026-10-27 ("isolated test demonstrating one
finding flowing into a test Gas City queue"). Production city
(designwins-orchestrator) is NEVER written — read-only snapshot only.

Sequence (mirrors Phase 5 acceptance items 1-3, 8):
  A. negative control: no approval artifact -> refused, zero API mutations
  B. authorized submit -> accepted receipt (idempotency key + read-back + events)
  C. resubmit same finding/approval -> duplicate, no second bead created
  D. NEO read-only snapshots for staging (and production, read-only)

Run: python3 demo/run_demo.py --base-url http://127.0.0.1:8373
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import finding as F, handoff as H, neo_status as NS
from bridge.gascity_client import GasCityClient

STAGING = "designwins-staging"
PRODUCTION = "designwins-orchestrator"


def main(base_url: str) -> int:
    client = GasCityClient(base_url)
    out = ROOT / "demo" / "out"
    out.mkdir(exist_ok=True)
    finding = json.loads((ROOT / "demo/DW-NEO-DEMO-01.finding.json").read_text())
    gaps = F.validate(finding)
    assert not gaps, f"demo finding invalid: {gaps}"

    health = client.health()
    print(f"supervisor: {health.get('status')} v{health.get('version')} "
          f"cities={health.get('cities_running')}/{health.get('cities_total')}")

    # -- A. negative control: unauthorized submit is refused pre-API ----------
    r_a = H.submit(dict(finding, authorization_state="proposal"),
                   ROOT / "demo/no-such-approval.json", STAGING, client, out)
    print(f"A refused: state={r_a['state']} reason={r_a.get('refusal',{}).get('reason')}")

    # -- build the demo approval (digest covers this exact finding body) -----
    approval = {
        "project": "designwins", "action": "gascity-ingest", "recipient": STAGING,
        "task_ref": finding["task_id"], "issuer": "sam",
        "authorization_ref": "task brief 2026-10-27 NEO<->Gas City Step 4 isolated test",
        "expires_at": int(time.time()) + 7200, "max_actions": 1,
        "spend_scope": "none", "max_spend_usd": 0,
        "payload_digest": F.digest(finding),
    }
    ap = out / "DW-NEO-DEMO-01.approval.json"
    ap.write_text(json.dumps(approval, indent=2) + "\n")

    # -- B. authorized idempotent submit --------------------------------------
    r_b = H.submit(finding, ap, STAGING, client, out)
    print(f"B submit: state={r_b['state']} bead={r_b.get('bead_id')} "
          f"key={r_b.get('idempotency_key','')[:16]}… attempts={r_b.get('attempts')}")
    assert r_b["state"] in ("accepted", "duplicate"), r_b

    # -- C. resubmit: must be duplicate, same bead, no new create -------------
    r_c = H.submit(finding, ap, STAGING, client, out)
    print(f"C resubmit: state={r_c['state']} bead={r_c.get('bead_id')} "
          f"(same bead: {r_c.get('bead_id') == r_b.get('bead_id')})")

    # -- D. NEO read-only snapshots -------------------------------------------
    for city, writable in ((STAGING, True), (PRODUCTION, False)):
        snap = NS.build_snapshot(client, city)
        alerts = NS.evaluate(snap)
        (out / f"neo-snapshot-{city}.json").write_text(json.dumps(snap, indent=2) + "\n")
        (out / f"neo-snapshot-{city}.md").write_text(NS.render_markdown(snap, alerts))
        neo = [b.get("id") for b in snap["findings"]]
        print(f"D snapshot {city}: findings={neo} alerts={[a['rule'] for a in alerts]}")
        if writable:
            assert r_b.get("bead_id") in neo, "demo bead not visible in staging snapshot"

    print("DEMO COMPLETE — receipts in demo/out/")
    return 0


if __name__ == "__main__":
    url = sys.argv[sys.argv.index("--base-url") + 1] if "--base-url" in sys.argv \
        else "http://127.0.0.1:8373"
    raise SystemExit(main(url))
