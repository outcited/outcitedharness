#!/usr/bin/env python3
"""Retrieve completed OpenRouter pin batches, verify, and write rows.

Polls every batch in batch-ids.json; for each completed batch downloads the
results, parses responses, runs the deterministic verbatim-quote verifier
against substrate text, and appends verified rows to the batch rows file.
Held/unverified rows are recorded for a later retry pass.

Usage:
    python3 scripts/pin_batch_retrieve.py \
        --requests-dir results/pin-tables-20261014/batches \
        --out results/pin-tables-20261014/pin-rows-batch.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.extract_pin_tables_tp4 import (  # noqa: E402
    PIPELINE_DB, _norm, verify_rows,
)

_tls = threading.local()


def _con():
    if getattr(_tls, "con", None) is None:
        _tls.con = sqlite3.connect(f"file:{PIPELINE_DB}?mode=ro", uri=True, timeout=60)
        _tls.con.row_factory = sqlite3.Row
    return _tls.con


def _key() -> str:
    import os
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY missing")


def substrate_text(sha: str) -> str | None:
    row = _con().execute(
        "SELECT output FROM results WHERE corpus_key=? AND kind_hint='substrate'"
        " ORDER BY id DESC LIMIT 1", (f"pdf:{sha}",)).fetchone()
    if not row:
        return None
    try:
        return "\n".join(p.get("text") or ""
                         for p in json.loads(row["output"]).get("pages") or [])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--requests-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    ids_path = args.requests_dir / "batch-ids.json"
    ids = json.loads(ids_path.read_text()) if ids_path.exists() else {}
    if not ids:
        print("no batches recorded")
        return 0
    key = _key()

    done = set()
    if args.out.exists():
        for l in args.out.read_text().splitlines():
            try:
                done.add(json.loads(l)["sha"])
            except Exception:
                pass

    tally: Counter = Counter()
    n_rows = 0
    with args.out.open("a") as fh:
        for stem, rec in ids.items():
            bid = rec.get("batch_id")
            if not bid:
                continue
            req = urllib.request.Request(
                f"https://openrouter.ai/api/v1/batches/{bid}",
                headers={"Authorization": "Bearer " + key})
            try:
                b = json.load(urllib.request.urlopen(req, timeout=120))
            except Exception as e:
                print(f"{stem}: poll failed {e}", flush=True)
                continue
            status = b.get("status")
            if status != "completed":
                print(f"{stem}: {status} {b.get('request_counts')}", flush=True)
                continue
            results_url = (b.get("results") or {}).get("url") if isinstance(b.get("results"), dict) else b.get("results")
            if not results_url:
                print(f"{stem}: completed but no results url", flush=True)
                continue
            try:
                data = urllib.request.urlopen(results_url, timeout=300).read().decode()
            except Exception as e:
                print(f"{stem}: download failed {e}", flush=True)
                continue
            for line in data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                cid = row.get("custom_id") or ""
                if not cid.startswith("pin-batch-"):
                    continue
                sha = cid[len("pin-batch-"):]
                if sha in done:
                    continue
                resp = row.get("response") or {}
                if row.get("error") or resp.get("status_code", 200) >= 400:
                    fh.write(json.dumps({"sha": sha, "verdict": "held",
                                         "reason": "batch_error"}, sort_keys=True) + "\n")
                    tally["held_batch_error"] += 1
                    done.add(sha)
                    continue
                msg = (resp.get("body", resp).get("choices") or [{}])[0].get("message", {})
                raw = (msg.get("content") or "").strip()
                if raw.startswith("```"):
                    raw = raw.strip("`").lstrip("json").strip()
                text = substrate_text(sha)
                if text is None:
                    fh.write(json.dumps({"sha": sha, "verdict": "held",
                                         "reason": "no_substrate"}, sort_keys=True) + "\n")
                    tally["held_no_substrate"] += 1
                    done.add(sha)
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    fh.write(json.dumps({"sha": sha, "verdict": "held",
                                         "reason": "unparseable"}, sort_keys=True) + "\n")
                    tally["held_unparseable"] += 1
                    done.add(sha)
                    continue
                if not payload.get("has_pin_table"):
                    fh.write(json.dumps({"sha": sha, "verdict": "no_pin_table"},
                                        sort_keys=True) + "\n")
                    tally["no_pin_table"] += 1
                    done.add(sha)
                    continue
                kept, dropped = verify_rows(payload, _norm(text))
                verdict = "ok" if kept else "unverified"
                fh.write(json.dumps({"sha": sha, "verdict": verdict,
                                     "package": payload.get("package"),
                                     "rows": kept, "dropped_unverified": dropped},
                                    ensure_ascii=False, sort_keys=True) + "\n")
                tally[verdict] += 1
                tally["rows"] += len(kept)
                n_rows += len(kept)
                done.add(sha)
            fh.flush()
            rec["retrieved"] = True
            ids_path.write_text(json.dumps(ids, indent=1))
    print(json.dumps({"retrieved_batches": sum(1 for r in ids.values() if r.get("retrieved")),
                      "new_rows": n_rows, **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())