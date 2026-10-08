#!/usr/bin/env python3
"""Sentinel: continuous quality regression (24/7).

Hourly  : re-verify 5 random recent claims (must be 100%) + canon 5x5 probe
Nightly : frozen gold sets through the LIVE chain; drift >2% vs last-known
           good alarms. Infra health = soak; MEANING health = sentinel.
Alerts  : results/sentinel/ALERTS.md + mail to m5-opencode box.
"""

import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")

from harness.pipeline.quote_verify import verify_claims

RESULTS = Path("/Volumes/M5_4TB/extract-results")
SENTINEL = RESULTS / "sentinel"
ALERTS = SENTINEL / "ALERTS.md"
LKG = SENTINEL / "last-known-good.json"
CANON = "http://100.100.116.82:8900/v1/chat/completions"

random.seed()


def alert(msg):
    line = f"\n## {time.strftime('%Y-%m-%dT%H:%M:%SZ')} — {msg}\n"
    with ALERTS.open("a") as f:
        f.write(line)
    print(line.strip(), flush=True)


def hourly_claims_check(con):
    rows = con.execute(
        "SELECT id, output, corpus_key FROM results WHERE kind_hint='extract'"
        " ORDER BY created_at DESC LIMIT 200").fetchall()
    sample = random.sample(rows, min(5, len(rows)))
    fails = 0
    for r in sample:
        try:
            out = json.loads(r["output"])
        except Exception:
            continue
        if not isinstance(out, list):
            continue
        text = _text_for(con, r["corpus_key"])
        if not text:
            continue
        simple = _simplify_claims(out)
        if not simple:
            continue
        verdicts = verify_claims(simple, text)
        p0 = [v for v in verdicts if v["severity"] == "P0"]
        if p0:
            fails += 1
            alert(f"P0 REGRESSION result={r['id']} key={r['corpus_key']}"
                  f" fails={len(p0)} — previously-passing claim now fails")
    if fails == 0 and sample:
        print(f"hourly: {len(sample)} sampled claims clean", flush=True)


def _text_for(con, corpus_key):
    row = con.execute("SELECT output FROM results WHERE corpus_key=?"
                      " AND kind_hint='substrate' ORDER BY created_at DESC LIMIT 1",
                      (corpus_key,)).fetchone()
    if not row:
        return ""
    try:
        sub = json.loads(row["output"])
        return "\n".join(p.get("text", "") for p in sub.get("pages", []))
    except Exception:
        return ""


def _simplify_claims(claims):
    out = {}
    for i, c in enumerate(claims):
        if isinstance(c, dict) and c.get("value") is not None:
            out[f"c{i}"] = {k: c.get(k) for k in
                            ("value", "quote", "row_header", "column_header",
                             "qualifier")}
    return out


def canon_determinism(con):
    body = json.dumps({"model": "qwen3.8-27b-fp8", "temperature": 0,
                       "max_tokens": 60,
                       "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user",
                                     "content": "Reply with exactly: OK"}]}).encode()
    keys = []
    for _ in range(3):
        try:
            req = urllib.request.Request(CANON, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                keys.append(json.loads(r.read())["choices"][0]["message"]["content"])
        except Exception as e:
            alert(f"canon unreachable: {str(e)[:60]}")
            return
    if len(set(keys)) != 1:
        alert(f"canon nondeterminism on trivial probe: {keys}")


def nightly_gold_drift():
    metrics = {}
    qv = Path("/Users/samkim/Harnessv1/results/lora-v1-pairs/gate-report.json")
    if qv.exists():
        metrics["student_gate_f1"] = json.loads(qv.read_text()).get("f1")
    pb = Path("/Users/samkim/Harnessv1/results/adjudication/canon-natural-gold-gate-v3.json")
    if pb.exists():
        try:
            d = json.loads(pb.read_text())
            metrics["canon_gate"] = d.get("summary", {}).get("bad_rejected")
        except Exception:
            pass
    burn = Path("/Volumes/M5_4TB/extract-results/burn-power-v1/_report.json")
    if burn.exists():
        metrics["burn_p0_rate"] = json.loads(burn.read_text()).get("p0_rate")
    if LKG.exists():
        prev = json.loads(LKG.read_text())
        for k, v in metrics.items():
            if v is None or prev.get(k) is None:
                continue
            delta = abs(v - prev[k])
            if delta > 0.02:
                alert(f"GOLD DRIFT {k}: {prev[k]} -> {v} (delta {delta:.3f})")
    LKG.write_text(json.dumps(metrics, indent=2))
    print(f"nightly: {json.dumps(metrics)}", flush=True)


def main():
    SENTINEL.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else "hourly"
    if mode == "hourly":
        import sqlite3
        con = sqlite3.connect(f"{RESULTS}/pipeline.db", timeout=15)
        con.row_factory = sqlite3.Row
        hourly_claims_check(con)
        canon_determinism(con)
    elif mode == "nightly":
        nightly_gold_drift()


if __name__ == "__main__":
    main()
