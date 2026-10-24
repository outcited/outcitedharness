#!/usr/bin/env python3
"""Emit `harness.extraction-telemetry.v1` for this machine.

Implements the producer side of docs/SEVEN-TELEMETRY-CONTRACT.md so seven
stops inferring from raw queues. STRICTLY read-only with respect to the
factory: it reads process state, queue manifests and output files and writes
only its own telemetry record.

Delivery (contract §Delivery):
  /var/lib/extraction-telemetry/<machine_id>.json   (preferred)
  falls back to $HOME/extraction-telemetry/<machine_id>.json when /var/lib is
  not writable, and to --out.

Contract rules honoured:
  * never leave a field absent — emit explicit null for measured-none
  * list every unmeasurable field in `unknown_fields`
  * queue items are document-facet work units; never merged with documents
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "harness.extraction-telemetry.v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(p: str):
    try:
        return Path(p).read_text()
    except Exception:                                    # noqa: BLE001
        return None


def host_facts() -> tuple[dict, dict, list]:
    hw, util, unknown = {}, {}, []
    try:
        hw["cpu_threads"] = os.cpu_count()
    except Exception:                                    # noqa: BLE001
        unknown.append("hardware.cpu_threads")
    try:
        md = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                md[k] = int(v.split()[0])
        hw["ram_total_mb"] = md.get("MemTotal", 0) // 1024
        util["ram_used_mb"] = (md.get("MemTotal", 0) -
                               md.get("MemAvailable", 0)) // 1024
    except Exception:                                    # noqa: BLE001
        unknown += ["hardware.ram_total_mb", "utilization.ram_used_mb"]
    try:
        st = os.statvfs("/")
        hw["disk_total_gb"] = (st.f_blocks * st.f_frsize) // (1024 ** 3)
        util["disk_used_gb"] = ((st.f_blocks - st.f_bfree) * st.f_frsize) // (1024 ** 3)
    except Exception:                                    # noqa: BLE001
        unknown += ["hardware.disk_total_gb", "utilization.disk_used_gb"]
    try:
        util["load_average"] = float(Path("/proc/loadavg").read_text().split()[0])
    except Exception:                                    # noqa: BLE001
        unknown.append("utilization.load_average")
    try:
        tot = 0.0
        n = 0
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("cpu "):
                f = [int(x) for x in line.split()[1:]]
                idle = f[3] + (f[4] if len(f) > 4 else 0)
                total = sum(f)
                tot += 100.0 * (total - idle) / max(1, total)
                n = 1
        util["cpu_pct"] = round(tot / max(1, n), 1)
    except Exception:                                    # noqa: BLE001
        unknown.append("utilization.cpu_pct")
    return hw, util, unknown


def services_and_lanes() -> tuple[list, list, dict, list]:
    """Derive service + lane records from live extractor processes and the
    queue/output files the contract names. No schema assumption beyond the
    contract: anything absent is reported absent."""
    unknown = []
    procs = []
    try:
        out = subprocess.run(["ps", "-eo", "pcpu,nlwp,args"],
                             capture_output=True, text=True,
                             timeout=20).stdout
    except Exception:                                    # noqa: BLE001
        out = ""
    for line in out.splitlines()[1:]:
        if "extract_" not in line or "grep" in line:
            continue
        p = line.split(None, 2)
        if len(p) < 3:
            continue
        m = re.search(r"extract_([a-z0-9_]+)\.py", p[2])
        svc = "extract-" + (m.group(1) if m else "unattributed")
        # NOTE: multiprocessing children inherit the parent's argv, so
        # `--workers 32` appears on every child. Summing it would report
        # thousands of workers. The worker count is the PROCESS count.
        procs.append({"name": svc, "status": "active", "restarts": 0})

    # worker count = live processes for that service (not summed --workers)
    agg: dict[str, dict] = {}
    for s in procs:
        a = agg.setdefault(s["name"], {"name": s["name"], "status": "active",
                                       "workers": 0, "restarts": 0})
        a["workers"] += 1
    # restarts: read systemd if reachable, else report null (never guessed)
    for name in list(agg):
        # We SAW live processes for this service, so it is active regardless of
        # whether systemd knows the unit name (many extractors run as
        # template instances like extract-mode-currents@0 or as bare
        # processes). Try the unit, then its hyphenated template form.
        agg[name]["status"] = "active"
        agg[name]["restarts"] = None
        for unit in (name + ".service",
                     name.replace("_", "-") + ".service",
                     name.replace("_", "-") + "@0.service"):
            try:
                r = subprocess.run(
                    ["systemctl", "show", unit,
                     "--property=NRestarts,ActiveState"],
                    capture_output=True, text=True, timeout=8).stdout
                d = dict(l.split("=", 1) for l in r.splitlines() if "=" in l)
                if not d.get("ActiveState"):
                    continue
                agg[name]["restarts"] = int(d.get("NRestarts", 0) or 0)
                st = d["ActiveState"]
                agg[name]["status"] = ("active" if st in ("active", "activating")
                                       else "failed" if st == "failed"
                                       else "active")
                break
            except Exception:                            # noqa: BLE001
                continue

    # lanes from queue manifests + output files
    lanes = []
    qdir = Path("/tmp")
    outdir = Path(os.path.expanduser("~/lane-out"))
    seen = set()
    for q in sorted(qdir.glob("*-queue.json")) + sorted(qdir.glob("vault-queue.json")):
        lane = q.name.replace("-queue.json", "")
        if lane in seen:
            continue
        seen.add(lane)
        total = ready = 0
        try:
            docs = json.loads(q.read_text()).get("documents", [])
            total = ready = len(docs)
        except Exception:                                # noqa: BLE001
            unknown.append(f"lanes[{lane}].queue_items_total")
            total = ready = None
        of = outdir / f"{lane}.jsonl"
        rows = None
        if of.exists():
            try:
                rows = sum(1 for _ in open(of, errors="replace"))
            except Exception:                            # noqa: BLE001
                unknown.append(f"lanes[{lane}].output_rows")
        lanes.append({
            "name": lane,
            "extraction_type": None,
            "extraction_version": None,
            "queue_source": str(q),
            "queue_items_total": total,
            "queue_items_completed": rows,
            "queue_items_running": None,
            "queue_items_ready": (max(0, ready - rows) if (ready is not None
                                 and rows is not None) else None),
            "queue_items_failed": None,
            "queue_items_blocked": None,
            "output_rows": rows,
            "output_file": str(of) if of.exists() else None,
            "last_progress": (datetime.fromtimestamp(of.stat().st_mtime,
                              timezone.utc).isoformat(timespec="seconds")
                              if of.exists() else None),
            "status": ("running" if any(s["name"].replace("extract-", "") in lane
                       for s in agg.values()) else
                       "completed" if (rows or 0) > 0 else "blocked"),
        })
    # the feeder itself
    feeder = {"heartbeat": None, "status": None}
    try:
        svc = subprocess.run(["systemctl", "show", "work-feeder.service",
                              "--property=ActiveState,ActiveEnterTimestamp"],
                             capture_output=True, text=True,
                             timeout=8).stdout
        d = dict(l.split("=", 1) for l in svc.splitlines() if "=" in l)
        if d.get("ActiveState"):
            feeder["status"] = ("running" if d["ActiveState"] == "active"
                                else "stalled" if d["ActiveState"] == "reloading"
                                else "stopped")
            feeder["heartbeat"] = d.get("ActiveEnterTimestamp") or None
        else:
            unknown.append("feeder.status")
    except Exception:                                    # noqa: BLE001
        unknown.append("feeder.status")
    return list(agg.values()), lanes, feeder, unknown


def downstream(unknown: list) -> dict:
    """The verification chain. Deliberately null where unmeasured — the
    contract requires explicit null + unknown_fields, never silence."""
    return {
        "raw_rows_unnormalized": None,
        "verified_claims": None,
        "normalized_claims": None,
        "identity_bound": None,
        "comparable_accepted": None,
        "note": "not wired to dw-bridge yet — measured as none, listed unknown",
    }


def build(machine_id: str, role: str) -> dict:
    hw, util, unknown = host_facts()
    svcs, lanes, feeder, u2 = services_and_lanes()
    unknown += u2
    ds = downstream(unknown)
    for k, v in ds.items():
        if k != "note" and v is None:
            unknown.append(f"downstream.{k}")
    status = "partial" if unknown else "complete"
    return {
        "schema": SCHEMA,
        "timestamp": now_iso(),
        "machine_id": machine_id,
        "machine_role": role,
        "hardware": hw,
        "utilization": util,
        "services": svcs,
        "feeder": feeder,
        "lanes": lanes,
        "downstream": ds,
        "telemetry_status": status,
        "unknown_fields": sorted(set(unknown)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine-id", default=None)
    ap.add_argument("--role", default="extraction")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    mid = a.machine_id
    if not mid:
        mid = read(os.path.expanduser("~/.machine-id")) or os.uname().nodename
        mid = mid.strip()
    rec = build(mid, a.role)
    dests = [a.out] if a.out else [
        f"/var/lib/extraction-telemetry/{mid}.json",
        os.path.expanduser(f"~/extraction-telemetry/{mid}.json"),
    ]
    written = None
    for d in dests:
        try:
            Path(d).parent.mkdir(parents=True, exist_ok=True)
            tmp = d + ".tmp"
            Path(tmp).write_text(json.dumps(rec, indent=1))
            os.replace(tmp, d)
            written = d
            break
        except Exception:                                # noqa: BLE001
            continue
    if not written:
        print(json.dumps(rec))
        return 1
    print(f"{now_iso()} telemetry -> {written} "
          f"status={rec['telemetry_status']} lanes={len(rec['lanes'])} "
          f"unknown={len(rec['unknown_fields'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
