#!/usr/bin/env python3
"""Student v2 gate: score candidate checkpoint against the v2 quote-native holdout.

Same field-level scoring as lora_gate.py (symbol/value/unit, unit-normalized),
against results/lora-v2-pairs holdout (190 docs). PASS: f1 >= 0.90 and
precision >= 0.95. Writes gate-report-v2.json.

Usage: python3 gate_v2.py [--endpoint URL] [--model NAME] [--limit N]
"""

import argparse
import json
import re
import urllib.request
from pathlib import Path

HOLDOUT = Path("results/lora-v2-pairs/llamafactory/power-tables-v2/holdout.jsonl")
REPORT = Path("results/lora-v2-pairs/gate-report-v2.json")
SYSTEM = (
    "You are a datasheet parametric extraction engine. Given datasheet pages, "
    "extract the parametric table rows for the requested part as compact JSON: "
    '[{"symbol": str, "value": number, "unit": str, "qualifier": str|null, '
    '"condition": str|null}]. Use only values printed in the document.'
)
UNIT_TO_OHM = {"mohm": 1e-3, "ohm": 1.0, "kohm": 1e3, "mω": 1e-3, "ω": 1.0}


def norm_value(value, unit):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    u = (unit or "").strip().lower()
    return v * UNIT_TO_OHM[u] if u in UNIT_TO_OHM else v


def call_model(endpoint, model, text, part, timeout=900):
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 6000,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"},
        ],
    }).encode()
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        c = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    m = re.search(r"\[.*\]", c, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            frag = m.group(0)
            cut = frag.rfind("}")
            if cut > 0:
                try:
                    return json.loads(frag[: cut + 1] + "]")
                except json.JSONDecodeError:
                    return []
    return []


def score(want_rows, got_rows):
    want = {(r.get("symbol") or r.get("param") or "").lower(): r
            for r in want_rows if (r.get("symbol") or r.get("param"))}
    got_syms = set()
    tp = fp = fn = 0
    for r in got_rows:
        sym = (r.get("symbol") or r.get("param") or "").lower()
        got_syms.add(sym)
        w = want.get(sym)
        if w is None:
            fp += 1
            continue
        wv = norm_value(w.get("value"), w.get("unit"))
        gv = norm_value(r.get("value"), r.get("unit"))
        if wv is not None and gv is not None and abs(wv - gv) <= max(1e-9, abs(wv) * 1e-4):
            tp += 1
        else:
            fp += 1
    fn = len(want) - len(want.keys() & got_syms)
    return tp, fp, fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://100.81.201.24:8950/v1/chat/completions")
    ap.add_argument("--model", default="power-tables-student-v1")
    ap.add_argument("--limit", type=int, default=190)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    rows = [json.loads(l) for l in HOLDOUT.open()][:args.limit]
    tot_tp = tot_fp = tot_fn = 0
    done = 0

    def run_one(i_r):
        i, r = i_r
        part = r["instruction"].split("Part: ")[1].split("\n")[0] if "Part: " in r["instruction"] else "?"
        text = r["input"].split("Datasheet pages:\n")[-1]
        want = json.loads(r["output"])
        try:
            got = call_model(args.endpoint, args.model, text, part)
        except Exception as e:
            return i, 0, 0, len(want), str(e)[:60]
        tp, fp, fn = score(want, got)
        return i, tp, fp, fn, None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(10) as ex:
        for i, tp, fp, fn, err in ex.map(run_one, enumerate(rows)):
            tot_tp += tp; tot_fp += fp; tot_fn += fn
            done += 1
            if err:
                print(f"[{i}] error {err}", flush=True)
            if done % 20 == 0:
                p = tot_tp / max(1, tot_tp + tot_fp)
                r_ = tot_tp / max(1, tot_tp + tot_fn)
                print(f"{done}/{len(rows)} precision={p:.3f} recall={r_:.3f}", flush=True)

    precision = tot_tp / max(1, tot_tp + tot_fp)
    recall = tot_tp / max(1, tot_tp + tot_fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    report = {
        "student": args.model, "docs": len(rows), "tag": args.tag,
        "fields_matched": tot_tp, "fields_wrong_or_extra": tot_fp, "fields_missed": tot_fn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "pass": f1 >= 0.90 and precision >= 0.95,
    }
    REPORT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
