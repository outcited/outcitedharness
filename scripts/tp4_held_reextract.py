#!/usr/bin/env python3
"""tp4_held_reextract — second-pass re-derivation of held claims (engine law
Step 2: TP4 + page context for docs where local binding failed).

Input: tp4-held-work.jsonl (held rows, reason=value_not_in_evidence).
For each: open the PDF page, send verbatim-quote + claimed value + page text
to TP4 (thinking off), ask it to re-derive the value from the page text —
answer must quote the supporting text span, else the row stays held (fail
closed, never guessed).

Output: tp4-held-resolved.jsonl rows {claim_id, verdict: rederived|still_held,
value, unit, span_quote} — feeds spine L2 re-verification (values go back
through the same evidence gates; TP4 proposes, L2 decides).
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import pymupdf

BASE = os.environ.get("TP4_BASE", "http://100.116.221.82:8888")
MODEL = os.environ.get("TP4_MODEL", "deepseek-v4.1-flash")
WORK = os.environ.get("TP4_WORK", "/tmp/tp4-held-work.jsonl")
OUT = os.environ.get("TP4_OUT", "/tmp/tp4-held-resolved.jsonl")
CONC = int(os.environ.get("TP4_CONC", "4"))
LIMIT = int(os.environ.get("TP4_LIMIT", "0"))

PROMPT = """A datasheet extraction row FAILED verification because its value was not
found in the evidence text. Re-derive it from the page.

Field: {field}
Claimed value: {value} {unit}
Row verbatim: {verbatim}

Page text (page {page}):
{pagetext}

Answer STRICT JSON only, one object:
{{"found": true/false,
  "value": <number or null>,
  "span_quote": "<exact substring from the page text supporting the value>",
  "note": "<max 12 words>"}}
Rules: span_quote MUST be an exact substring of the page text. If the value
cannot be re-derived from this page, found=false. Never guess."""


def call_tp4(system, user, timeout=60):
    body = json.dumps({
        "model": MODEL, "temperature": 0.1, "max_tokens": 300,
        "chat_template_kwargs": {"thinking": False},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}]}).encode()
    req = Request(BASE + "/v1/chat/completions", data=body,
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"] or ""


_page_cache = {}


def page_text(path, page_1):
    key = (path, page_1)
    if key in _page_cache:
        return _page_cache[key]
    try:
        doc = pymupdf.open(path)
        t = doc[page_1 - 1].get_text()[:6000] if 0 < page_1 <= doc.page_count else ""
        doc.close()
    except Exception:
        t = ""
    _page_cache[key] = t
    return t


def extract_json(text):
    import re
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def work_one(item):
    pt = page_text(item["source_path"], item.get("page_1based") or 1)
    if not pt:
        return dict(item, verdict="still_held", note="no_page_text")
    user = PROMPT.format(field=item["field"], value=item["value_raw"],
                         unit=item.get("unit") or "", verbatim=(item.get("verbatim") or "")[:200],
                         page=item.get("page_1based"), pagetext=pt)
    try:
        ans = extract_json(call_tp4("You re-derive datasheet values from page text. Strict JSON only.", user))
    except Exception as e:
        return dict(item, verdict="error", note=str(e)[:60])
    if not ans or not ans.get("found"):
        return dict(item, verdict="still_held", note=(ans or {}).get("note", "not_found"))
    span = (ans.get("span_quote") or "").strip()
    if len(span) < 3 or span not in pt:
        return dict(item, verdict="still_held", note="span_not_in_page")
    val = ans.get("value")
    if not isinstance(val, (int, float)):
        return dict(item, verdict="still_held", note="value_not_numeric")
    return dict(item, verdict="rederived", value=val,
                span_quote=span[:300], note=ans.get("note", ""))


def main():
    items = [json.loads(l) for l in open(WORK) if l.strip()]
    if LIMIT:
        items = items[:LIMIT]
    done_ids = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try:
                done_ids.add(json.loads(l)["claim_id"])
            except Exception:
                pass
    items = [i for i in items if i["claim_id"] not in done_ids]
    print(f"todo: {len(items)}", flush=True)
    n_ok = n_held = n_err = 0
    with open(OUT, "a") as out:
        with ThreadPoolExecutor(CONC) as pool:
            for res in pool.map(work_one, items):
                out.write(json.dumps(res) + "\n")
                if res["verdict"] == "rederived":
                    n_ok += 1
                elif res["verdict"] == "error":
                    n_err += 1
                else:
                    n_held += 1
                if (n_ok + n_held + n_err) % 200 == 0:
                    out.flush()
                    print(f"  {n_ok + n_held + n_err}: rederived={n_ok} held={n_held} err={n_err}", flush=True)
    print(f"DONE rederived={n_ok} still_held={n_held} errors={n_err}")


if __name__ == "__main__":
    main()
