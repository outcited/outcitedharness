#!/usr/bin/env python3
"""Timestamped before/after receipt for the Gas City Stage-A restart.

Captures both cities (read-only GETs) + supervisor identity into a JSON
receipt. Run BEFORE and AFTER the restart; the report is the diff.

  python3 neo-gascity/ops/restart_receipt.py before   # -> receipts/restart-<tag>.json
  python3 neo-gascity/ops/restart_receipt.py after
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.gascity_client import GasCityClient  # noqa: E402

CITIES = ("designwins-orchestrator", "designwins-staging")


def capture(client: GasCityClient, tag: str) -> Path:
    receipt: dict = {"tag": tag, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "schema": "gc.restart-receipt.v1", "cities": {}}
    receipt["supervisor"] = client.health()
    for city in CITIES:
        st = client.city_status(city)
        beads = client.beads(city, limit=300)[0]
        items = beads.get("items") or []
        ids = sorted(b["id"] for b in items)
        receipt["cities"][city] = {
            "running": st.get("running"), "suspended": st.get("suspended"),
            "agents": st.get("agents"), "work": st.get("work"),
            "partial": st.get("partial"), "partial_errors": st.get("partial_errors"),
            "bead_count": len(ids),
            "bead_set_digest": hashlib.sha256(",".join(ids).encode()).hexdigest()[:16],
            "statuses": {s: sum(1 for b in items if b.get("status") == s)
                         for s in ("open", "in_progress", "closed")},
            "sessions": [{"id": s.get("id"), "state": s.get("state")}
                         for s in (client.sessions(city).get("items") or [])],
        }
    out = Path(__file__).resolve().parent.parent / "receipts"
    out.mkdir(exist_ok=True)
    path = out / f"restart-{tag}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"wrote {path}")
    for city, c in receipt["cities"].items():
        print(f"  {city}: work={c['work']} beads={c['bead_count']} "
              f"digest={c['bead_set_digest']} sessions={len(c['sessions'])} "
              f"partial={c['partial']}")
    return path


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "manual"
    raise SystemExit(capture(GasCityClient("http://127.0.0.1:8373"), tag))
