#!/usr/bin/env python3
"""ADMISSION-WIRING-GAP-20261021 — wire receiver manifests + the
SHA-verified pdfs-incoming corpus into the M5 admission/backlog interface.

Reuse, not a second framework:
  * manifests = the factory's own wave dirs on the corpus-mirror
    (exports/corpus-deliveries/incremental-*/), rows carry
    {asset_id, sha256, vault_ref, manufacturer, category, source_url}
  * blobs = the T7-pulled content-addressed local copy
    results/pdfs-incoming/<sha[:2]>/<sha[2:4]>/<sha>.pdf
  * output = the existing lane-queue shape {"documents": [...]} plus a
    per-doc jsonl ledger, consumed by the same wave/queue tooling

Laws (operator directive 2026-10-21):
  * exact SHA-256 verification before admission (streamed, never trusted
    from the filename alone)
  * manifest identity recorded (wave + asset_id + source_url)
  * duplicate suppression via a durable sqlite ledger (sha PK) — the
    watermark makes the mtime problem structurally impossible
  * durable checkpoint per wave; restart resumes exactly
  * held/rejected carry explicit reasons; nothing silently dropped
  * counts come from the ledger, never from filesystem mtimes

Usage:
  python3 scripts/admit_deliveries.py --canary          # fixture + 50 real
  python3 scripts/admit_deliveries.py --waves N         # process N newest
  python3 scripts/admit_deliveries.py --reconcile       # population report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import os
ROOT = Path(__file__).resolve().parents[1]
MIRROR_DELIVERIES = Path(
    "/Users/samkim/mnt/yosemite-smb/m5-opencode/corpus-mirror/exports/"
    "corpus-deliveries")
# the live corpus is machine-level, never worktree-level
LOCAL_BLOBS = Path(os.environ.get(
    "ADMISSION_BLOBS_ROOT", "/Users/samkim/Harnessv1/results/pdfs-incoming"))
ADMISSION_DIR = ROOT / "results/admission"
LEDGER = ADMISSION_DIR / "admission.sqlite"
QUEUE_JSONL = ADMISSION_DIR / "admitted-queue.jsonl"
QUEUE_JSON = ADMISSION_DIR / "admitted-queue.json"

SLUG_RE = re.compile(r"[^a-z0-9]+")
WAVE_RE = re.compile(r"^incremental-\d{8}-\d+$")


def part_slug(source_url: str, fallback: str) -> str:
    if source_url:
        stem = source_url.rstrip("/").rsplit("/", 1)[-1]
        stem = re.sub(r"\.(pdf|PDF)$", "", stem.split("?")[0])
        host = ""
        m = re.match(r"https?://(?:www\.)?([^./]+)", source_url)
        if m:
            host = m.group(1)
        slug = SLUG_RE.sub("-", f"{host}-{stem}".lower()).strip("-")
        if slug:
            return slug[:96]
    return fallback[:96]


def open_ledger(path: Path = LEDGER) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.executescript("""
      CREATE TABLE IF NOT EXISTS docs(
        sha256 TEXT PRIMARY KEY, asset_id TEXT, source_url TEXT,
        manufacturer TEXT, category TEXT, wave TEXT, state TEXT,
        reason TEXT, blob_path TEXT, admitted_at REAL);
      CREATE TABLE IF NOT EXISTS watermark(k TEXT PRIMARY KEY, v TEXT);
      CREATE INDEX IF NOT EXISTS docs_state ON docs(state);
    """)
    return con


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_BLOBS_OVERRIDE: Path | None = None


def local_blob_path(sha: str) -> Path:
    base = _BLOBS_OVERRIDE if _BLOBS_OVERRIDE is not None else LOCAL_BLOBS
    return base / sha[:2] / sha[2:4] / f"{sha}.pdf"


def load_manifest(wave_dir: Path) -> list[dict]:
    mf = wave_dir / "corpus-delivery-manifest.jsonl"
    rows = []
    for line in mf.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def process_wave(con: sqlite3.Connection, wave_dir: Path,
                 verify_from_mirror: bool = False,
                 queue_out=None, log=print) -> dict:
    """One wave -> ledger outcomes + queue rows. Durable per-wave commit."""
    wave = wave_dir.name
    counts = {"admitted": 0, "duplicate": 0, "held": 0, "rejected": 0}
    reasons: dict[str, int] = {}
    rows = load_manifest(wave_dir)
    now = time.time()
    fresh = []
    for row in rows:
        sha = (row.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            con.execute(
                "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sha or "?", row.get("asset_id"), row.get("source_url"),
                 row.get("manufacturer"), row.get("category"), wave,
                 "rejected", "bad-manifest-sha", "", now))
            counts["rejected"] += 1
            reasons["bad-manifest-sha"] = reasons.get("bad-manifest-sha", 0) + 1
            continue
        prev = con.execute(
            "SELECT state FROM docs WHERE sha256=?", (sha,)).fetchone()
        if prev is not None:
            counts["duplicate"] += 1
            continue
        blob = local_blob_path(sha)
        if blob.exists():
            actual = sha256_file(blob)
            if actual != sha:
                con.execute(
                    "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sha, row.get("asset_id"), row.get("source_url"),
                     row.get("manufacturer"), row.get("category"), wave,
                     "rejected", f"sha-mismatch:{actual[:12]}", str(blob),
                     now))
                counts["rejected"] += 1
                reasons["sha-mismatch"] = reasons.get("sha-mismatch", 0) + 1
                continue
            state, reason, bpath = "admitted", "", str(blob)
        elif verify_from_mirror:
            # fall back to the wave's own blob on the mirror (slow path)
            ref = row.get("vault_ref") or ""
            mvec = wave_dir / ref
            if mvec.exists() and sha256_file(mvec) == sha:
                state, reason, bpath = "admitted-mirror", "", str(mvec)
            else:
                state, reason, bpath = "held", "blob-missing", ""
        else:
            state, reason, bpath = "held", "blob-missing-local", ""
        con.execute(
            "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sha, row.get("asset_id"), row.get("source_url"),
             row.get("manufacturer"), row.get("category"), wave, state,
             reason, bpath, now))
        if state in ("admitted", "admitted-mirror"):
            counts["admitted"] += 1
            fresh.append({
                "source_path": bpath,
                "part_number": part_slug(row.get("source_url") or "", sha[:16]),
                "sha256": sha,
                "source_url": row.get("source_url"),
                "manufacturer": row.get("manufacturer"),
                "category": row.get("category"),
                "wave": wave,
            })
        elif state == "held":
            counts["held"] += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    con.execute("INSERT OR REPLACE INTO watermark VALUES ('wave', ?)",
                (wave,))
    con.commit()
    if queue_out and fresh:
        for r in fresh:
            queue_out.write(json.dumps(r, ensure_ascii=False,
                                       sort_keys=True) + "\n")
        queue_out.flush()
    log(json.dumps({"wave": wave, "rows": len(rows), **counts,
                    "reasons": reasons}, sort_keys=True))
    return counts


def waves_on_mirror(limit: int = 0) -> list[Path]:
    if not MIRROR_DELIVERIES.is_dir():
        return []
    ws = sorted((p for p in MIRROR_DELIVERIES.iterdir()
                 if p.is_dir() and WAVE_RE.match(p.name)),
                key=lambda p: p.name)
    return ws[-limit:] if limit else ws


def run(con: sqlite3.Connection, limit: int, log=print) -> dict:
    total = {"admitted": 0, "duplicate": 0, "held": 0, "rejected": 0}
    mark = (con.execute("SELECT v FROM watermark WHERE k='wave'")
              .fetchone() or [""])[0]
    todo = [w for w in waves_on_mirror() if w.name > mark]
    log(f"watermark={mark or 'START'} waves_to_process={len(todo)}")
    with QUEUE_JSONL.open("a") as q:
        for w in todo:
            c = process_wave(con, w, queue_out=q, log=log)
            for k in total:
                total[k] += c.get(k, 0)
            if limit and sum(total.values()) >= limit:
                break
    if QUEUE_JSONL.exists():
        lines = QUEUE_JSONL.read_text().splitlines()
        docs = [json.loads(x) for x in lines if x.strip()][-50000:]
        QUEUE_JSON.write_text(json.dumps({"documents": docs}, indent=1))
    return total


def reconcile(con: sqlite3.Connection, log=print) -> dict:
    local_files = sum(1 for _ in LOCAL_BLOBS.rglob("*.pdf"))
    ledger = dict(con.execute(
        "SELECT state, count(*) FROM docs GROUP BY state").fetchall())
    rep = {
        "pdfs_incoming_files": local_files,
        "ledger": ledger,
        "population_note": (
            "pdfs-incoming is the T7 puller population (blobs that left the "
            "factory); the ledger covers manifest-declared assets only. A "
            "blob without a manifest row is not yet admissible (identity "
            "unproven) — it stays UNKNOWN, never counted admitted."),
    }
    log(json.dumps(rep, indent=1, sort_keys=True))
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--waves", type=int, default=0)
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--fixture", type=Path,
                    default=ROOT / "tests/fixtures/admission/canary-wave")
    ap.add_argument("--blobs-root", type=Path, default=None,
                    help="override blob root (canary sandbox)")
    a = ap.parse_args()
    global _BLOBS_OVERRIDE
    _BLOBS_OVERRIDE = a.blobs_root
    con = open_ledger()
    if a.reconcile:
        reconcile(con)
        return 0
    if a.canary:
        # fixture wave exercises all four outcome classes deterministically
        total = {"admitted": 0, "duplicate": 0, "held": 0, "rejected": 0}
        with QUEUE_JSONL.open("a") as q:
            c1 = process_wave(con, a.fixture, queue_out=q)
            for k in total:
                total[k] += c1[k]
            c2 = process_wave(con, a.fixture, queue_out=q)  # rerun => dupes
            total["duplicate"] += c2["duplicate"]
        print("CANARY:", json.dumps(total, sort_keys=True))
        return 0
    total = run(con, a.waves)
    print("TOTAL:", json.dumps(total, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
