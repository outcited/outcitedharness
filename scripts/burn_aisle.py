import os as _os
#!/usr/bin/env python3
"""First bounded production burn: one aisle, full cascade, every metric.

Usage: burn_aisle.py [--aisle power] [--limit N] [--dry-run]

Pipeline per doc: substrate -> pillar route -> student (text pillars) ->
V4.1 (routed vision pages) -> deterministic verify -> repair rounds ->
ledger rows + summary report. Power aisle = exports/power-datasheet-pairs.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/samkim/Harnessv1")
from harness.pipeline import store
from harness.pipeline.quote_verify import verify_claims

RESULTS = Path("/Volumes/M5_4TB/extract-results")
ROUTES = RESULTS / "routes"
SUBSTRATE_DIRS = [RESULTS / "collected", RESULTS / "runs"]
BURN_OUT = RESULTS / (_os.environ.get("BURN_OUT_NAME", "burn-power-v1"))

STUDENT = "http://100.81.201.24:8950/v1/chat/completions"
DGX1_STUDENT = "http://100.81.201.24:8950/v1/chat/completions"
V41 = "http://100.116.221.82:8888/v1/chat/completions"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
CLOUD_MODEL = "deepseek/deepseek-v4.1-flash"
POWER_ROOT = Path("/Volumes/M5_4TB/exports/power-datasheet-pairs")
MCU_ROOT = Path("/Volumes/macbookM4-4TB/datasheet-corpus/mcu")
VAULT_ROOT = Path("/Volumes/M5_4TB/vault/landing")
AISLE_ROOTS = {"power": POWER_ROOT, "mcu": MCU_ROOT, "vault": VAULT_ROOT}

STUDENT_PROMPT = """You are a datasheet parametric extraction engine. Given datasheet pages,
extract the parametric table rows for the requested part. Every value MUST carry a quote
copied character-for-character from the text, plus the ROW and COLUMN headers that govern
the cell — if you cannot quote it or name its row/column, emit null.
Output ONLY JSON: [{"symbol": str, "value": number, "unit": str, "qualifier": str|null,
"condition": str|null, "quote": "<verbatim span>", "row_header": "<row label from the table>",
"column_header": "<column label: Min/Typ/Max/parameter name>"}]"""

REPAIR = """The verifier rejected these fields (reason codes attached): %s
Fix ONLY the quotes and headers to be character-for-character copies supporting the SAME
value, or null the field. Never change values. If rejected as wrong_cell, the value sits in
a different column than claimed — find the correct row/column headers or null.
Return ONLY the corrected JSON array."""


import os as _os
PREFER_LOCAL = _os.environ.get("BURN_PREFER_LOCAL", "0") == "1"

LOCAL_CHAIN = [
    (V41, "deepseek-v4.1-flash"),
    (OPENROUTER, CLOUD_MODEL),
]
CLOUD_CHAIN = [
    (OPENROUTER, CLOUD_MODEL),
    (V41, "deepseek-v4.1-flash"),
]


def propose(text, part):
    chain = LOCAL_CHAIN if PREFER_LOCAL else CLOUD_CHAIN
    for endpoint, model in chain:
        try:
            claims = call(endpoint, model, [
                {"role": "system", "content": STUDENT_PROMPT},
                {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"}])
        except Exception:
            claims = None
        if isinstance(claims, list):
            tier = "local-v41" if endpoint == V41 else "cloud-v41"
            return claims, tier
    return None, None


def call(endpoint, model, messages, timeout=240):
    headers = {"Content-Type": "application/json"}
    if "openrouter" in endpoint:
        key = [l.split("=", 1)[1].strip() for l in open("/Users/samkim/Harnessv1/.env")
               if l.startswith("OPENROUTER_API_KEY")][0]
        headers.update({"Authorization": f"Bearer {key}",
                        "HTTP-Referer": "https://localhost/harness", "X-Title": "burn"})
        from harness.pipeline import store as _store
        _con = _store.connect()
        ok, spend = _store.budget_check(_con, "burn-cloud")
        if not ok:
            raise RuntimeError("BURN_DAILY_CAP_REACHED")
    body = {"model": model, "temperature": 0, "max_tokens": 16000,
            "reasoning": {"effort": "low"}, "messages": messages}
    if "openrouter" not in endpoint:
        body.pop("reasoning", None)
        body["max_tokens"] = 6000
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    msg = d["choices"][0]["message"]
    c = msg.get("content") or msg.get("reasoning") or ""
    if "openrouter" in endpoint:
        cost = d.get("usage", {}).get("cost")
        if cost is not None:
            _con.execute(
                "INSERT INTO adjudication_ledger (judge, severity, verdict, reason_code,"
                " created_at) VALUES ('cost-meter','P4','COST', ?, ?)",
                (str(cost), time.time()))
            _con.commit()
    c = re.sub(r"```(?:json)?", "", c).strip()
    m = re.search(r"(\[.*\]|\{.*\})", c, re.DOTALL)
    if not m:
        return None
    frag = m.group(0)
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        cut = frag.rfind("}")
        if cut > 0:
            try:
                return json.loads(frag[: cut + 1] + "]")
            except json.JSONDecodeError:
                return None
    return None


def load_substrate(stem):
    for d in SUBSTRATE_DIRS:
        for jf in d.rglob(f"{stem}.json"):
            try:
                return json.loads(jf.read_text())
            except Exception:
                return None
    return None


def burn_doc(stem, source_path, route, con, job_meta):
    sub = load_substrate(stem)
    if sub is None:
        return {"stem": stem, "skip": "no_substrate"}
    text = "\n".join(p.get("text", "") for p in sub.get("pages", []))[:28000]
    part = sub.get("filename", stem).rsplit(".", 1)[0]

    try:
        claims = call(STUDENT, "power-tables-student-v1", [
            {"role": "system", "content": STUDENT_PROMPT},
            {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"}])
        tier = "student" if isinstance(claims, list) else None
    except Exception:
        claims = None
        tier = None
    if not isinstance(claims, list):
        claims, tier = propose(text, part)
    if not isinstance(claims, list) or not claims:
        return {"stem": stem, "skip": "no_claims"}

    for rnd in range(2):
        bad = [i for i, c in enumerate(claims)
               if isinstance(c, dict) and c.get("value") is not None
               and not _quick_ok(c, text)]
        if not bad:
            break
        fixes = call(V41, "deepseek-v4.1-flash", [
            {"role": "system", "content": REPAIR % json.dumps(
                [{"i": i, "reason": "quote_mismatch"} for i in bad])},
            {"role": "user", "content": f"Claims:\n{json.dumps([claims[i] for i in bad])}\n\nText:\n{text}"}])
        if isinstance(fixes, list):
            for f in fixes:
                if isinstance(f, dict) and "i" in f:
                    claims[f["i"]] = {**claims[f["i"]], **{k: v for k, v in f.items() if k != "i"}}

    verdicts = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("value") is None:
            continue
        q = c.get("quote") or ""
        if not q:
            verdicts.append({"i": i, "verdict": "UNSUPPORTED", "reason": "unquoted", "severity": "P0"})
            continue
        from harness.pipeline.quote_verify import find_quote, value_in_quote, column_position_binds
        ok = find_quote(q, text, field=_alias_field(c.get("symbol", ""))) and value_in_quote(c.get("value"), q)
        if ok and (c.get("column_header") or c.get("row_header")):
            ordinal = column_position_binds(c, q, text)
            if ordinal is False:
                ok = False
        verdicts.append({"i": i, "verdict": "SUPPORTED" if ok else "UNSUPPORTED",
                         "reason": None if ok else ("wrong_cell" if not ok and c.get("column_header") else "quote_not_in_doc"),
                         "severity": "P2" if ok else "P0"})

    p0_idx = {v["i"] for v in verdicts if v["severity"] == "P0"}
    surviving_claims = [c for i, c in enumerate(claims)
                        if isinstance(c, dict) and i not in p0_idx and c.get("value") is not None]
    return {"stem": stem, "tier": tier, "claims": len(claims),
            "surviving": len(surviving_claims),
            "p0": sum(1 for v in verdicts if v["severity"] == "P0"),
            "supported": sum(1 for v in verdicts if v["verdict"] == "SUPPORTED"),
            "claim_data": surviving_claims}


def _quick_ok(c, text):
    from harness.pipeline.quote_verify import find_quote
    return bool(c.get("quote")) and find_quote(c["quote"], text, field=_alias_field(c.get("symbol", "")))


def _alias_field(symbol):
    s = (symbol or "").lower()
    if "rds" in s: return "v_abs_max"
    if "vds" in s or "vin" in s: return "vin_max_v"
    if "id" == s.strip() or "iout" in s or "id_" in s: return "iout_max_a"
    if "tj" in s or "temp" in s: return "temp_max_c"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aisle", default="power")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=25)
    args = ap.parse_args()

    BURN_OUT.mkdir(parents=True, exist_ok=True)
    con = store.connect()
    root = AISLE_ROOTS[args.aisle]
    pattern = "*/pdf/*.pdf" if args.aisle == "power" else "**/*.pdf"
    all_pdfs = sorted(root.glob(pattern))
    pdfs = [p for p in all_pdfs if not (BURN_OUT / f"{p.stem}.json").exists()][: args.limit]
    print(f"burn: aisle={args.aisle} docs={len(pdfs)} (of {len(all_pdfs)} total)", flush=True)
    if args.dry_run:
        for p in pdfs[:5]:
            print(" would burn:", p.name)
        return
    import concurrent.futures
    tally = Counter()
    t0 = time.time()
    done_count = 0
    lock = __import__("threading").Lock()

    def run_doc(p):
        route = {"route": {"vision_pages": []}}
        try:
            return p, burn_doc(p.stem, str(p), route, con, None)
        except Exception as e:
            return p, {"stem": p.stem, "skip": f"error:{str(e)[:60]}"}

    with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
        for p, res in ex.map(run_doc, pdfs):
            tally[res.get("tier", res.get("skip", "?"))] += 1
            tally["p0"] += res.get("p0", 0)
            tally["claims"] += res.get("claims", 0)
            with (BURN_OUT / f"{p.stem}.json").open("w") as f:
                json.dump(res, f, ensure_ascii=False)
            done_count += 1
            if done_count % 25 == 0:
                print(f"{done_count}/{len(pdfs)} {dict(tally)} elapsed={time.time()-t0:.0f}s", flush=True)
    report = {"aisle": args.aisle, "docs": len(pdfs), "elapsed_s": round(time.time() - t0, 1),
              "tally": dict(tally),
              "p0_rate": round(tally["p0"] / max(1, tally["claims"]), 4)}
    (BURN_OUT / "_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
