#!/usr/bin/env python3
"""rip_pass — fleet page-index ripper: every doc -> per-page lane index.

One pass per document (open-1x law): TOC section map, per-page lane tags
(sanctioned LANE_PATTERNS), gated find_tables (only where pin/ball signals
fire). Emits compact per-doc records — the document-universe index that
knives are exposed FROM.

Scheduling: multiprocessing.Pool + pidfile SIGKILL on deadline (a hung
pymupdf call owns at most its own slot; Pool respawns it). Resume by sha.
Path resolution per layout: flat2 (cas/{2}/{sha}.pdf), deep2
(cas/{2}/{2}/{sha}.pdf), trunc (happy-style 31-char names).

usage:
  python3 rip_pass.py --index INDEX.jsonl --root /path/to/cas \
      --out-dir ~/pageindex --shard 1/3 --workers 28 --layout flat2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal as _signal
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.page_index import LANE_PATTERNS  # noqa: E402

LANES = {name: list(pats) for name, pats in LANE_PATTERNS.items()}
import re  # noqa: E402


def rip_document(pdf_path: Path) -> dict:
    t0 = time.time()
    data = pdf_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    doc = pymupdf.open(stream=data, filetype="pdf")
    n_pages = doc.page_count

    toc = doc.get_toc(simple=True)
    sec_by_page: dict[int, str] = {}
    if toc:
        stack: list[tuple[int, str]] = []
        cursor = 1
        entries = sorted(toc, key=lambda e: e[2])
        for lvl, title, page in entries:
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, title.strip()))
            sec = " > ".join(t for _, t in stack[-3:])
            for p in range(cursor, min(page, n_pages + 1)):
                sec_by_page[p] = sec
            cursor = max(cursor, page)

    pages_out = []
    lanes_total: Counter = Counter()
    t_tables = 0.0
    for pno in range(n_pages):
        page = doc[pno]
        text = page.get_text("text")
        lanes = []
        excerpt = None
        for name, pats in LANES.items():
            for pat in pats:
                m = pat.search(text)
                if m:
                    lanes.append(name)
                    lanes_total[name] += 1
                    if excerpt is None:
                        s = max(0, m.start() - 40)
                        excerpt = " ".join(text[s:m.end() + 60].split())[:120]
                    break
        n_tables = 0
        if any(l in ("pin_or_ball", "pin_semantics") for l in lanes):
            t1 = time.time()
            try:
                n_tables = len(page.find_tables().tables)
            except Exception:
                n_tables = -1
            t_tables += time.time() - t1
        rec = {"p": pno + 1, "lanes": lanes, "t": n_tables, "c": len(text)}
        if sec_by_page.get(pno + 1):
            rec["sec"] = sec_by_page[pno + 1][-60:]
        if excerpt:
            rec["ex"] = excerpt
        pages_out.append(rec)
    doc.close()
    return {
        "sha": sha,
        "pages": n_pages,
        "lanes_total": dict(lanes_total),
        "table_pages": sum(1 for r in pages_out if r["t"]),
        "table_ms": round(t_tables * 1000),
        "wall_ms": round((time.time() - t0) * 1000),
        "pages_rip": pages_out,
    }


def _worker_tracked(payload):
    """Module-level (picklable): publish pid, rip, return record."""
    sha, path, out_path = payload
    (Path("/tmp/rip-pids") / sha[:16]).write_text(str(os.getpid()))
    try:
        rec = rip_document(Path(path))
    finally:
        (Path("/tmp/rip-pids") / sha[:16]).unlink(missing_ok=True)
    if out_path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rec, ensure_ascii=False))
    return {"sha": sha, "pages": rec["pages"], "lanes_total": rec["lanes_total"],
            "wall_ms": rec["wall_ms"], "out": str(out)}


def resolve(sha: str, root: Path, layout: str) -> Path | None:
    cands = {
        "flat2": [root / sha[:2] / f"{sha}.pdf", root / sha[:2] / f"{sha[2:4]}" / f"{sha}.pdf",
                  root / sha[:2] / sha, root / sha[:2] / sha[:31], root / sha[:2] / f"{sha[:31]}.pdf"],
    }[layout if layout in ("flat2",) else "flat2"]
    for c in cands:
        if c.exists():
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", required=True, help="corpus INDEX.jsonl (sha + locations)")
    ap.add_argument("--root", required=True, help="cas root on THIS box")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--doc-timeout", type=float, default=120.0)
    ap.add_argument("--layout", default="flat2", choices=["flat2"])
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    args.index = Path(args.index)
    args.root = Path(args.root)
    args.out_dir = Path(args.out_dir)

    idx, total = (int(x) for x in args.shard.split("/"))
    rows = []
    with open(args.index) as f:
        for i, line in enumerate(f):
            if i % total != idx:
                continue
            o = json.loads(line)
            rows.append(o["sha"])
    done: set[str] = set()
    if args.resume:
        for rec in Path(args.out_dir).glob("*.json"):
            done.add(rec.stem)
    todo = [s for s in rows if s not in done]
    print(f"[rip] shard {args.shard}: {len(rows)} shas, {len(done)} done, {len(todo)} to rip", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pid_dir = Path("/tmp/rip-pids")
    pid_dir.mkdir(exist_ok=True)

    pool = Pool(processes=args.workers)
    inflight: dict = {}
    from collections import deque

    queue_docs = deque(todo)
    kill_budget = args.doc_timeout * 2
    tally: Counter = Counter()
    t_start = time.time()

    def submit(sha: str, attempt: int):
        path = resolve(sha, Path(args.root), args.layout)
        token = f"{sha[:16]}.{attempt}.{os.getpid()}"
        if path is None:
            _record(sha, {"sha": sha, "error": "not_local"}, token, attempt)
            return
        payload = (sha, str(path), str(args.out_dir / f"{sha}.json"))
        inflight[token] = {"sha": sha, "attempt": attempt, "t0": time.time(),
                           "res": pool.apply_async(_worker_tracked, (payload,))}

    def _record(sha: str, rec: dict, token: str, attempt: int):
        tally[rec.get("error", "ok") if "error" in rec else "ok"] += 1
        inflight.pop(token, None)
        (pid_dir / token).unlink(missing_ok=True)

    while queue_docs or inflight:
        while queue_docs and len(inflight) < args.workers * 2:
            sha = queue_docs.popleft()
            if sha in done:
                continue
            att = attempts.get(sha, 0)
            if att >= 2:
                tally["timeout"] += 1
                attempts[sha] = 3
                continue
            submit(sha, att)
        for token in list(inflight.keys()):
            e = inflight[token]
            if e["res"].ready():
                try:
                    r = e["res"].get()
                    done.add(r["sha"])
                    tally["ok"] += 1
                    tally["pages"] += r["pages"]
                except Exception as exc:  # noqa: BLE001
                    sha = e["sha"]
                    attempts[sha] = e["attempt"] + 1
                    if attempts[sha] < 2:
                        queue_docs.append(sha)
                    else:
                        tally["error"] += 1
                inflight.pop(token, None)
        now = time.time()
        for token in list(inflight.keys()):
            e = inflight[token]
            if now - e["t0"] > kill_budget:
                pf = pid_dir / token
                if pf.exists():
                    try:
                        os.kill(int(pf.read_text().strip()), _signal.SIGKILL)
                    except (ProcessLookupError, ValueError):
                        pass
                sha = e["sha"]
                attempts[sha] = e["attempt"] + 1
                if attempts[sha] < 2:
                    queue_docs.append(sha)
                else:
                    tally["timeout"] += 1
                inflight.pop(token, None)
                tally["timeout_first_pass"] += 1
        if tally and int(time.time() - t_start) % 60 < 6:
            el = time.time() - t_start
            done_n = sum(v for k, v in tally.items() if k in ("ok", "timeout", "error"))
            print(f"[rip] {done_n}/{len(todo)}  {done_n/el*3600:.0f}/h  inflight={len(inflight)}  {dict(tally)}",
                  flush=True)
        time.sleep(2)

    pool.terminate()
    pool.join()
    print(json.dumps({"shard": args.shard, "tally": dict(sorted(tally.items())),
                      "wall_s": round(time.time() - t_start, 1)}, indent=1))
    return 0


attempts: dict = {}

if __name__ == "__main__":
    sys.exit(main())
