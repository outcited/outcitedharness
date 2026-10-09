#!/usr/bin/env python3
"""Probe live fleet state (endpoints + node containers), snapshot it, mail deltas.

Truth is derived, never declared: every run re-reads the services themselves
(/healthz, /health, /v1/models) and docker ps.
Snapshots: /Volumes/M5_4TB/fleet-state/snapshots/  Changes: CHANGES.md + agent-inbox mail.

A probe never reports an empty list. "Reachable but not serving what we expect"
is recorded as HTTP_<code>, because that is the failure mode that hides: the
e10b embedder 404s on /v1/models while healthy, and stock bge-m3 on asus2 404s
on /healthz. Both used to be indistinguishable from silence.
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

# name -> (url, probe kind)
ENDPOINTS = {
    "tp4_dsv41": ("http://100.116.221.82:8888/v1/models", "models"),
    "asus3_nemotron": ("http://100.89.118.36:8900/v1/models", "models"),
    # Primary canon/judge: harness/pipeline/api.py, scripts/sentinel.py,
    # scripts/canon_gate.py and scripts/soak_sample.py all default to this
    # endpoint, so it is probed as a service, not just a container.
    "asus4_canon": ("http://100.100.116.82:8900/v1/models", "models"),
    # Second canon replica (restored 2026-10-09 on Sam's call). Same model, port
    # 8901. Probed so a replica that is running-but-not-serving is visible:
    # "container up" and "judge answers" are different facts.
    "dgx1_canon_replica": ("http://100.81.201.24:8901/v1/models", "models"),
    # Production embedder since 2026-10-09: RELOCATED off dgx1/e10b to asus4
    # (dgx1 is now a training box). Same weights (FAE v4 behind the
    # bge-m3-cr-tapes-v1 label), verified bit-identical before cutover.
    # /healthz is the route it actually implements; /v1/models 404s.
    "spark_embedder": ("http://100.100.116.82:8800/healthz", "embedder"),
    # GCI moved with the embedder (they are welded by harness/gci/encoder.py).
    # It binds only to its Tailscale address; 401 = alive, bearer-guarded.
    "harness_gci": ("http://100.100.116.82:8810/health", "gci"),
}

# asus4 is Tailscale-only: it gave its fabric switch port to dgx1 on 2026-10-04,
# so the old relay path via 10.77.0.6 is unroutable and reported a false SSH_FAIL
# for four days. Direct is the only path that exists.
NODES = {
    "dgx2": "samkim2@100.116.221.82",
    "asus1": "samkimasus1@100.124.181.13",
    "dgx3": "samkim3@100.73.119.63",
    "asus3": "samkimasus3@100.89.118.36",
    "asus2": "samkimasus2@100.68.133.1",
    "asus4": "samkimasus4@100.100.116.82",
    "e10b": "samkim@100.81.201.24",
}


def http_get(url, timeout=8):
    """Return (status, body). status 0 means the connection never completed."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--connect-timeout", "4", "--max-time", str(timeout),
             "-w", "\n%{http_code}", url],
            capture_output=True, text=True, timeout=timeout + 5)
    except Exception:
        return 0, ""
    if r.returncode != 0:
        return 0, ""
    body, _, code = r.stdout.rpartition("\n")
    try:
        return int(code.strip()), body.strip()
    except ValueError:
        return 0, body.strip()


def probe_models(url):
    status, body = http_get(url)
    if status == 0:
        return ["UNREACHABLE"]
    if status != 200:
        return [f"HTTP_{status}"]
    try:
        data = json.loads(body)
    except Exception:
        return ["HTTP_200_NON_JSON"]
    ids = sorted(str(m.get("id", "?")) for m in data.get("data", []))
    return ids or ["HTTP_200_NO_MODELS"]


def probe_embedder(url):
    """Identity, not just liveness: model label + device + route count.

    uptime_sec is deliberately excluded so a healthy service does not emit a
    delta every run. A stock-bge-m3 impostor on this port fails here with
    HTTP_404, which is exactly the signal we want.
    """
    status, body = http_get(url)
    if status == 0:
        return ["UNREACHABLE"]
    if status != 200:
        return [f"HTTP_{status}"]
    try:
        data = json.loads(body)
    except Exception:
        return ["HTTP_200_NON_JSON"]
    state = "READY" if data.get("ready") else "NOT_READY"
    return [
        f"{state} model={data.get('model', '?')} "
        f"device={data.get('device', '?')} routes={len(data.get('routes', []))}"
    ]


def probe_gci(url):
    """/health is bearer-guarded (harness/gci/api.py). We probe unauthenticated:
    401 proves the process is up and serving. The token is never passed, so it
    can never leak into a process list or a snapshot.
    """
    status, body = http_get(url)
    if status == 0:
        return ["UNREACHABLE"]
    if status == 401:
        return ["ALIVE_AUTH_REQUIRED"]
    if status == 200:
        try:
            data = json.loads(body)
        except Exception:
            return ["HTTP_200_NON_JSON"]
        return [f"READY queue_depth={data.get('queue_depth')} paused={data.get('paused')}"]
    return [f"HTTP_{status}"]


PROBES = {"models": probe_models, "embedder": probe_embedder, "gci": probe_gci}


def probe_endpoints():
    state = {}
    for name, (url, kind) in ENDPOINTS.items():
        result = PROBES[kind](url)
        state[name] = result
    return state


def probe_nodes():
    return {name: ssh_docker(host) for name, host in NODES.items()}


def ssh_docker(host):
    try:
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
    for old in sorted(SNAP_DIR.glob("*.json"))[:-168]:
        old.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
