#!/usr/bin/env python3
"""emit-extraction-telemetry.py — harness.extraction-telemetry.v1 for burn boxes.

Read-only: derives everything from /proc, burn out-dir ledgers, and running
processes. Writes ~/extraction-telemetry/<machine_id>.json per
docs/SEVEN-TELEMETRY-CONTRACT.md. No secrets, no dispatch — observe only.

Lane accounting: for each ~/lane-out/burn-*/ledger.jsonl, total = queue docs
(from the matching *-queue.json if present, else ledger rows), completed =
ledger rows, running = detected burn_pass worker processes.
"""
import json, os, glob, re, subprocess, time
from pathlib import Path

HOME = Path.home()
LANE_OUT = HOME / "lane-out"
OUT_DIR = HOME / "extraction-telemetry"
MACHINE_ID = os.uname().nodename


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


def hardware():
    threads = os.cpu_count() or 0
    ram = None
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                ram = int(line.split()[1]) // 1024
                break
    except OSError:
        pass
    disk = None
    try:
        st = os.statvfs(HOME)
        disk = round(st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        pass
    return {"cpu_threads": threads, "ram_total_mb": ram, "disk_total_gb": disk}


def utilization():
    cpu_pct = ram_used = None
    load = None
    try:
        idle = sum(float(x) for x in sh("cat /proc/loadavg").split()[:3])
        load = idle
    except Exception:
        pass
    try:
        parts = sh("top -bn1 | head -5").splitlines()
        for l in parts:
            if "Cpu(s)" in l:
                cpu_pct = 100 - float(re.search(r"([\d.]+)\s*id", l).group(1))
    except Exception:
        pass
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                total = ram_total_mb = None
                ram_used = None
                break
    except Exception:
        pass
    try:
        mi = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            mi[k] = int(v.split()[0]) // 1024
        ram_used = mi.get("MemTotal", 0) - mi.get("MemAvailable", 0)
    except Exception:
        pass
    disk_used = None
    try:
        du = sh("df -BG --output=used " + str(HOME) + " | tail -1").strip()
        disk_used = int(du.rstrip("G")) if du else None
    except Exception:
        pass
    return {"cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else None,
            "ram_used_mb": ram_used, "disk_used_gb": disk_used, "load_average": load}


def services():
    out = []
    try:
        ps = sh("ps aux | grep -E 'burn_pass|extract_' | grep -v grep") or ""
    except Exception:
        ps = ""
    for line in ps.splitlines()[:8]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        cmd = parts[10]
        m = re.search(r"burn_pass\.py --queue (\S+)", cmd)
        name = os.path.basename(m.group(1)).replace("-queue.json", "") if m else cmd[:60]
        out.append({"name": f"burn-{name}", "status": "active",
                    "workers": int(parts[1] and 0) or None, "restarts": None})
    # worker process count per queue (cheap, accurate)
    if ps:
        for svc in out:
            q = svc["name"].replace("burn-", "")
            n = len([l for l in ps.splitlines() if f"{q}-queue" in l])
        # fallthrough: workers from ps lines matching queue name
    return out


def lanes():
    out = []
    for led in sorted(glob.glob(str(LANE_OUT / "burn-*" / "ledger.jsonl"))):
        lane = os.path.basename(os.path.dirname(led))[len("burn-"):]
        total = completed = None
        try:
            with open(led) as fh:
                completed = sum(1 for _ in fh)
        except OSError:
            continue
        q = HOME / f"{lane}-queue.json"
        if not q.exists():
            cands = glob.glob(str(HOME / "*queue*.json"))
            q = next((c for lane in [lane] for c in cands if lane in c), None)
        if q and os.path.exists(q):
            try:
                d = json.load(open(q))
                docs = d.get("documents") or d.get("items") or d
                total = len(docs) if isinstance(docs, list) else None
            except Exception:
                pass
        out.append({"name": lane, "extraction_type": "burn_pass",
                    "queue_source": str(q) if q else None,
                    "queue_items_total": total,
                    "queue_items_completed": completed,
                    "last_progress": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(led))),
                    "status": "active" if completed else "empty"})
    return out


def main():
    rec = {
        "schema": "harness.extraction-telemetry.v1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine_id": MACHINE_ID,
        "machine_role": "extraction",
        "hardware": hardware(),
        "utilization": utilization(),
        "services": services(),
        "feeder": {"heartbeat": None, "status": None},
        "lanes": lanes(),
        "downstream": {"note": "burn box — consolidation happens on m5"},
        "telemetry_status": "partial",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{MACHINE_ID}.json"
    path.write_text(json.dumps(rec, indent=1))
    print("wrote", path)


if __name__ == "__main__":
    main()
