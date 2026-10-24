#!/usr/bin/env python3
"""Probe live fleet state (endpoints + node containers), snapshot it, mail deltas.

Truth is derived, never declared: every run re-reads the services themselves
(/healthz, /health, /v1/models) and docker ps.
Snapshots: /Volumes/M5_4TB/fleet-state/snapshots/  Changes: CHANGES.md + mail to the
canonical agent inbox on the NAS (see inbox_root; the M5 tree is frozen).

A probe never reports an empty list. "Reachable but not serving what we expect"
is recorded as HTTP_<code>, because that is the failure mode that hides: the
e10b embedder 404s on /v1/models while healthy, and stock bge-m3 on asus2 404s
on /healthz. Both used to be indistinguishable from silence.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get(
    "FLEET_STATE_ROOT", "/Volumes/M5_4TB/fleet-state"))
# 2026-10-21: the NAS tree is owned by uid 1026 (_lpoperator) and not
# group-writable by samkim, which silently killed the hourly job from
# 2026-10-12 (PermissionError on CHANGES.md append). FLEET_STATE_ROOT moves
# the writable state (snapshots + CHANGES.md) to a samkim-owned directory;
# the NAS copy stays as frozen history.
SNAP_DIR = ROOT / "snapshots"
CHANGES = ROOT / "CHANGES.md"

# Canonical agent inbox = the NAS share (Sam directive 2026-10-08, see
# /Volumes/M5_4TB/agent-inbox/MOVED.md). The M5 tree is FROZEN and this probe
# kept writing change-mails into it after the freeze (2026-10-09 08:39Z and
# 17:24Z), so the frozen path is deliberately not a fallback here: if the share
# is not mounted the mail spools under ~/Logs and the run says so out loud.
NAS_INBOX = Path.home() / "Volumes/Electronicdataassets/agent-inbox"
SPOOL = Path.home() / "Logs/fleet-state-mail-spool"
MAIL_BOXES = ("cursor-cr", "m5-opencode", "m4opencode")


def inbox_root():
    """(write_root, note). AGENT_INBOX overrides; NAS wins; frozen M5 never."""
    override = os.environ.get("AGENT_INBOX")
    if override:
        return Path(override), ""
    if NAS_INBOX.is_dir():
        return NAS_INBOX, ""
    return SPOOL, " (NAS inbox NOT mounted -> spooled to ~/Logs/fleet-state-mail-spool; frozen M5 tree not written)"

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
    # extraction/coordination boxes (Sam: "why isn't seven telling me shit"
    # — 2026-10-23: kloey had 1,084 OOM events in 48h and no watcher saw it,
    # because these boxes were never probed at all)
    "kloey": "samkim@192.168.4.68",
    "winter": "samkim@100.91.13.94",
    "happy": "samsonkim@100.69.201.109",
    "seven": "samsonkim@100.89.168.75",
}

# OS-health thresholds; breach => health event line in the delta (feeds
# Seven's classifier) + full numbers in the snapshot JSON. Alerts are
# diff-based: a persistent condition alerts once, re-alerts on worsening.
HEALTH_CMD = (
    "mem_tot=$(free -m | awk '/^Mem:/{print $2}'); "
    "mem_avail=$(free -m | awk '/^Mem:/{print $7}'); "
    "swap_tot=$(free -m | awk '/^Swap:/{print $2}'); "
    "swap_used=$(free -m | awk '/^Swap:/{print $3}'); "
    "disk=$(df -P / | awk 'NR==2{gsub(/%/,\"\"); print $5}'); "
    "load5=$(awk '{print $2}' /proc/loadavg); "
    "cores=$(nproc); "
    "oom=$(journalctl -k --since \"48 hours ago\" 2>/dev/null | "
    "grep -ciE 'oom|out of memory' || echo 0); "
    "echo \"$mem_avail $mem_tot $swap_used $swap_tot $disk $load5 $cores $oom\""
)

def node_health(host):
    """One SSH: (ok?, health_str, alerts). None on SSH failure."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
             HEALTH_CMD],
            capture_output=True, text=True, timeout=25)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        parts = r.stdout.split()
        mem_avail, mem_tot = int(parts[0]), int(parts[1])
        swap_used, swap_tot = int(parts[2]), int(parts[3])
        disk, load5, cores, oom = int(parts[4]), float(parts[5]), int(parts[6]), int(parts[7])
        alerts = []
        # low % alone is normal on model-serving boxes (weights+cache pin
        # most RAM); alert only when BOTH low-% and under an absolute floor.
        if mem_tot and mem_avail / mem_tot < 0.10 and mem_avail < 8000:
            alerts.append(f"mem_available={mem_avail}MB ({100*mem_avail//mem_tot}%)")
        if swap_tot and swap_used / swap_tot > 0.50:
            alerts.append(f"swap={100*swap_used//swap_tot}%")
        if disk > 90:
            alerts.append(f"disk={disk}%")
        if cores and load5 > 1.5 * cores:
            alerts.append(f"load5={load5:.1f}/{cores}c")
        if oom > 20:
            alerts.append(f"oom_48h={oom}")
        h = (f"mem_avail={mem_avail}MB/{mem_tot}MB swap={swap_used}/{swap_tot}MB "
             f"disk={disk}% load5={load5:.1f}/{cores}c oom48h={oom}")
        return h, alerts
    except Exception:
        return None


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
    """Containers per node + OS health. Health alerts ride the delta so
    Seven's classifier sees them (fleet-infra-change); numbers go to the
    snapshot."""
    out = {}
    for name, host in NODES.items():
        out[name] = {"services": ssh_docker(host)}
        h = node_health(host)
        if h is None:
            if out[name]["services"] == ["SSH_FAIL"]:
                out[name]["health"] = "SSH_FAIL"
            continue
        out[name]["health"], alerts = h
        if alerts:
            out[name]["health_alerts"] = alerts
    return out


def health_delta_lines(probe):
    """Alert lines for the CHANGES feed, only for boxes in breach."""
    lines = []
    for name, d in sorted(probe.items()):
        for a in d.get("health_alerts", []):
            lines.append(f"{name}-health: [ok] -> [ALERT {a}]")
    return lines


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
    root, note = inbox_root()
    index = root.parent / "INDEX.jsonl" if root == NAS_INBOX else None
    wrote = []
    for box in MAIL_BOXES:
        name = f"{slug}_fleet-state-probe_{box}_status_fleet-change.md"
        try:
            d = root / box
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(body)
            wrote.append(str(d / name))
        except OSError:
            # read-only mirror (NFS): spool locally instead of dying —
            # the CHANGES feed already carries the event either way.
            SPOOL.mkdir(parents=True, exist_ok=True)
            (SPOOL / name).write_text(body)
            wrote.append(str(SPOOL / name))
        if index is not None:
            row = {
                "ts": stamp,
                "from": "fleet-state-probe",
                "to": box,
                "type": "status",
                "id": f"fleet-change-{slug}",
                "subject": subject,
                "path": f"agent-inbox/{box}/{name}",
            }
            try:
                with index.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError:
                pass
    return note


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
        # OS-health alerts ride alongside service deltas; diff semantics
        # unchanged (a persistent condition alerts once, re-alerts on change).
        prev_alerts = {n for n in latest.get("health_alerts_seen", [])}
        cur_alerts = set(health_delta_lines(state["nodes"]))
        delta = delta + sorted(cur_alerts - prev_alerts)
        state["health_alerts_seen"] = sorted(cur_alerts)
        if delta:
            entry = f"\n## {stamp}\n" + "\n".join(f"- {d}" for d in delta) + "\n"
            with CHANGES.open("a") as f:
                f.write(entry)
            note = mail(delta, stamp)
            print(f"CHANGED:\n{entry}" + note)
        else:
            print(f"OK: no change ({stamp})")
            # Heartbeat: a quiet fleet is legitimate, but downstream freshness
            # watchers (Director fleet_changes feed) read CHANGES.md mtime.
            # Append ONE dated line per calendar day maximum so a steady fleet
            # never reads as a dead probe.
            today = now.strftime("%Y-%m-%d")
            last_lines = CHANGES.read_text().splitlines()[-5:] if CHANGES.exists() else []
            if not any(l.startswith(f"- heartbeat {today}") for l in last_lines):
                with CHANGES.open("a") as f:
                    f.write(f"\n## {stamp}\n- heartbeat {today}: probe ran, no fleet changes\n")
    path = SNAP_DIR / (now.strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(state, indent=2, sort_keys=True))
    for old in sorted(SNAP_DIR.glob("*.json"))[:-168]:
        old.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
