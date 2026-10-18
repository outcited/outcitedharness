#!/usr/bin/env python3
"""Staged map-context canary for the eligible empty population
(PR-DIRECTION: classify first, then a SMALL representative canary;
exact input identities + original failure reasons preserved).

Route: same model (TP4), same prompt schema, same 28k text cap, same
deterministic gate as the burn lane — the ONLY change is the added
CONTEXT MAP built deterministically from the substrate (table-signal
pages, printed header rows, row-label inventory). Nothing fabricated;
map is source-derived evidence with the hold-on-conflict policy.

Metrics: recovered docs (>=1 gate-passing claim), claims proposed vs
gated, latency. Cost $0 (local lane).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.pipeline.quote_verify import (  # noqa: E402
    find_quote, value_in_quote, column_position_binds)

TP4 = "http://100.116.221.82:8888/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"

STUDENT_PROMPT = (
    "You are a datasheet parametric extraction engine. Given datasheet pages, "
    "extract the parametric table rows for the requested part. Every value MUST carry a quote "
    "copied character-for-character from the text, plus the ROW and COLUMN headers that govern "
    "the cell — if you cannot quote it or name its row/column, emit null. "
    "Output ONLY JSON: [{\"symbol\": str, \"value\": number, \"unit\": str, \"qualifier\": str|null, "
    "\"condition\": str|null, \"quote\": \"<verbatim span>\", \"row_header\": \"<row label from the table>\", "
    "\"column_header\": \"<column label: Min/Typ/Max/parameter name>\"}]"
)
MAP_PREAMBLE = (
    "\n\nCONTEXT MAP (deterministic, source-derived evidence — use it to find "
    "the tables; it is NOT unquestionable truth; where the map disagrees with "
    "the text, prefer the text and leave the row out):\n"
)

SIGNALS = [
    re.compile(r"electrical characteristics", re.I),
    re.compile(r"absolute maximum", re.I),
    re.compile(r"recommended operating", re.I),
    re.compile(r"min\W{0,3}typ\W{0,3}max", re.I),
]
HEADER_ROW = re.compile(r"(parameter|symbol).{0,40}(condition|min|typ|max|unit)", re.I)
ROW_LABEL = re.compile(r"^[A-Z][A-Za-z0-9 ()/.,_–-]{2,40}$")
UNIT = re.compile(r"\b(?:[mµunp]?[AVWΩ]|°C|kHz|MHz|ns|µs|mA|mV)\b")


def build_map(pages: list[str]) -> dict:
    out_pages = []
    for i, t in enumerate(pages):
        cap = t[:28000]
        sig = [rx.pattern for rx in SIGNALS if rx.search(cap)]
        headers = [ln.strip() for ln in cap.splitlines()
                   if HEADER_ROW.search(ln) and len(ln) < 120][:4]
        row_labels = [ln.strip() for ln in cap.splitlines()
                      if ROW_LABEL.match(ln.strip()) and UNIT.search(ln)
                      and len(ln.strip()) < 80][:12]
        if sig or headers:
            out_pages.append({
                "page_1based": i + 1,
                "signals": sig,
                "printed_header_rows": headers,
                "row_label_candidates": row_labels,
            })
    return {
        "table_signal_pages": out_pages,
        "map_build": "deterministic regex inventory over substrate text",
        "trust_policy": "source-derived; on conflict with text, prefer text",
    }


def call(text, part):
    body = {
        "model": MODEL, "temperature": 0, "max_tokens": 12000,  # map+claims arrays were truncating at 6000
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": STUDENT_PROMPT},
            {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"},
        ],
    }
    req = urllib.request.Request(
        TP4, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
    except Exception as exc:
        return None, time.time() - t0, f"{type(exc).__name__}: {exc}"[:120]
    dt = time.time() - t0
    c = d["choices"][0]["message"].get("content") or ""
    m = re.search(r"\[.*", c, re.DOTALL)
    if not m:
        return None, dt, "no_json_array"
    frag = m.group(0)
    try:
        claims = json.loads(frag)
        return (claims if isinstance(claims, list) else None), dt, None
    except json.JSONDecodeError:
        pass
    # salvage: truncated array — take complete objects up to the last brace
    # (burn.call's recovery pattern); mark as truncated_salvage
    cut = frag.rfind("}")
    if cut > 0:
        try:
            claims = json.loads(frag[:cut + 1] + "]")
            return claims, dt, "truncated_salvage"
        except Exception:
            pass
    return None, dt, "bad_json"


def alias_field(symbol):
    s = (symbol or "").lower()
    if "rds" in s:
        return "v_abs_max"
    if "vds" in s or "vin" in s:
        return "vin_max_v"
    if "id" == s.strip() or "iout" in s or "id_" in s:
        return "iout_max_a"
    if "tj" in s or "temp" in s:
        return "temp_max_c"
    return ""


def gate(claims, text):
    surv = p0 = 0
    for c in claims:
        if not isinstance(c, dict) or c.get("value") is None:
            continue
        q = c.get("quote") or ""
        if not q:
            p0 += 1
            continue
        ok = find_quote(q, text, field=alias_field(c.get("symbol", ""))) \
            and value_in_quote(c.get("value"), q)
        if ok and (c.get("column_header") or c.get("row_header")):
            from harness.pipeline.quote_verify import column_position_binds as cpb
            if cpb(c, q, text) is False:
                ok = False
        if ok:
            surv += 1
        else:
            p0 += 1
    return surv, p0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--classification", type=Path,
                    default=ROOT / "scratch/m5-soak/empty-classification.jsonl")
    ap.add_argument("--eligible-class", default="confirmed_recoverable")
    ap.add_argument("--sample", type=int, default=25)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "scratch/m5-soak/map-context-canary.json")
    args = ap.parse_args()

    rows = [json.loads(l) for l in args.classification.read_text().splitlines() if l.strip()]
    elig = [r for r in rows if r.get("eligible") == args.eligible_class]
    sample = elig[:args.sample]

    subs = json.loads((ROOT / "scratch/m5-soak/fixup-substrates.json").read_text())
    ra_subs = json.loads((ROOT / "scratch/m5-soak/ra03-substrates.json").read_text())
    for k, v in ra_subs.items():
        subs.setdefault(k, v)

    results = []
    recovered_docs = proposed = gated = p0_total = 0
    for rec in sample:
        stem = rec["stem"]
        sub_path = subs.get(stem)
        sub = json.loads(Path(sub_path).read_text())
        pages = [p.get("text") or "" for p in sub.get("pages", [])]
        text = "\n".join(pages)[:28000]
        part = sub.get("filename", stem).rsplit(".", 1)[0]
        mmap = build_map(pages)
        enriched = text + MAP_PREAMBLE + json.dumps(mmap, ensure_ascii=False)
        claims, dt, err = call(enriched, part)
        parse_note = err if claims is not None else None
        if claims is None:
            results.append({"stem": stem, "status": "model_empty_or_error",
                            "error": err, "latency_s": round(dt, 1),
                            "original_failure": rec.get("original_failure"),
                            "baseline_claims": rec.get("python_baseline_claims")})
            continue
        s, p0 = gate(claims, text)
        proposed += len([c for c in claims if isinstance(c, dict) and c.get("value") is not None])
        gated += s
        p0_total += p0
        if s > 0:
            recovered_docs += 1
        results.append({
            "stem": stem, "status": "recovered" if s > 0 else "gate_rejected_all",
            "claims_proposed": len(claims), "gate_surviving": s, "gate_p0": p0,
            "latency_s": round(dt, 1),
            "original_failure": rec.get("original_failure"),
            "baseline_claims": rec.get("python_baseline_claims"),
            "map_pages": len(mmap["table_signal_pages"]),
            "parse_note": parse_note,
        })
        print(f"{stem:34s} proposed={len(claims):3d} survived={s:3d} p0={p0:3d} {dt:.0f}s",
              flush=True)

    out = {
        "schema": "map-context-canary.v1",
        "eligible_class": args.eligible_class,
        "population_sampled": len(sample),
        "eligible_population_total": len(elig),
        "recovered_docs": recovered_docs,
        "recovery_rate": round(recovered_docs / max(1, len(sample)), 3),
        "claims_proposed": proposed,
        "claims_gate_surviving": gated,
        "claims_p0": p0_total,
        "note": "same model/prompt-schema/text-cap/gate as burn; only the "
                "deterministic context map added; $0 (TP4 local)",
        "rows": results,
    }
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in
                      ("eligible_class", "population_sampled",
                       "eligible_population_total", "recovered_docs",
                       "recovery_rate", "claims_proposed",
                       "claims_gate_surviving")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
