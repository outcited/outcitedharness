#!/usr/bin/env python3
"""Probe live fleet state (endpoints + node containers), snapshot it, mail deltas.

Truth is derived, never declared: every run re-reads /v1/models and docker ps.
Snapshots: /Volumes/M5_4TB/fleet-state/snapshots/  Changes: CHANGES.md + agent-inbox mail.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Volumes/M5_4TB/fleet-state")
SNAP_DIR = ROOT / "snapshots"
CHANGES = ROOT / "CHANGES.md"
INBOX = Path("/Volumes/M5_4TB/agent-inbox")

ENDPOINTS = {
    "tp4_dsv41": "http://100.116.221.82:8888/v1/models",
    "asus3_nemotron": "http://100.89.118.36:8900/v1/models",
    "spark_embedder": "http://100.81.201.24:8800/v1/models",
}

NODES = {
    "dgx2": "samkim2@100.116.221.82",
    "asus1": "samkimasus1@100.124.181.13",
    "dgx3": "samkim3@100.73.119.63",
    "asus3": "samkimasus3@100.89.118.36",
    "asus2": "samkimasus2@100.68.133.1",
    "e10b": "samkim@100.81.201.24",
}

ASUS4_RELAY = ("samkimasus2@100.68.133.1", "samkimasus4@10.77.0.6")


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def probe_endpoints():
    state = {}
    for name, url in ENDPOINTS.items():
        out = run(f"curl -s --connect-timeout 4 --max-time 8 {url!r}")
        try:
            data = json.loads(out)
            state[name] = sorted(m.get("id", "?") for m in data.get("data", []))
        except Exception:
            state[name] = ["UNREACHABLE"]
    return state


def probe_nodes():
    state = {}
    for name, host in NODES.items():
        out = ssh_docker(host)
        state[name] = out
    relay, worker = ASUS4_RELAY
    state["asus4"] = ssh_docker(worker, via=relay)
    return state


def ssh_docker(host, via=None):
    try:
        if via:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", via,
                 f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} \"docker ps --format '{{{{.Names}}}}'\""],
                capture_output=True, text=True, timeout=25)
        else:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                 "docker ps --format '{{.Names}}'"],
                capture_output=True, text=True, timeout=25)
        if r.returncode != 0:
            return ["SSH_FAIL"]
        return sorted(n for n in r.stdout.split() if n) or ["NO_CONTAINERS"]
    except Exception:
        return ["SSH_FAIL"]


def diff(old, new):
    lines = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a != b:
            lines.append(f"{key}: {a} -> {b}")
    return lines


def mail(delta, stamp):
    subject = f"FLEET CHANGE DETECTED: {'; '.join(d.split(':')[0] for d in delta)}"
    slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = (
        f"---\nid: fleet-change-{slug}\nfrom: fleet-state-probe\n"
        f"to: all\ntype: status\nin_reply_to: null\n"
        f'subject: "{subject}"\ncreated_at: {stamp}\n'
        f"requires_response_by: null\nblocks_approval: null\n---\n\n"
        f"Automated fleet-state probe detected a change:\n\n"
        + "\n".join(f"- {d}" for d in delta)
        + "\n\nIf you made this change, reply with why (one line). If you did not, investigate.\n"
        f"Snapshot: {SNAP_DIR / (stamp.replace(':', '').replace('-', '') + '.json')}\n"
    )
    for box in ("cursor-cr", "m5-opencode", "m4opencode"):
        d = INBOX / box
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}_fleet-state-probe_{box}_status_fleet-change.md").write_text(body)


def main():
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"timestamp": stamp, "endpoints": probe_endpoints(), "nodes": probe_nodes()}
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snaps = sorted(SNAP_DIR.glob("*.json"))
    latest = json.loads(snaps[-1].read_text()) if snaps else None
    if latest:
        delta = diff(
            {**latest.get("endpoints", {}), **latest.get("nodes", {})},
            {**state["endpoints"], **state["nodes"]},
        )
        if delta:
            entry = f"\n## {stamp}\n" + "\n".join(f"- {d}" for d in delta) + "\n"
            with CHANGES.open("a") as f:
                f.write(entry)
            mail(delta, stamp)
            print(f"CHANGED:\n{entry}")
        else:
            print(f"OK: no change ({stamp})")
    path = SNAP_DIR / (now.strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(state, indent=2, sort_keys=True))
    keep = sorted(SNAP_DIR.glob("*.json"))[-168:]
    for old in sorted(SNAP_DIR.glob("*.json"))[:-168]:
        old.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
