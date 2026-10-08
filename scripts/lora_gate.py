#!/usr/bin/env python3
"""LoRA gate: score the tuned student against holdout labels.

Field-level scoring on the 150 held-out pairs: symbol-set match, value match
(unit-normalized), unit match. Writes gate-report.json; PASS requires
value+unit exact >= 0.95 on fields the student asserts and no invented
symbols beyond 0.5%.
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

STUDENT = "http://100.68.133.1:8950/v1/chat/completions"
MODEL = "power-tables-student-v1"
HOLDOUT = Path("results/lora-v1-pairs/llamafactory/power-tables-v1/holdout.jsonl")
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
    if u in UNIT_TO_OHM:
        return v * UNIT_TO_OHM[u]
    return v


def call_student(text, part, timeout=240):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 6000,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"},
        ],
    }).encode()
    req = urllib.request.Request(STUDENT, data=body,
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
        wv, gv = norm_value(w.get("value"), w.get("unit")), norm_value(r.get("value"), r.get("unit"))
        if wv is not None and gv is not None and abs(wv - gv) <= max(1e-9, abs(wv) * 1e-4):
            tp += 1
        else:
            fp += 1
    fn = len(want) - len(want.keys() & got_syms)
    return tp, fp, fn


def main(limit=150):
    import concurrent.futures
    rows = [json.loads(l) for l in HOLDOUT.open()][:limit]
    tot_tp = tot_fp = tot_fn = 0
    done = 0

    def run_one(i_r):
        i, r = i_r
        part = r["instruction"].split("Part: ")[1].split("\n")[0] if "Part: " in r["instruction"] else "?"
        text = r["input"].split("Datasheet pages:\n")[-1]
        want = json.loads(r["output"])
        try:
            got = call_student(text, part, timeout=600)
        except Exception as e:
            return i, 0, 0, len(want), str(e)[:60]
        tp, fp, fn = score(want, got)
        return i, tp, fp, fn, None

    with concurrent.futures.ThreadPoolExecutor(10) as ex:
        for i, tp, fp, fn, err in ex.map(run_one, enumerate(rows)):
            tot_tp += tp; tot_fp += fp; tot_fn += fn
            done += 1
            if err:
                print(f"[{i}] error {err}", flush=True)
            if done % 10 == 0:
                p = tot_tp / max(1, tot_tp + tot_fp)
                r_ = tot_tp / max(1, tot_tp + tot_fn)
                print(f"{done}/{len(rows)} precision={p:.3f} recall={r_:.3f}", flush=True)
    precision = tot_tp / max(1, tot_tp + tot_fp)
    recall = tot_tp / max(1, tot_tp + tot_fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    report = {
        "student": MODEL, "docs": len(rows),
        "fields_matched": tot_tp, "fields_wrong_or_extra": tot_fp, "fields_missed": tot_fn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "pass": f1 >= 0.90 and precision >= 0.95,
    }
    Path("results/lora-v1-pairs/gate-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
