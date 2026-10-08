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

RESULTS = Path("/Volumes/M5_4TB/extract-results")
CORPORA = [
    "/Volumes/macbookM4-4TB/datasheet-corpus/mcu",
    "/Volumes/macbookM4-4TB/datasheets",
    "/Volumes/M5_4TB/vault/landing",
    "/Volumes/M5_4TB/vault/cas",
    "/Volumes/M5_4TB/exports/power-datasheet-pairs",
]
BATCH = 40
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
    watermarks = {}
    try:
        watermarks = json.loads((RESULTS / ".scan-watermarks.json").read_text())
    except Exception:
        pass
    new_jobs = 0
    for root in CORPORA:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dir_m = os.path.getmtime(dirpath)
            fresh = dir_m > watermarks.get(dirpath, 0)
            if fresh:
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
                watermarks[dirpath] = dir_m
            dirnames[:] = [
                d for d in dirnames
                if os.path.getmtime(os.path.join(dirpath, d)) > watermarks.get(os.path.join(dirpath, d), 0)
                or fresh
            ]
    (RESULTS / ".scan-watermarks.json").write_text(json.dumps(watermarks))
    return new_jobs


def worker_running(host=None):
    if host is None:
        rc, out = sh("pgrep -fl extract_worker.py | wc -l")
        return rc == 0 and out.strip().isdigit() and int(out) > 0
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} 'pgrep -f extract_worker.py | wc -l'")
    return rc == 0 and out.strip().isdigit() and int(out) > 0


def start_worker(host=None):
    if host is None:
        sh("nohup /Users/samkim/Harnessv1/.venv/bin/python "
           "/Users/samkim/Harnessv1/scripts/extract_worker.py "
           ">> /Volumes/M5_4TB/extract-results/worker-m5.log 2>&1 < /dev/null &")
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


def push_batch_local(jobs):
    import shutil
    for j in jobs:
        try:
            shutil.copy2(j["source_path"], os.path.expanduser(f"~/extract-jobs/incoming/{os.path.basename(j['source_path'])}"))
        except OSError:
            pass


def remote_list(host, which):
    rc, out = sh(f"ssh -o BatchMode=yes -o ConnectTimeout=8 {host} 'ls ~/extract-jobs/{which}/ 2>/dev/null'")
    return out.split() if rc == 0 else []


def collect_and_ack(con, host, name):
    dest = RESULTS / "collected" / name
    dest.mkdir(parents=True, exist_ok=True)
    if host is None:
        import shutil
        moved = list((Path.home() / "extract-jobs" / "out").glob("*.json"))
        for j in moved:
            shutil.move(str(j), dest / j.name)
        errs = [p.name for p in (Path.home() / "extract-jobs" / "err").glob("*.pdf")]
    else:
        sh(f"rsync -a --remove-source-files --timeout=300 {host}:extract-jobs/out/ {dest}/", timeout=600)
        errs = [Path(e).name for e in remote_list(host, "err")]
    acked = failed = 0
    for jf in dest.glob("*.json"):
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
    RESULTS.mkdir(parents=True, exist_ok=True)
    Path.home().joinpath("extract-jobs/incoming").mkdir(parents=True, exist_ok=True)
    sh("cp /Users/samkim/Harnessv1/scripts/extract_worker.py /tmp/extract_worker.py")
    for host in REMOTE_WORKERS.values():
        sh(f"scp -o BatchMode=yes /tmp/extract_worker.py {host}:extract_worker.py", timeout=60)
    con = store.connect()
    pending = con.execute("SELECT COUNT(*) c FROM jobs WHERE state IN ('pending','claimed')").fetchone()["c"]
    print(f"dispatcher v3 (sqlite) up: open jobs={pending}", flush=True)
    while True:
        t0 = time.time()
        try:
            new_jobs = scan_and_enqueue(con)
            targets = [("m5", None)]
            for name, host in REMOTE_WORKERS.items():
                if name == "asus2" and not asus2_free():
                    continue
                if not worker_running(host):
                    start_worker(host)
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
        time.sleep(60)


if __name__ == "__main__":
    main()
