#!/usr/bin/env python3
"""opn_grammar_pilot.py — TP4 bounded pilot: decode OPN ordering-code grammar.

CANDIDATE-ONLY: model output is never gold, never written to identity tiers.
Validation path: candidates with an ordering-table quote found in our corpus
docs are staged for human validation; everything else stays candidate.
Settings law: local TP4, chat_template_kwargs {"thinking": false}, temp 0.
Concurrency 8 per the 2026-10-20 ruling.
"""
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

EP = "http://192.168.4.45:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

PROMPT = ("Decode this electronic component ordering part number (OPN) grammar. "
          "Return a JSON object with keys: base_part (die/function family), "
          "package_code (what the suffix letters mean, e.g. TR=Tape and Reel), "
          "temp_grade (I=industrial/A=automotive/etc if encoded), "
          "variant_notes (speed/quality/mounting distinctions), "
          "manufacturer_guess. Use standard industry ordering conventions. "
          "If any field is not decodable, use null. JSON only.")

def call(slug):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 2000,
        "chat_template_kwargs": {"thinking": False},
        "messages": [{"role": "user", "content": PROMPT + "\n\nOPN: " + slug}],
    }).encode()
    req = urllib.request.Request(EP, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read())
    content = res["choices"][0]["message"].get("content") or ""
    s, e = content.find("{"), content.rfind("}")
    parsed = None
    if s >= 0 and e > s:
        try:
            parsed = json.loads(content[s:e + 1])
        except json.JSONDecodeError:
            pass
    return {"slug": slug, "candidate": parsed,
            "status": "decoded" if parsed else "undecoded",
            "latency_s": round(time.time() - t0, 1)}

def main():
    slugs = json.load(open("/Users/samkim/lane-out/spine/opn-pilot-slugs.json"))
    out = Path("/Users/samkim/lane-out/spine/opn-grammar-pilot.jsonl")
    with out.open("w") as fh:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(call, s) for s in slugs]
            for f in as_completed(futs):
                r = f.result()
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    n = d = 0
    pkg = 0
    for line in out.open():
        r = json.loads(line)
        n += 1
        d += r["status"] == "decoded"
        pkg += bool(r.get("candidate") and (r["candidate"].get("package_code") or r["candidate"].get("temp_grade")))
    print(json.dumps({"pilot": n, "decoded": d, "with_package_or_temp_semantics": pkg,
                      "candidate_only": True, "gold_promotion": "NONE (review-gated)"}, indent=1))

if __name__ == "__main__":
    main()
