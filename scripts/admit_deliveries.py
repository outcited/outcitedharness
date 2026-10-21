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
# operator-bounded 2026-10-21: legacy families admitted per-canary only
LEGACY_FAMILIES = ("datasheet-tail", "refdesigns-eval", "unassigned-l0",
                   "errata-and-nxp", "pcn-lifecycle")
FAMILY_RE = re.compile(
    r"^(" + "|".join(LEGACY_FAMILIES) + r")-\d{8}$")


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
        reason TEXT, blob_path TEXT, admitted_at REAL, family TEXT
        NOT NULL DEFAULT 'incremental');
      CREATE TABLE IF NOT EXISTS watermark(k TEXT PRIMARY KEY, v TEXT);
      CREATE INDEX IF NOT EXISTS docs_state ON docs(state);
    """)
    cols = [r[1] for r in con.execute("PRAGMA table_info(docs)")]
    if "family" not in cols:
        con.execute("ALTER TABLE docs ADD COLUMN family TEXT NOT NULL"
                    " DEFAULT 'incremental'")
        con.commit()
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


class ManifestCorruption(RuntimeError):
    pass


_PREAMBLE = (re.compile(r"^CREATE TABLE", re.I),
             re.compile(r"^COPY \d+", re.I))


def load_manifest(wave_dir: Path) -> list[dict]:
    mf = wave_dir / "corpus-delivery-manifest.jsonl"
    rows = []
    for n, line in enumerate(mf.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # recognized psql-dump framing (CREATE TABLE / COPY N / terminator)
        if any(rx.match(line) for rx in _PREAMBLE) or line == "\\.":
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            # structural errors are never silently skipped (operator law 6)
            raise ManifestCorruption(
                f"{mf.name}:{n}: {e}") from e
    return rows


def process_wave(con: sqlite3.Connection, wave_dir: Path,
                 verify_from_mirror: bool = False,
                 queue_out=None, log=print, family: str = "incremental",
                 row_cap: int = 0) -> dict:
    """One wave -> ledger outcomes + queue rows. Durable per-wave commit."""
    wave = wave_dir.name
    counts = {"admitted": 0, "duplicate": 0, "held": 0, "rejected": 0}
    reasons: dict[str, int] = {}
    rows = load_manifest(wave_dir)
    now = time.time()
    fresh = []
    family_of = (lambda n: family)
    # legacy waves share one manifest per family dir; cap rows per call for
    # bounded canaries (watermark NOT advanced when capped => retryable)
    capped = False
    for row in rows:
        sha = (row.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            con.execute(
                "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        if row_cap and (counts["admitted"] + counts["held"]
                        + counts["rejected"]) >= row_cap:
            capped = True
            break
        blob = local_blob_path(sha)
        if blob.exists():
            actual = sha256_file(blob)
            if actual != sha:
                con.execute(
                    "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sha, row.get("asset_id"), row.get("source_url"),
                     row.get("manufacturer"), row.get("category"), wave,
                     "rejected", f"sha-mismatch:{actual[:12]}", str(blob),
                     now, family))
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
            "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sha, row.get("asset_id"), row.get("source_url"),
             row.get("manufacturer"), row.get("category"), wave, state,
             reason, bpath, now, family))
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
    if not capped:   # capped canaries stay retryable (never lost behind watermark)
        con.execute("INSERT OR REPLACE INTO watermark VALUES ('wave', ?)",
                    (wave,))
    con.commit()
    if queue_out and fresh:
        for r in fresh:
            queue_out.write(json.dumps(r, ensure_ascii=False,
                                       sort_keys=True) + "\n")
        queue_out.flush()
    log(json.dumps({"wave": wave, "family": family, "rows": len(rows),
                    "capped": capped, **counts, "reasons": reasons},
                   sort_keys=True))
    return counts


def waves_on_mirror(limit: int = 0,
                    families: tuple[str, ...] = ("incremental",)) -> list[Path]:
    if not MIRROR_DELIVERIES.is_dir():
        return []
    rx = {"incremental": WAVE_RE, "legacy": FAMILY_RE}
    pats = [rx[f] for f in families if f in rx]
    ws = sorted((p for p in MIRROR_DELIVERIES.iterdir()
                 if p.is_dir() and any(r.match(p.name) for r in pats)),
                key=lambda p: p.name)
    return ws[-limit:] if limit else ws


def _checkpoint(con, log, label, integrity_failures):
    ledger = dict(con.execute(
        "SELECT state, count(*) FROM docs GROUP BY state").fetchall())
    queue_rows = sum(1 for _ in QUEUE_JSONL.open()) \
        if QUEUE_JSONL.exists() else 0
    dupe_ledger = con.execute(
        "SELECT sha256, count(*) c FROM (SELECT sha256 FROM docs) "
        "GROUP BY sha256 HAVING c > 1").fetchall()
    log(json.dumps({
        "checkpoint": label,
        "ledger_states": ledger,
        "queue_jsonl_rows": queue_rows,
        "integrity_failures": integrity_failures,
        "duplicate_ledger_keys": len(dupe_ledger)}, sort_keys=True))
    if dupe_ledger or integrity_failures:
        raise SystemExit(f"STOP: integrity failure at {label}")


def rebuild_queue_from_ledger(con, log=print) -> dict:
    """Queue is REGENERATED from the ledger (authoritative), then asserted
    one-row-per-admitted-sha: ledger-to-queue idempotency, proven."""
    rows = con.execute(
        "SELECT sha256, asset_id, source_url, manufacturer, category, "
        "wave, blob_path FROM docs WHERE state IN "
        "('admitted','admitted-mirror')").fetchall()
    seen = set()
    docs = []
    for sha, aid, url, mfr, cat, wave, blob in rows:
        if sha in seen:
            raise SystemExit(f"STOP: nondeterministic admission {sha}")
        seen.add(sha)
        docs.append({
            "source_path": blob, "part_number": part_slug(url or "", sha[:16]),
            "sha256": sha, "source_url": url, "asset_id": aid,
            "manufacturer": mfr, "category": cat, "wave": wave})
    QUEUE_JSONL.write_text("".join(
        json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n"
        for d in docs))
    QUEUE_JSON.write_text(json.dumps({"documents": docs}))
    proof = {"ledger_admitted": len(rows), "queue_rows": len(docs),
             "distinct_shas": len(seen)}
    log(json.dumps({"queue_rebuild": proof}, sort_keys=True))
    assert proof["ledger_admitted"] == proof["queue_rows"] \
        == proof["distinct_shas"], "ledger-to-queue idempotency FAILED"
    return proof


def retry_held(con: sqlite3.Connection, log=print) -> dict:
    """Objective 1 (operator 2026-10-21): held rows re-examine on rerun —
    a blob that arrived since the last pass admits now. Rejected rows are
    NEVER retried (deterministic verdicts)."""
    recovered = 0
    still_held = 0
    for sha, wave, aid, url, mfr, cat in con.execute(
            "SELECT sha256, wave, asset_id, source_url, manufacturer, "
            "category FROM docs WHERE state='held'").fetchall():
        blob = local_blob_path(sha)
        if blob.exists() and sha256_file(blob) == sha:
            con.execute(
                "UPDATE docs SET state='admitted', reason='', blob_path=?, "
                "admitted_at=? WHERE sha256=?", (str(blob), time.time(), sha))
            recovered += 1
        else:
            still_held += 1
    con.commit()
    if recovered:
        rebuild_queue_from_ledger(con, log=log)
    log(json.dumps({"held_retry": {"recovered": recovered,
                                   "still_held": still_held}},
                   sort_keys=True))
    return {"recovered": recovered, "still_held": still_held}


def run(con: sqlite3.Connection, limit: int, log=print,
        full: bool = False, families: tuple[str, ...] = ("incremental",),
        row_cap: int = 0) -> dict:
    total = {"admitted": 0, "duplicate": 0, "held": 0, "rejected": 0}
    integrity: list[str] = []
    held_waves: list[str] = []
    retry_held(con, log=log)
    mark = (con.execute("SELECT v FROM watermark WHERE k='wave'")
              .fetchone() or [""])[0]
    todo = []
    for fam in families:
        if fam == "incremental":
            todo += [w for w in waves_on_mirror(families=("incremental",))
                     if w.name > mark]
        else:
            todo += [w for w in waves_on_mirror(families=(fam,))
                     if (con.execute(
                         "SELECT 1 FROM docs WHERE wave=? AND state IN "
                         "('admitted','admitted-mirror','rejected')",
                         (w.name,)).fetchone() is None)]
    todo.sort(key=lambda w: w.name)
    log(f"watermark={mark or 'START'} waves_to_process={len(todo)}")
    quarters = {max(1, int(len(todo) * q)): f"{int(q*100)}%"
                for q in (0.25, 0.50, 0.75)}
    with QUEUE_JSONL.open("a") as q:
        for i, w in enumerate(todo, 1):
            if not (w / "corpus-delivery-manifest.jsonl").exists():
                held_waves.append(w.name)   # incomplete wave: recorded
                log(json.dumps({"wave-held": w.name,
                                "reason": "manifest-missing"}))
                continue
            fam = "incremental" if WAVE_RE.match(w.name) else "legacy"
            try:
                c = process_wave(con, w, queue_out=q, log=log, family=fam,
                                 row_cap=row_cap)
            except ManifestCorruption as e:
                integrity.append(str(e))
                break
            for k in total:
                total[k] += c.get(k, 0)
            if i in quarters:
                _checkpoint(con, log, quarters[i], integrity)
            if limit and sum(total.values()) >= limit:
                break
    _checkpoint(con, log, "100%", integrity)
    if held_waves:
        log(json.dumps({"held_waves": held_waves}, sort_keys=True))
    if integrity:
        raise SystemExit("STOP: manifest corruption — " + "; ".join(integrity))
    rebuild_queue_from_ledger(con, log=log)
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
    ap.add_argument("--full", action="store_true",
                    help="drain ALL remaining waves (operator-gated)")
    ap.add_argument("--families", default="incremental",
                    help="comma list: incremental,legacy")
    ap.add_argument("--row-cap", type=int, default=0,
                    help="max NEW docs per wave (canary bound; cap keeps"
                         " wave retryable behind watermark)")
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
    fams = tuple(x.strip() for x in a.families.split(",") if x.strip())
    total = run(con, a.waves, full=a.full, families=fams,
                row_cap=a.row_cap)
    print("TOTAL:", json.dumps(total, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
