#!/usr/bin/env python3
"""Hosted adjudicator. Verifies claimed extraction rows against source text.

Calibration mode reads llamafactory holdout pairs (claims = CR-adjudicated
gold) and reports judge-vs-gold agreement. Generic mode takes a rows jsonl:
{"id", "text", "claims": [{"symbol","value","unit","qualifier","condition"}]}.
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

MODEL = "qwen/qwen-2.5-72b-instruct"
API = "https://openrouter.ai/api/v1/chat/completions"

JUDGE_SYSTEM = (
    "You are a strict datasheet extraction auditor. You receive document text "
    "and a JSON list of claimed parametric rows. For EACH claim, verify it is "
    "supported by the text: same value, unit, qualifier, and condition. Reply "
    'ONLY JSON: {"verdicts": [{"i": <claim index>, "verdict": '
    '"SUPPORTED"|"UNSUPPORTED"|"NOT_IN_DOC", "reason": "<short>"}]}. '
    "NOT_IN_DOC means the relevant text is absent. Do not use outside knowledge."
)


def call_api(text, claims, key, timeout=180):
    body = json.dumps(
        {
            "model": MODEL,
            "temperature": 0,
            "seed": 1788172800,
            "max_tokens": 8192,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": f"Document text:\n{text}\n\nClaims:\n{json.dumps(claims, ensure_ascii=False)}",
                },
            ],
            "provider": {"order": ["DeepInfra"], "allow_fallbacks": False},
        }
    ).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/harness",
            "X-Title": "model-harness-adjudicator",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
                return out["choices"][0]["message"]["content"]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def parse_verdicts(content):
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0)).get("verdicts", [])
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None


def judge_item(item, key):
    claims = item.get("claims") or json.loads(item["output"])
    text = item.get("text") or item.get("input", "")
    verdicts = []
    for start in range(0, len(claims), 25):
        chunk = claims[start : start + 25]
        content = call_api(text, chunk, key)
        v = parse_verdicts(content)
        if v is None:
            return {"id": item.get("id", "?"), "error": "unparseable", "raw": content[:200]}
        for row in v:
            row["i"] = row.get("i", 0) + start
        verdicts.extend(v)
    return {
        "id": item.get("id", "?"),
        "n_claims": len(claims),
        "verdicts": verdicts,
    }


def load_holdout(path, limit):
    items = []
    for i, line in enumerate(Path(path).open()):
        if i >= limit:
            break
        r = json.loads(line)
        items.append({"id": f"holdout-{i}", "input": r["input"], "output": r["output"]})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", default="results/lora-v1-pairs/llamafactory/power-tables-v1/holdout.jsonl")
    ap.add_argument("--rows")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default="results/adjudication/judge-calibration-v1.jsonl")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        for line in open("/Users/samkim/Harnessv1/.env"):
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        print("no OPENROUTER_API_KEY")
        return 1

    if args.rows:
        items = [json.loads(l) for l in open(args.rows)][: args.limit]
    else:
        items = load_holdout(args.holdout, args.limit)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tally = {"SUPPORTED": 0, "UNSUPPORTED": 0, "NOT_IN_DOC": 0, "error": 0}
    done = 0
    with out_path.open("w") as f, concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(judge_item, it, key): it for it in items}
        for fut in concurrent.futures.as_completed(futs):
            try:
                res = fut.result()
            except Exception as e:
                res = {"id": "?", "error": str(e)[:120]}
            for v in res.get("verdicts", []):
                verdict = v.get("verdict", "error")
                tally[verdict] = tally.get(verdict, 0) + 1
            if "error" in res:
                tally["error"] += 1
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(items)} {tally}", flush=True)
    total = sum(tally.values()) or 1
    summary = {
        "model": MODEL,
        "items": len(items),
        "claims_judged": total,
        "tally": tally,
        "gold_supported_rate": round(tally["SUPPORTED"] / total, 4),
        "note": "gold rows judged UNSUPPORTED = judge errors; NOT_IN_DOC often = page truncation in pair build",
    }
    Path(str(out_path) + ".summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
