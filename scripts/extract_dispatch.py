#!/usr/bin/env python3
"""Extraction dispatcher v3 — SQLite queue semantics (harness.pipeline.store).

Jobs live in pipeline.db: claim/ack, visibility timeout, retry, dead-letter.
The dispatcher bridges queue -> workers (rsync spool), collects results into
the store, and fails jobs whose files landed in worker err/ dirs.
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.pipeline import store

# pipeline.db on NFS carries a stale fcntl lock AND refuses new-file writes to
# this uid (sqlite-on-NFS rot + the transport law, 2026-10-12). The live queue
# is the same data on local disk — the dispatcher is its only writer; workers
# never touch it (they consume the rsync spool).
os.environ["PIPELINE_DB"] = "/Users/samkim/Harnessv1/results/pipeline-live.db"  # FORCE: launchd env overrides setdefault

RESULTS = Path("/Volumes/M5_4TB/extract-results")
# The charley mount is RO for this uid (transport failure, 2026-10-12) —
# watermarks and collected outputs land locally; consumers read both.
WRITABLE = Path("/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/dispatch")  # NAS yosemiteraid5 .4.44 (single NIC since 2026-10-13)
# Single-NIC per NAS since 2026-10-13 (dual-NIC on eero caused ARP flapping).
# No alternate NIC paths — reads via .4.54, writes via .4.44.
WRITABLE_ALT = WRITABLE
CORPUS_ALT_ROOT = None


def _rr(paths):
    """Round-robin over paths that exist; yield one per call."""
    live = [p for p in paths if p and Path(p).exists()]
    if not live:
        live = [paths[0]]
    i = 0
    while True:
        yield live[i % len(live)]
        i += 1


def _alternator():
    """Thread-safe 0/1 flip for spreading load across two parallel NAS NICs."""
    import threading
    lock = threading.Lock()
    box = [0]

    def nxt():
        with lock:
            v = box[0]
            box[0] ^= 1
            return v

    return nxt


def writable_alt_live(host="192.168.4.40", cache=[0.0, False]):
    """Fast reachability check for the alt (NIC 2) write server.

    Pings the server IP; never touches the mount. Cached 60s. Returns False on
    an IP conflict / dead NIC so we fall back to the primary write path."""
    now = time.time()
    if now - cache[0] < 60:
        return cache[1]
    rc, _ = sh(f"ping -c1 -t1 {host} >/dev/null 2>&1 && echo up")
    cache[0], cache[1] = now, rc == 0
    return cache[1]

CORPUS_PATHS = [Path(x) for x in [
    "/Volumes/macbookM4-4TB/datasheet-corpus/mcu",
    "/Volumes/macbookM4-4TB/datasheets",
    "/Volumes/M5_4TB/vault/landing",
    "/Volumes/M5_4TB/vault/cas",
    "/Volumes/M5_4TB/exports/power-datasheet-pairs",
    "/Volumes/M5_4TB/exports/corpus-deliveries",
    # NVMe mirror (yosemiteraid5): Sam directive 2026-10-14 — the PDF factory
    # lands new deliveries here as well; corpus_key dedupe makes double-scan
    # safe (same sha-stem never enqueues twice).
    "/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/corpus-mirror/exports/corpus-deliveries",
    "/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/corpus-mirror/cas",
    "/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/corpus-mirror/landing",
]]
BATCH = 480
SCAN_INTERVAL = 21600  # seconds between full corpus rescans (6h); full walk over the
# flaky .4.54 NFS is expensive and the queue is fully enqueued — rescans buy nothing
# until the factory delivers new blobs. Lower this to force a fresh scan.
REMOTE_WORKERS = {
    "asus4": "samkimasus4@100.100.116.82",
    "asus2": "samkimasus2@100.68.133.1",
}
ASUS2_TRAINING_CONTAINER = "lora-power-v1"
WORKER_VENV_PY = "~/extract-venv/bin/python"


def sh(cmd, timeout=120):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def scan_and_enqueue(con):
    # Idempotent, content-addressed scan: walk every corpus root, and enqueue any
    # PDF whose sha-key isn't already in the jobs table. mtime watermarks are
    # NOT used to skip files — a delivery can gain sharded blobs (blobs/XX/YY/)
    # without a dir mtime change, which silently dropped fresh PDFs. The DB
    # existence check makes re-walking correct; a per-file stat guards cost.
    new_jobs = 0
    for root in CORPUS_PATHS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(".pdf"):
                    continue
                p = os.path.join(dirpath, fn)
                corpus_key = f"pdf:{Path(p).stem}"
                exists = con.execute(
                    "SELECT 1 FROM jobs WHERE kind='substrate' AND corpus_key=?",
                    (corpus_key,),
                ).fetchone()
                if not exists:
                    store.enqueue(con, "substrate", corpus_key, source_path=p)
                    new_jobs += 1
    return new_jobs


def worker_running(host=None):
    if host is None:
        rc, out = sh("pgrep -fl extract_worker.py | wc -l")
        return rc == 0 and out.strip().isdigit() and int(out) > 0
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} 'pgrep -f extract_worker.py | wc -l'")
    return rc == 0 and out.strip().isdigit() and int(out) > 0


def start_worker(host=None, name=None):
    if host is None:
        sh("nohup /Users/samkim/Harnessv1/.venv/bin/python "
           "/Users/samkim/Harnessv1/scripts/extract_worker.py "
           ">> /Users/samkim/Harnessv1/results/worker-m5.log 2>&1 < /dev/null &")
    else:
        sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} "
           f"'setsid nohup {WORKER_VENV_PY} ~/extract_worker.py "
           f">> ~/extract-jobs/worker.log 2>&1 < /dev/null & echo started'")
        if name == "asus4":
            import time as _t
            _t.sleep(3)
            sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} "
               f"'for pid in $(pgrep -f extract_worker.py); do renice -n 15 -p $pid >/dev/null; done'")
    print(f"started worker on {host or 'm5'}", flush=True)


def asus2_free():
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {REMOTE_WORKERS['asus2']} "
                 f"\"free -g | awk 'NR==2{{print \\\$7}}'\"")
    if rc != 0:
        return False
    try:
        return int(out.strip()) > 20
    except ValueError:
        return False


def incoming_count(host=None):
    if host is None:
        return len(glob.glob(os.path.expanduser("~/extract-jobs/incoming/*.pdf")))
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} 'ls ~/extract-jobs/incoming/*.pdf 2>/dev/null | wc -l'")
    return int(out) if rc == 0 and out.strip().isdigit() else 999


def push_batch(host, jobs):
    files = " ".join(f"'{j['source_path']}'" for j in jobs if os.path.exists(j["source_path"]))
    if files:
        sh(f"rsync -a --timeout=300 {files} {host}:extract-jobs/incoming/", timeout=600)


def _alt_read_path(p):
    """Single-NIC per NAS since 2026-10-13: the .4.62 read-alt is gone, so reads
    always use the primary path (never touch a dead mount)."""
    return p


def push_batch_local(jobs):
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    rr = _alternator()

    def _copy(j):
        use_alt = rr() == 1
        src = _alt_read_path(j["source_path"]) if use_alt else j["source_path"]
        try:
            shutil.copy2(src, os.path.expanduser(f"~/extract-jobs/incoming/{os.path.basename(j['source_path'])}"))
        except OSError:
            try:
                shutil.copy2(j["source_path"], os.path.expanduser(f"~/extract-jobs/incoming/{os.path.basename(j['source_path'])}"))
            except OSError:
                pass

    with ThreadPoolExecutor(32) as pool:
        list(pool.map(_copy, jobs))


def remote_list(host, which):
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} 'ls ~/extract-jobs/{which}/ 2>/dev/null'")
    return out.split() if rc == 0 else []


def collect_and_ack(con, host, name):
    dest = WRITABLE / "collected" / name
    dest.mkdir(parents=True, exist_ok=True)
    # Second NAS NIC write path is OPTIONAL. Only use it if its server answers
    # a fast ping — never stat() a mount whose server may be down (it hangs).
    dest_alt = dest
    if writable_alt_live():
        dest_alt = WRITABLE_ALT / "collected" / name
        dest_alt.mkdir(parents=True, exist_ok=True)
    if host is None:
        import shutil
        from concurrent.futures import ThreadPoolExecutor
        moved = list((Path.home() / "extract-jobs" / "out").glob("*.json"))
        rr = _alternator()

        def _mv(j):
            target = dest_alt if rr() == 1 else dest
            try:
                shutil.move(str(j), target / j.name)
            except OSError:
                pass

        with ThreadPoolExecutor(32) as pool:
            list(pool.map(_mv, moved))
        # Both mount paths see the same export; dedup by name to avoid double-ack.
        seen = set()
        fresh = []
        for f in [dest / j.name for j in moved] + [dest_alt / j.name for j in moved]:
            if f.name not in seen and f.exists():
                seen.add(f.name)
                fresh.append(f)
        errs = [p.name for p in (Path.home() / "extract-jobs" / "err").glob("*.pdf")]
    else:
        sh(f"rsync -a --remove-source-files --timeout=300 {host}:extract-jobs/out/ {dest}/", timeout=600)
        errs = [Path(e).name for e in remote_list(host, "err")]
        fresh = list(dest.glob("*.json"))
    acked = failed = 0
    for jf in fresh:
        stem = jf.stem
        job = con.execute("SELECT id FROM jobs WHERE kind='substrate' AND corpus_key='pdf:'||? AND state='claimed'",
                          (stem,)).fetchone()
        if not job:
            continue
        try:
            rec = json.loads(jf.read_text())
        except Exception:
            continue
        store.ack(con, job["id"], rec.get("extractor", "extract_worker"),
                  rec.get("extractor_version", "?"), jf.read_text(),
                  document_sha256=rec.get("sha256"),
                  page_label_map=rec.get("page_labels"))
        acked += 1
    for err_stem in errs:
        job = con.execute("SELECT id FROM jobs WHERE kind='substrate' AND corpus_key='pdf:'||? AND state IN ('claimed','pending')",
                          (Path(err_stem).stem,)).fetchone()
        if job:
            store.fail(con, job["id"], "worker could not open file")
            failed += 1
    return acked, failed


def main():
    WRITABLE.mkdir(parents=True, exist_ok=True)
    Path.home().joinpath("extract-jobs/incoming").mkdir(parents=True, exist_ok=True)
    sh("cp /Users/samkim/Harnessv1/scripts/extract_worker.py /tmp/extract_worker.py")
    for host in REMOTE_WORKERS.values():
        sh(f"scp -o BatchMode=yes /tmp/extract_worker.py {host}:extract_worker.py", timeout=60)
    con = store.connect()
    pending = con.execute("SELECT COUNT(*) c FROM jobs WHERE state IN ('pending','claimed')").fetchone()["c"]
    print(f"dispatcher v3 (sqlite) up: open jobs={pending}", flush=True)
    _last_scan = [time.time()]  # don't rescan on startup; queue is fully enqueued
    while True:
        t0 = time.time()
        try:
            # Rescan only occasionally AND only when the queue is drained — the
            # full NFS walk is slow and pointless while we have live work.
            new_jobs = 0
            drain = con.execute(
                "SELECT COUNT(*) c FROM jobs WHERE state IN ('pending','claimed')"
            ).fetchone()["c"]
            if drain == 0 and time.time() - _last_scan[0] >= SCAN_INTERVAL:
                new_jobs = scan_and_enqueue(con)
                _last_scan[0] = time.time()
            targets = [("m5", None)]
            for name, host in REMOTE_WORKERS.items():
                if name == "asus2" and not asus2_free():
                    continue
                if not worker_running(host):
                    start_worker(host, name)
                targets.append((name, host))
            if not worker_running(None):
                start_worker(None)
            acked = failed = 0
            for name, host in targets:
                a, f = collect_and_ack(con, host, name)
                acked += a
                failed += f
            for name, host in targets:
                if incoming_count(host) > BATCH:
                    continue
                jobs = store.claim(con, f"dispatch-{name}", kinds=("substrate",), limit=BATCH)
                if jobs:
                    if host is None:
                        push_batch_local(jobs)
                    else:
                        push_batch(host, jobs)
            stats = con.execute(
                "SELECT state, COUNT(*) n FROM jobs GROUP BY state"
            ).fetchall()
            dead = con.execute("SELECT COUNT(*) c FROM jobs WHERE state='dead'").fetchone()["c"]
            state_str = ", ".join("{}:{}".format(r[0], r[1]) for r in stats)
            print(f"cycle {time.time()-t0:.1f}s new={new_jobs} acked={acked} failed={failed} "
                  f"states=[{state_str}] dead={dead}", flush=True)
        except Exception as e:
            print(f"cycle error: {e}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
