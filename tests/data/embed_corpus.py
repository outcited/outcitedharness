"""Embed the corpus and measure whether vectors beat lexical — DIRECTOR-06 §2.

Read-only client against the existing asus4:8800 BGE-M3 service. Throttled and
batched; the service is the SOLE adjudication host and must not be loaded.
If the service is unreachable this exits without touching the index — lexical
retrieval continues to work.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/tmp/verify-05d")
from director_consumer.memory import ProjectMemory

EMBED_URL = "http://100.100.116.82:8800/v1/embeddings"
BATCH = 16
SLEEP = 0.15           # throttled: never hammer the sole adjudication host
DB = "/tmp/mem-eval/memory.db"


def embed(texts: list[str]) -> list[list[float]] | None:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        body = {"model": "bge-m3", "input": chunk}
        req = urllib.request.Request(
            EMBED_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError) as e:
            print("embedder unavailable:", e)
            return None
        rows = sorted(d.get("data", []), key=lambda x: x.get("index", 0))
        out.extend([row["embedding"] for row in rows])
        time.sleep(SLEEP)
    return out


def main() -> int:
    mem = ProjectMemory(DB, project_id="designwins",
                        authorized_projects={"designwins", "payroll"})
    rows = mem.con.execute(
        "SELECT id, title, body FROM mem WHERE embedding IS NULL").fetchall()
    print("records needing embeddings:", len(rows))
    texts = [((r["title"] or "") + "\n" + (r["body"] or ""))[:2000]
             for r in rows]
    t0 = time.time()
    vecs = embed(texts)
    if vecs is None:
        print("VECTOR PATH DISABLED (embedder unreachable) — lexical only")
        mem.close()
        return 2
    for r, v in zip(rows, vecs):
        mem.con.execute("UPDATE mem SET embedding=? WHERE id=?",
                        (json.dumps(v), r["id"]))
    mem.con.commit()
    print("embedded %d records in %.1fs (%.0f ms avg)"
          % (len(vecs), time.time() - t0,
             1000 * (time.time() - t0) / max(1, len(vecs))))
    print("counts:", mem.counts())
    mem.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
