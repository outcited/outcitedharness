#!/usr/bin/env python3
"""intake_daemon.py — continuous new-doc pipeline for extraction boxes.

Polls factory sources, drains new blobs, L0-classifies, queues, burns when
the box is free, and ships manifests to m5's fold enrichment store.

Laws honored:
- sha-dedupe (same blob never processed twice; CORPUS.md law 1)
- L0 front door (harness.electronics.l0.classify_document; fail-closed unknown)
- memory law: burns at 16 workers on 32T/29GB boxes
- never fabricates: no manifest -> empty source_url, fold skips
"""
import json, os, subprocess, sys, time, hashlib
from pathlib import Path

HOME = Path.home()
INTAKE = HOME / "intake"
SPOOL = INTAKE / "spool"
MANIFESTS = INTAKE / "manifests"
QUEUES = INTAKE / "queues" / "pending"
LOGS = INTAKE / "logs"
STATE = INTAKE / "state.json"
LOCK = INTAKE / "burn.lock"
POLL_S = 900
BURN_WORKERS = 16

SOURCES = [
    ("macmini_t7", "samsonkim@100.80.101.116:mnt/t7/spool/tmp-drain/"),
    ("happy_macmobile", "samsonkim@100.69.201.109:/mnt/macmobile/corpus-deliveries/"),
]
M5 = "samkim@100.106.13.24"

sys.path.insert(0, str(HOME / "Harnessv1"))
from harness.electronics.l0 import classify_document, extension_class  # noqa: E402


def log(msg):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOGS / "daemon.log", "a") as fh:
        fh.write(line + "\n")


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"seen_shas": [], "seen_files": {}, "drained_batches": [], "burns": []}


def save_state(st):
    st["seen_shas"] = st["seen_shas"][-200000:]
    STATE.write_text(json.dumps(st, indent=1))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def classify(pdf_path):
    """L0 front door: extension first, then content, fail-closed unknown."""
    import pymupdf
    fn = os.path.basename(pdf_path)
    ext = extension_class(fn)
    if ext:
        return ext, "extension"
    try:
        doc = pymupdf.open(pdf_path)
        front = " ".join((doc[i].get_text() or "") for i in range(min(3, doc.page_count)))
        doc.close()
        if len(front.strip()) < 80:
            return "image_only", "no_text_layer"
        return classify_document(front[:8000], fn, ""), "content"
    except Exception:
        return "unknown", "fail_closed"


def poll_sources(st):
    for name, src in SOURCES:
        dst = SPOOL / name
        dst.mkdir(parents=True, exist_ok=True)
        rc, out = sh(f"rsync -a --ignore-existing {src} {dst}/", timeout=1800)
        if rc != 0:
            log(f"rsync {name} rc={rc}: {out.strip()[:160]}")
    new_batches = []
    for name, _ in SOURCES:
        for d in sorted((SPOOL / name).glob("*")):
            if d.is_dir() and not d.name.startswith(".") and d.name not in st["drained_batches"]:
                st["drained_batches"].append(d.name)
                new_batches.append(f"{name}/{d.name}")
    return new_batches


def harvest(st):
    """New PDFs only: fast (size,mtime) skip, then sha dedupe. Also flat-copy manifests."""
    seen_shas = set(st["seen_shas"])
    docs = 0
    out_docs = []
    for root, dirs, files in os.walk(SPOOL):
        for fn in files:
            p = Path(root) / fn
            if fn == "corpus-delivery-manifest.jsonl":
                dst = MANIFESTS / f"{Path(root).name}-manifest.jsonl"
                if not dst.exists():
                    dst.write_text(p.read_text(errors="replace"))
                continue
            if not fn.lower().endswith(".pdf") or fn.startswith("._"):
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            if stat.st_size == 0:
                continue
            fkey = f"{stat.st_mtime_ns}:{stat.st_size}"
            if st["seen_files"].get(str(p)) == fkey:
                continue
            st["seen_files"][str(p)] = fkey
            sha = sha256_file(p)
            if sha in seen_shas:
                continue
            seen_shas.add(sha)
            st["seen_shas"].append(sha)
            cls, src = classify(p)
            out_docs.append({"source_path": str(p), "doc_class": cls,
                             "class_source": src, "sha256": sha})
            docs += 1
    return out_docs, docs


def burn_if_free(st):
    # single-burn-per-box law: never stack on ANY running burn_pass
    rc, _ = sh("pgrep -f burn_pass.py")
    if rc == 0:
        return
    # retire finished burns (.burning -> .done, lock cleared)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
            os.kill(pid, 0)
            return
        except (ProcessLookupError, ValueError):
            LOCK.unlink()
            for b in QUEUES.parent.glob("pending/*.burning"):
                b.rename(b.with_suffix(".done"))
    pending = sorted(QUEUES.glob("*.json"))
    if not pending:
        return
    qf = pending[0]
    out_dir = HOME / "lane-out" / "burn-intake" / qf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"burn start {qf.name} -> {out_dir} ({BURN_WORKERS}w)")
    rc, out = sh(
        f"cd {HOME}/Harnessv1 && nohup .venv/bin/python scripts/burn_pass.py "
        f"--queue {qf} --out-dir {out_dir} --workers {BURN_WORKERS} "
        f"--doc-timeout 60 --resume > {out_dir}/run.log 2>&1 & echo $!")
    try:
        pid = int(out.strip().splitlines()[-1])
        LOCK.write_text(str(pid))
        st["burns"].append({"queue": qf.name, "pid": pid,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        qf.rename(qf.with_suffix(".burning"))
    except (ValueError, IndexError):
        log(f"burn launch failed: {out[:200]}")


def main():
    for d in (SPOOL, MANIFESTS, QUEUES, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    log("intake daemon start (poll %ss)" % POLL_S)
    st = load_state()
    while True:
        try:
            batches = poll_sources(st)
            if batches:
                log(f"drained: {batches}")
            docs, n = harvest(st)
            if docs:
                qf = QUEUES / f"intake-{time.strftime('%Y%m%dT%H%M%S')}.json"
                qf.write_text(json.dumps({"documents": docs}))
                from collections import Counter
                log(f"queued {n} docs -> {qf.name} classes={dict(Counter(d['doc_class'] for d in docs))}")
                rc, out = sh(f"rsync -a --ignore-existing {MANIFESTS}/ {M5}:lane-out/intake-manifests/", timeout=180)
                if rc != 0:
                    log(f"manifest push to m5 rc={rc} (will retry next poll)")
            burn_if_free(st)
            save_state(st)
        except Exception as e:  # noqa: BLE001 — daemon must survive
            log(f"ERROR {type(e).__name__}: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
