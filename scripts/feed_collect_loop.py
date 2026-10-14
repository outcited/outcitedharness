#!/usr/bin/env python3
"""Feed + collect daemon v2: step-traced, alarm-guarded (NFS hang-proof)."""
import os, sys, time, shutil, signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/samkim/Harnessv1")
sys.path.insert(0, "/Users/samkim/Harnessv1/scripts")
os.environ["PIPELINE_DB"] = "/Users/samkim/Harnessv1/results/pipeline-live.db"
from harness.pipeline import store

IN = Path.home() / "extract-jobs/incoming"
OUT = Path.home() / "extract-jobs/out"
ERR = Path.home() / "extract-jobs/err"
DEST = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/dispatch/collected/m5")

class StepTimeout(Exception):
    pass

def _alarm(signum, frame):
    raise StepTimeout()

signal.signal(signal.SIGALRM, _alarm)

def collect(con):
    DEST.mkdir(parents=True, exist_ok=True)
    for j in OUT.glob("*.json"):
        try:
            shutil.move(str(j), str(DEST / j.name))
        except OSError:
            pass
    acked = 0
    for jf in DEST.glob("*.json"):
        row = con.execute(
            "SELECT id FROM jobs WHERE kind='substrate' AND corpus_key='pdf:'||? AND state='claimed'",
            (jf.stem,)).fetchone()
        if not row:
            continue
        try:
            rec = __import__("json").loads(jf.read_text())
        except Exception:
            continue
        store.ack(con, row["id"], rec.get("extractor", "extract_worker"),
                  rec.get("extractor_version", "?"), jf.read_text(),
                  document_sha256=rec.get("sha256"), page_label_map=rec.get("page_labels"))
        acked += 1
    return acked

def feed(con):
    jobs = store.claim(con, "dispatch-m5-feed", kinds=("substrate",), limit=120)
    def one(j):
        try:
            shutil.copy2(j["source_path"], str(IN / os.path.basename(j["source_path"])))
        except OSError as e:
            store.fail(con, j["id"], f"copy: {e}")
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(one, jobs))
    for p0 in IN.glob("*.pdf"):
        if p0.stat().st_size == 0:
            p0.unlink()
    return len(jobs)

def main():
    con = store.connect()
    print("feed-collect v2 up", flush=True)
    while True:
        try:
            signal.alarm(120)
            n = collect(con)
            signal.alarm(0)
            if n:
                print(f"acked {n}", flush=True)
        except StepTimeout:
            signal.alarm(0)
            print("collect TIMEOUT (nfs?)", flush=True)
        except Exception as e:
            signal.alarm(0)
            print(f"collect err: {str(e)[:80]}", flush=True)
        try:
            signal.alarm(120)
            if len(list(IN.glob("*.pdf"))) < 120:
                n = feed(con)
                if n:
                    print(f"fed {n}", flush=True)
            signal.alarm(0)
        except StepTimeout:
            signal.alarm(0)
            print("feed TIMEOUT (nfs?)", flush=True)
        except Exception as e:
            signal.alarm(0)
            print(f"feed err: {str(e)[:80]}", flush=True)
        time.sleep(2)

if __name__ == "__main__":
    main()
