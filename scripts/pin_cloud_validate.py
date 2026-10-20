#!/usr/bin/env python3
"""Pin-lane cloud validation: OpenRouter deepseek-v4.1-flash vs TP4, same docs.

Same PIN_PROMPT, same substrate, same deterministic verifier (verbatim-quote
law). Reports: verifier pass rate, pin-name agreement vs TP4, real token
usage -> cost/doc -> extrapolation to the 37,912-doc general queue.
Variant B (enriched context) on a subset: L0 class + vendor + signal hint
prepended — the "send what we know before images" idea, measured.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path("/Users/samkim/Harnessv1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_pin_tables_tp4 import (  # noqa: E402
    MAX_CHARS,
    PIN_PROMPT,
    load_substrate_text,
    verify_rows,
    _norm,
)

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
CLOUD_MODEL = "deepseek/deepseek-v4.1-flash"
KEY = os.environ.get("OPENROUTER_API_KEY") or [
    l.split("=", 1)[1].strip() for l in open(ROOT / ".env") if l.startswith("OPENROUTER_API_KEY")
][0]

# assumed OpenRouter pricing (USD / M tokens) — confirm against current page
PRICE_IN = 0.30
PRICE_OUT = 1.10


def call_cloud(text: str, context: str = "") -> dict:
    prefix = f"<context>\n{context}\n</context>\n\n" if context else ""
    # proven call shape (MODEL_SETTINGS: reasoning low + 16K, or hidden
    # thinking eats the budget and content comes back empty/short)
    body = {
        "model": CLOUD_MODEL,
        "temperature": 0,
        "max_tokens": 16000,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "user",
                      "content": prefix + PIN_PROMPT + "\n\n<datasheet>\n" + text[:MAX_CHARS] + "\n</datasheet>"}],
    }
    req = urllib.request.Request(
        OPENROUTER, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
                 "HTTP-Referer": "https://harness.local", "X-Title": "harness-pin-validate"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.load(resp)


def pin_names(rows: list[dict]) -> set:
    return {_norm(r.get("name") or r.get("pin")) for r in rows if r.get("name") or r.get("pin")}


def main() -> int:
    sample = json.load(open("/tmp/tp4-ok-sample.json"))
    N_ENRICHED = 5
    results = []
    for i, doc in enumerate(sample):
        stem = Path(doc.get("source_path", "")).stem if doc.get("source_path") else doc["sha"]
        text = load_substrate_text(stem)
        if not text:
            # try the sha stem variant
            text = load_substrate_text(doc["sha"])
        if not text:
            results.append({"part": doc["part_number"], "error": "no_substrate"})
            continue
        try:
            resp = call_cloud(text)
        except Exception as exc:
            results.append({"part": doc["part_number"], "error": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        payload = {}
        try:
            c = resp["choices"][0]["message"]["content"] or ""
            c = re.sub(r"^```(?:json)?|```$", "", c.strip(), flags=re.M).strip()
            payload = json.loads(c[c.index("{"): c.rindex("}") + 1])
        except Exception:
            payload = {"rows": []}
        kept, dropped = verify_rows(payload, _norm(text))
        usage = resp.get("usage", {})
        cost = (usage.get("prompt_tokens", 0) * PRICE_IN + usage.get("completion_tokens", 0) * PRICE_OUT) / 1e6

        tp4_names = pin_names(doc.get("rows") or [])
        cloud_names = pin_names(kept)
        inter = tp4_names & cloud_names
        results.append({
            "part": doc["part_number"],
            "tp4_pins": len(tp4_names), "cloud_pins": len(cloud_names),
            "matched": len(inter),
            "tp4_only": len(tp4_names - cloud_names), "cloud_only": len(cloud_names - tp4_names),
            "cloud_verifier_kept": len(kept), "cloud_verifier_dropped": dropped,
            "has_pin_table_agree": bool(doc.get("rows")) == bool(payload.get("has_pin_table", True)),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "cost_usd": round(cost, 6),
        })

        # variant B: enriched context on the first N docs
        if i < N_ENRICHED:
            ctx = ("Document class: MCU datasheet. Extract only this package's pin rows; "
                   "pin tables live in the 'Pin Description/Pin Assignment' section.")
            try:
                resp_b = call_cloud(text, ctx)
                payload_b = json.loads(resp_b["choices"][0]["message"]["content"])
                kept_b, _ = verify_rows(payload_b, _norm(text))
                u = resp_b.get("usage", {})
                results[-1]["enriched_pins"] = len(pin_names(kept_b))
                results[-1]["enriched_prompt_tokens"] = u.get("prompt_tokens")
            except Exception as exc:
                results[-1]["enriched_error"] = str(exc)[:80]

    ok = [r for r in results if "error" not in r]
    agree = [r for r in ok if r["has_pin_table_agree"]]
    tot_in = sum(r.get("prompt_tokens") or 0 for r in ok)
    tot_out = sum(r.get("completion_tokens") or 0 for r in ok)
    tot_cost = sum(r.get("cost_usd") or 0 for r in ok)
    n = len(ok)
    print(json.dumps({
        "docs": len(results), "cloud_ok": n,
        "has_pin_table_agreement": f"{len(agree)}/{n}",
        "pin_name_agreement_pct": round(100 * sum(r["matched"] for r in ok) /
                                        max(1, sum(r["tp4_pins"] + r["cloud_only"] for r in ok)), 1),
        "cloud_rows_verifier_kept": sum(r["cloud_verifier_kept"] for r in ok),
        "cloud_rows_verifier_dropped": sum(r["cloud_verifier_dropped"] for r in ok),
        "avg_prompt_tokens": round(tot_in / max(1, n)),
        "avg_completion_tokens": round(tot_out / max(1, n)),
        "avg_cost_usd_per_doc": round(tot_cost / max(1, n), 5),
        "extrapolated_37912_docs_usd": round(tot_cost / max(1, n) * 37912, 2),
        "errors": [r for r in results if "error" in r][:5],
    }, indent=1))
    json.dump(results, open("/tmp/pin-cloud-validation.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
