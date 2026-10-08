#!/usr/bin/env python3
"""Dense judge bake-off. Plants known defects in gold claims and measures
catch rate (planted) vs false rejection (clean) per judge.

Corruption classes: unit swap (same dimension), value x1000/x0.001,
qualifier flip, condition drop. Deterministic seeding.
"""

import concurrent.futures
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from adjudicate import JUDGE_SYSTEM, parse_verdicts  # noqa: E402

import os  # noqa: E402

API = "https://openrouter.ai/api/v1/chat/completions"
JUDGES = {
    "qwen36-27b": ("qwen/qwen3.6-27b", None, None),
    "qwen38-27b": ("qwen/qwen3.8-27b", None, {"max_tokens": 8000, "reasoning": {"effort": "low"}}),
}
UNIT_SWAPS = {
    "V": "mV", "mV": "V", "A": "mA", "mA": "A", "Ohm": "mOhm", "mOhm": "Ohm",
    "F": "pF", "pF": "nF", "H": "uH", "W": "mW", "mW": "W", "Hz": "kHz",
    "MHz": "kHz", "GHz": "MHz", "V/us": "V/ns", "C": "mC",
}
QUAL_FLIPS = {"typical": "maximum", "maximum": "minimum", "minimum": "typical"}
SEED = 1788172800


def plant(claims, rng):
    planted = {}
    out = []
    for i, c in enumerate(claims):
        c = dict(c)
        kind = None
        r = rng.random()
        if r < 0.35 and c.get("unit") in UNIT_SWAPS:
            c["unit"] = UNIT_SWAPS[c["unit"]]
            kind = "unit_swap"
        elif r < 0.55 and isinstance(c.get("value"), (int, float)):
            c["value"] = c["value"] * 1000 if rng.random() < 0.5 else c["value"] / 1000
            kind = "value_scale"
        elif r < 0.75 and c.get("qualifier") in QUAL_FLIPS:
            c["qualifier"] = QUAL_FLIPS[c["qualifier"]]
            kind = "qual_flip"
        elif r < 0.9 and c.get("condition"):
            c["condition"] = None
            kind = "cond_drop"
        if kind:
            planted[i] = kind
        out.append(c)
    return out, planted


def call_judge(model, pin, text, claims, key, extra=None, timeout=180):
    body = {
        "model": model,
        "temperature": 0,
        "seed": SEED,
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"Document text:\n{text}\n\nClaims:\n{json.dumps(claims, ensure_ascii=False)}"},
        ],
    }
    if pin:
        body["provider"] = pin
    if extra:
        body.update(extra)
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://localhost/harness", "X-Title": "judge-bakeoff"},
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                msg = json.loads(r.read())["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning") or ""
                if isinstance(content, list):
                    content = " ".join(str(x) for x in content)
                return content
        except Exception:
            if attempt == 1:
                raise
            time.sleep(3)


def run_doc(args):
    judge, (model, pin, extra), item, key = args
    rng = random.Random(SEED + hash(item["id"]) % 100000)
    claims = json.loads(item["output"])
    mixed, planted = plant(claims, rng)
    order = list(range(len(mixed)))
    rng.shuffle(order)
    shuffled = [mixed[j] for j in order]
    remap = {new_i: old_i for new_i, old_i in enumerate(order)}
    verdicts = []
    text = item["input"][-15000:]
    for start in range(0, len(shuffled), 50):
        content = call_judge(model, pin, text, shuffled[start:start + 50], key, extra=extra)
        v = parse_verdicts(content)
        if v is None:
            return {"judge": judge, "id": item["id"], "error": "unparseable"}
        for row in v:
            row["i"] = row.get("i", 0) + start
        verdicts.extend(v)
    res = {"judge": judge, "id": item["id"], "planted": {}, "clean": {"ok": 0, "rejected": 0}}
    for v in verdicts:
        new_i = v.get("i", 0)
        old_i = remap.get(new_i, new_i)
        verdict = v.get("verdict", "error")
        if old_i in planted:
            kind = planted[old_i]
            caught = verdict in ("UNSUPPORTED", "NOT_IN_DOC")
            res["planted"].setdefault(kind, {"caught": 0, "missed": 0})
            res["planted"][kind]["caught" if caught else "missed"] += 1
        else:
            res["clean"]["ok" if verdict == "SUPPORTED" else "rejected"] += 1
    return res


def main():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        for line in open("/Users/samkim/Harnessv1/.env"):
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    items = []
    for i, line in enumerate(open("results/lora-v1-pairs/llamafactory/power-tables-v1/holdout.jsonl")):
        if i >= 30:
            break
        items.append({"id": f"holdout-{i}", "input": json.loads(line)["input"], "output": json.loads(line)["output"]})

    out_path = Path("results/adjudication/dense-bakeoff-v2-qwen27.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [(j, cfg, it, key) for j, cfg in JUDGES.items() for it in items]
    done_ids = set()
    if out_path.exists():
        for line in out_path.open():
            try:
                prev = json.loads(line)
                if "error" not in prev:
                    done_ids.add((prev["judge"], prev["id"]))
            except Exception:
                pass
    jobs = [j for j in jobs if (j[0], j[2]["id"]) not in done_ids]
    tally = {}
    done = 0
    with out_path.open("a") as f, concurrent.futures.ThreadPoolExecutor(12) as ex:
        futs = {ex.submit(run_doc, j): j for j in jobs}
        for fut in concurrent.futures.as_completed(futs):
            try:
                res = fut.result()
            except Exception as e:
                j = futs[fut]
                res = {"judge": j[0], "id": j[2]["id"], "error": str(e)[:120]}
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if "error" in res:
                msg = res["error"]
            else:
                msg = "caught=" + str(sum(v["caught"] for v in res["planted"].values()))
            print(f"{done}/{len(jobs)} {res['judge']} {res['id']} {msg}", flush=True)
            t = tally.setdefault(res["judge"], {"planted_caught": 0, "planted_missed": 0, "clean_ok": 0, "clean_rejected": 0, "errors": 0})
            if "error" in res:
                t["errors"] += 1
                continue
            for kind_stats in res["planted"].values():
                t["planted_caught"] += kind_stats["caught"]
                t["planted_missed"] += kind_stats["missed"]
            t["clean_ok"] += res["clean"]["ok"]
            t["clean_rejected"] += res["clean"]["rejected"]
    summary = {}
    for judge, t in tally.items():
        pc = t["planted_caught"] + t["planted_missed"]
        cc = t["clean_ok"] + t["clean_rejected"]
        summary[judge] = {
            "catch_rate": round(t["planted_caught"] / pc, 4) if pc else None,
            "false_reject_rate": round(t["clean_rejected"] / cc, 4) if cc else None,
            **t,
        }
    Path("results/adjudication/dense-bakeoff-v1.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
