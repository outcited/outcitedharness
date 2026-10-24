#!/usr/bin/env python3
"""reader_v3.py — LLM-assisted claim extraction for the not-recovered population.

Strategy: deterministic readers (v1/v2) produce synthetic verbatims for ~39K
claims. This reader sends page text to an LLM (TP4 local or QwenCloud) and
asks for claims with EXACT verbatim quotes + page numbers. Output is
CANDIDATE-ONLY — every claim must pass the hardened L2 verifier.

Architecture: sequential PDF reading (pymupdf process-safe), parallel API
calls (network thread-safe). No pymupdf in threads.
"""
import argparse, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pymupdf

BASE = os.environ.get("READER_V3_BASE", "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1")
MODEL = os.environ.get("READER_V3_MODEL", "deepseek-v4.1-flash")

PROMPT = """Extract printed engineering specifications from these datasheet pages.
For each spec, output a JSON array element with these keys:
  field: flash_kb, sram_kb, clk_max_mhz, supply_v, n_gpio, n_uart, n_i2c, n_spi,
         n_can, n_usb, n_adc, n_dma, n_timer, esd_hbm_v, esd_cdm_v, latchup_ma,
         temp_range_c, eeprom_kb, package, RthetaJA, RthetaJC, PsiJT, PsiJB,
         mode_current
  value: numeric value as printed
  unit: unit exactly as printed
  page_1based: page number from the === PAGE N === marker
  verbatim: EXACT text fragment (5-50 words, copy character-for-character)
  value_role: "typ" or "min" or "max" or null
Only extract values actually printed. If none, return []. JSON only."""

def env(k):
    if k == "QWEN_CLOUD_API_KEY":
        return os.environ.get("QWEN_CLOUD_API_KEY", "tp4-local-no-key")
    env_path = os.environ.get("HARNESS_ENV", os.path.join(os.path.dirname(__file__), "..", ".env"))
    try:
        for line in open(env_path):
            if line.startswith(k + "="):
                return line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return "tp4-local-no-key"

def extract_page(text, page_num, key):
    body = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": 4000,
        "reasoning": {"effort": "low"},
        "messages": [{"role": "user", "content": PROMPT + "\n\n" + text[:40000]}]
    }).encode()
    req = urllib.request.Request(BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read())
            content = res["choices"][0]["message"].get("content") or ""
            s, e = content.find("["), content.rfind("]")
            if s >= 0 and e > s:
                import json as _j
                rows = _j.loads(content[s:e+1])
                return rows if isinstance(rows, list) else []
            return []
        except Exception:
            if attempt == 2:
                return []
    return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = env("QWEN_CLOUD_API_KEY")
    q = json.loads(Path(args.queue).read_text())
    docs = q.get("documents") or q
    if args.limit:
        docs = docs[:args.limit]
    out = (out_dir / "claims-v3.jsonl").open("a")
    led = (out_dir / "ledger.jsonl").open("a")
    done = set()
    led_path = out_dir / "ledger.jsonl"
    if led_path.exists():
        for line in led_path.open():
            try:
                r = json.loads(line)
                if r.get("source_path"):
                    done.add(r["source_path"])
            except Exception:
                pass
    todo = [d for d in docs if d["source_path"] not in done]
    print(f"reader-v3: {len(todo)} docs, api-workers={args.workers}", flush=True)
    t0 = time.time()
    total = 0
    n = 0
    for d in todo:
        src = d["source_path"]
        try:
            doc = pymupdf.open(src)
        except Exception:
            led.write(json.dumps({"source_path": src, "status": "unreadable"}) + "\n")
            n += 1
            continue
        scored = []
        for i in range(min(doc.page_count, 60)):
            t = doc[i].get_text() or ""
            score = sum(t.count(k) for k in (
                "Min", "Max", "Typ", "Unit", "VDD", "MHz", "kHz", "kB",
                "mA", "\u00b5A", "\u00b0C", "C/W", "GPIO", "UART", "SPI",
                "I2C", "ADC", "DMA", "Flash", "SRAM"))
            if t.count("....") > 5 or "Table of Contents" in t:
                score = 0
            scored.append((score, i))
        scored.sort(reverse=True)
        pages = [i for s, i in scored[:8] if s > 5]
        batches = []
        for bs in range(0, len(pages), 3):
            batch = pages[bs:bs + 5]
            combined = ""
            for pi in batch:
                text = doc[pi].get_text() or ""
                if len(text.strip()) < 50:
                    continue
                combined += "\n=== PAGE " + str(pi + 1) + " ===\n" + text[:4000] + "\n"
            if combined.strip():
                batches.append(combined)
        doc.close()
        claims = []
        if batches:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                for rows in ex.map(lambda c: extract_page(c, 1, key), batches):
                    for r in (rows if isinstance(rows, list) else []):
                        if isinstance(r, dict) and r.get("field") and r.get("value") is not None:
                            r["source_path"] = src
                            claims.append(r)
        for c in claims:
            out.write(json.dumps(c, sort_keys=True) + "\n")
            total += 1
        led.write(json.dumps({"source_path": src,
            "status": "extracted" if claims else "no_specs"}) + "\n")
        n += 1
        if n % 10 == 0:
            out.flush()
            led.flush()
            print(f"{n}/{len(todo)} docs, {total} claims, {time.time()-t0:.0f}s", flush=True)
    out.close()
    led.close()
    print(json.dumps({"docs": n, "claims": total,
        "wall_s": round(time.time() - t0, 1)}), flush=True)

if __name__ == "__main__":
    main()
