#!/usr/bin/env python3
"""Capture soak metrics for the M3 Rust-vs-Python factory comparison.

Sources (all read-only):
- per-stem JSON rows from each soak output root
- the Python soak's _report.json + progress log
- the Rust factory DB (factory_chunks + factory_events) for orchestration
  counts: claims, completes, fails, recovers, retries
- the Python pipeline DB adjudication ledger for cloud cost metering

Emits one JSON block per --side {python,rust} plus a summary when both are
given.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def load_rows(out_root: Path):
    rows = {}
    for chunk_dir in sorted(out_root.glob("chunk-*")):
        for f in chunk_dir.glob("*.json"):
            if f.name == "manifest.json":
                continue
            d = json.loads(f.read_text())
            rows[d["stem"]] = d
    # python soak layout: flat dir of {stem}.json
    if not rows:
        for f in out_root.glob("*.json"):
            if f.name.startswith("_"):
                continue
            d = json.loads(f.read_text())
            rows[d["stem"]] = d
    return rows


def row_metrics(rows):
    skips = Counter(r.get("skip") for r in rows.values() if r.get("skip"))
    ok = [r for r in rows.values() if not r.get("skip")]
    claims = sum(r.get("claims", 0) for r in ok)
    surviving_claims = [c for r in ok for c in (r.get("claim_data") or [])]
    with_page = sum(1 for c in surviving_claims if c.get("page") is not None)
    with_sha = sum(1 for c in surviving_claims if c.get("doc_sha256"))
    with_quote = sum(1 for c in surviving_claims if c.get("quote"))
    return {
        "docs_completed": len(rows),
        "docs_with_claims": len(ok),
        "skip_classes": dict(skips),
        "tiers": dict(Counter(r.get("tier") for r in ok)),
        "claims_total": claims,
        "claims_per_claim_doc": round(claims / max(1, len(ok)), 3),
        "surviving_claims": len(surviving_claims),
        "surviving_rate": round(len(surviving_claims) / max(1, claims), 4),
        "supported": sum(r.get("supported", 0) for r in ok),
        "p0": sum(r.get("p0", 0) for r in ok),
        "provenance": {
            "quote_pct": round(100 * with_quote / max(1, len(surviving_claims)), 2),
            "page_pct": round(100 * with_page / max(1, len(surviving_claims)), 2),
            "doc_sha_pct": round(100 * with_sha / max(1, len(surviving_claims)), 2),
        },
    }


def controller_metrics(state_dir: Path):
    db = state_dir / "factory.sqlite3"
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        chunks = dict(
            con.execute(
                "SELECT status, COUNT(*) FROM factory_chunks GROUP BY status"
            ).fetchall()
        )
        events = dict(
            con.execute(
                "SELECT event, COUNT(*) FROM factory_events GROUP BY event"
            ).fetchall()
        )
        attempts = con.execute(
            "SELECT COUNT(*), MAX(attempt) FROM factory_chunks"
        ).fetchone()
        elapsed = con.execute(
            "SELECT MIN(created_at), MAX(updated_at) FROM factory_chunks"
        ).fetchone()
        return {
            "chunks_by_status": chunks,
            "events": events,
            "chunks_total": attempts[0],
            "max_attempt": attempts[1],
            "first_created": elapsed[0],
            "last_updated": elapsed[1],
        }
    finally:
        con.close()


def python_cost(pipeline_db: Path):
    if not pipeline_db.exists():
        return {"metered_cost_usd": 0.0, "note": "no pipeline db"}
    con = sqlite3.connect(f"file:{pipeline_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT reason_code, COUNT(*) FROM adjudication_ledger "
            "WHERE judge='cost-meter' GROUP BY reason_code"
        ).fetchall()
        if not rows:
            return {"metered_cost_usd": 0.0, "note": "no cost-meter rows (local lanes only)"}
        total = sum(float(r[0]) * r[1] for r in rows if r[0] and r[0] != "None")
        return {
            "metered_cost_usd": round(total, 4),
            "rows": [[r[0], r[1]] for r in rows],
        }
    except sqlite3.OperationalError as e:
        return {"metered_cost_usd": 0.0, "note": f"ledger unreadable: {e}"}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--side", choices=("python", "rust"), required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--elapsed-s", type=float, default=None,
                    help="wall seconds for the run (from log/report if omitted)")
    ap.add_argument("--python-report", type=Path, default=None)
    ap.add_argument("--state-dir", type=Path, default=None)
    ap.add_argument("--pipeline-db", type=Path, default=None)
    args = ap.parse_args()

    rows = load_rows(args.out_root)
    out = {"side": args.side, **row_metrics(rows)}
    if args.python_report and args.python_report.exists():
        rep = json.loads(args.python_report.read_text())
        out["report"] = rep
        out["elapsed_s"] = rep.get("elapsed_s")
    else:
        out["elapsed_s"] = args.elapsed_s
    docs = out["docs_completed"]
    if out["elapsed_s"]:
        out["docs_per_hour"] = round(3600 * docs / out["elapsed_s"], 1)
        out["verified_claims_per_hour"] = round(
            3600 * out["surviving_claims"] / out["elapsed_s"], 1
        )
    if args.state_dir:
        out["controller"] = controller_metrics(args.state_dir)
    if args.pipeline_db:
        out["cost"] = python_cost(args.pipeline_db)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
