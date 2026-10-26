#!/usr/bin/env python3
"""phrase_boards — Layer 2: natural engineer phrasings for every rank board.

Sam directive 2026-10-22: a rank board is a materialized knife; the phrasing
layer makes it findable in the language engineers actually type. Local
asus2 (free), temperature low, strict JSON. Idempotent per board name.

Input:  RANKBOARD_DIR/index.v1.jsonl (+ boards.v1/*.json)
Output: RANKBOARD_DIR/phrases.v1.jsonl
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

BASE = os.environ.get("PHRASE_BASE", "http://100.68.133.1:8900/v1")
MODEL = os.environ.get("PHRASE_MODEL", "qwen3.8-27b-nvfp4")
RB = Path(os.environ.get("RANKBOARD_DIR", "/Users/samkim/lane-out/rankboards"))
N_PHRASES = int(os.environ.get("PHRASE_N", "6"))
WORKERS = int(os.environ.get("PHRASE_WORKERS", "8"))
KEY = os.environ.get("PHRASE_KEY", "")

PROMPT = """You write search phrases for an electronics-parts comparison engine.

Board: electronic components ranked by "{axis}" ({direction}, {statistic} values{cohort}).
Units of the ranked quantity: {unit}. Example top value: {example}.

Write {n} DIFFERENT natural phrases an engineer would type to find this ranking.
Rules:
- plain engineer language, no marketing words
- short (3-10 words each)
- do NOT invent attributes not in the axis
- one phrase per line, no numbering

Phrases:"""


def call(board):
    rows = board["rows"]
    if not rows:
        return None
    ex = rows[0]
    cohort = ", automotive AEC-Q qualified parts only" if board["cohort"] == "aec" else ""
    prompt = PROMPT.format(
        axis=board["symbol_group"], direction="lowest first" if board["direction"] == "asc" else "highest first",
        statistic=board["qualifier"], cohort=cohort,
        unit=ex["unit_canon"], example=f'{ex["value_text"]} {ex["unit_canon"]}',
        n=N_PHRASES)
    body = json.dumps({
        "model": MODEL, "temperature": 0.2, "max_tokens": 400,
        "chat_template_kwargs": {"thinking": False},
        "reasoning_effort": "low",
        "messages": [{"role": "user", "content": prompt}]}).encode()
    headers = {"Content-Type": "application/json"}
    if KEY:
        headers["Authorization"] = "Bearer " + KEY
    req = Request(BASE + "/chat/completions", data=body, headers=headers)
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as r:
                d = json.load(r)
            text = d["choices"][0]["message"]["content"] or ""
            bad = ("need", "write", "rules", "phrases:", "board:", "units of",
                   "example top", "you write", "electronic components ranked",
                   "natural phrases", "differen", "maybe", "could ", "ensure")
            phrases = []
            for ln in text.splitlines():
                ln = ln.strip(" -*#0123456789.\"' ")
                if 8 < len(ln) < 70 and not any(b in ln.lower() for b in bad) \
                        and ln[-1] not in "?!:":
                    phrases.append(ln)
            phrases = phrases[:N_PHRASES]
            if len(phrases) >= 3:
                return phrases
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def main():
    idx = [json.loads(l) for l in open(RB / "index.v1.jsonl")]
    done = set()
    out_path = RB / "phrases.v1.jsonl"
    if out_path.exists():
        for l in open(out_path):
            try:
                done.add(json.loads(l)["name"])
            except json.JSONDecodeError:
                pass
    todo = [i for i in idx if i["name"] not in done]
    print(f"boards: {len(idx)} | phrased: {len(done)} | todo: {len(todo)}", flush=True)
    out = open(out_path, "a")
    n_ok = n_fail = 0

    def work(item):
        b = json.load(open(RB / "boards.v1" / f'{item["name"]}.json'))
        p = call(b)
        return item, p

    with ThreadPoolExecutor(WORKERS) as pool:
        for item, phrases in pool.map(work, todo):
            if phrases:
                out.write(json.dumps({
                    "name": item["name"], "symbol_group": item["symbol_group"],
                    "direction": item["direction"], "qualifier": item["qualifier"],
                    "cohort": item["cohort"], "phrases": phrases}) + "\n")
                n_ok += 1
            else:
                n_fail += 1
            if (n_ok + n_fail) % 50 == 0:
                out.flush()
                print(f"  {n_ok + n_fail}/{len(todo)} ok={n_ok} fail={n_fail}", flush=True)
    out.close()
    print(f"DONE ok={n_ok} fail={n_fail} -> {out_path}")


if __name__ == "__main__":
    main()
