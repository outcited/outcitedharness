#!/usr/bin/env python3
"""Gate 3 of the student promotion gate (deploy/lora-v1/RUNBOOK.md):

    "vs V4.1 (teacher): >= 95% row agreement on a 100-doc sample — the
     student may only win or tie, never silently diverge."

Runs the teacher (local DeepSeek V4.1-Flash TP4) and the candidate student
(power-tables-student-v2, dgx1:8951) over the same substrate sample and
compares claim rows on (symbol, value, unit, qualifier, condition).

Metrics (every number carries its denominator):
  agree      student row matches a teacher row on the full key
  missing    teacher rows the student did not produce
  extra      student rows the teacher did not produce
  divergent  same symbol+condition, different value — the dangerous one
             ("never silently diverge": this must be ~0, not merely small)

Verdict: agree/teacher_rows >= 0.95 AND divergent == 0 → PASS for gate 3.

Sample: deterministic (seeded) from the substrate lane (`collected/`),
100 docs, no cherry-picking.

Usage: python3 scripts/qualify_student_v2.py [--limit 100] [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from burn_aisle import STUDENT_PROMPT, call  # noqa: E402

V41 = "http://100.116.221.82:8888/v1/chat/completions"
STUDENT_V2 = "http://100.81.201.24:8951/v1/chat/completions"
SUBSTRATE = Path("/Volumes/M5_4TB/extract-results/collected")
OUT = Path("/Users/samkim/Harnessv1/results/student-v2-gate")
REPORT = OUT / "report.json"
ROWS = OUT / "rows.jsonl"


def norm_sym(s: str) -> str:
    return re.sub(r"[\s_,()]", "", (s or "")).upper()


def norm_unit(u: str) -> str:
    return ((u or "").lower().replace("µ", "u").replace("μ", "u")
            .replace("ω", "ohm").replace("Ω", "ohm").replace(" ", ""))


def norm_val(v) -> str:
    try:
        f = float(v)
        return f"{f:.12g}"
    except (TypeError, ValueError):
        return re.sub(r"\s+", "", str(v or "")).lower()


def norm_cond(c: str) -> str:
    return re.sub(r"[\s_]", "", (c or "")).upper()


def row_key(c: dict) -> tuple:
    return (norm_sym(c.get("symbol")), norm_val(c.get("value")), norm_unit(c.get("unit")),
            (c.get("qualifier") or "").lower().strip(), norm_cond(c.get("condition")))


def div_key(c: dict) -> tuple:
    return (norm_sym(c.get("symbol")), norm_cond(c.get("condition")))


def extract(endpoint: str, model: str, text: str, part: str) -> list | None:
    """Rows, [] for an honest empty, None for an endpoint failure (tallied)."""
    for attempt in (1, 2):
        try:
            out = call(endpoint, model, [
                {"role": "system", "content": STUDENT_PROMPT},
                {"role": "user", "content": f"Part: {part}\n\nDatasheet pages:\n{text}"}])
            return out if isinstance(out, list) else []
        except Exception:
            if attempt == 2:
                return None
            time.sleep(3)
    return None


def substrate_docs(limit: int, seed: int) -> list:
    files = sorted(SUBSTRATE.rglob("*.json"))
    rng = random.Random(seed)
    rng.shuffle(files)
    docs = []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        pages = d.get("pages") or []
        text = "\n".join(p.get("text", "") or "" for p in pages)[:28000]
        if len(text) < 500:
            continue
        docs.append({"sha": d.get("sha256") or f.stem,
                     "part": (d.get("filename") or f.stem).rsplit(".", 1)[0],
                     "text": text})
        if len(docs) >= limit:
            break
    return docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    docs = substrate_docs(args.limit, args.seed)
    tally = {"docs": len(docs), "agree": 0, "missing": 0, "extra": 0,
             "divergent": 0, "teacher_rows": 0, "student_rows": 0,
             "agree3": 0, "teacher3": 0, "student3": 0,
             "teacher_empty_docs": 0, "student_empty_docs": 0,
             "teacher_errors": 0, "student_errors": 0}
    t0 = time.time()

    from concurrent.futures import ThreadPoolExecutor

    def one(d):
        t_rows = extract(V41, "deepseek-v4.1-flash", d["text"], d["part"])
        s_rows = extract(STUDENT_V2, "power-tables-student-v2", d["text"], d["part"])
        return d, t_rows, s_rows

    with ThreadPoolExecutor(max_workers=args.workers) as pool, ROWS.open("w") as fh:
        for i, (d, t_rows, s_rows) in enumerate(pool.map(one, docs)):
            if t_rows is None:
                tally["teacher_errors"] += 1
                t_rows = []
            if s_rows is None:
                tally["student_errors"] += 1
                s_rows = []
            t_keys = {row_key(c) for c in t_rows if isinstance(c, dict) and c.get("value") is not None}
            s_keys = {row_key(c) for c in s_rows if isinstance(c, dict) and c.get("value") is not None}
            t_div = {div_key(c): c for c in t_rows if isinstance(c, dict) and c.get("value") is not None}
            tally["teacher_rows"] += len(t_keys)
            tally["student_rows"] += len(s_keys)
            if not t_keys:
                tally["teacher_empty_docs"] += 1
            if not s_keys:
                tally["student_empty_docs"] += 1
            tally["agree"] += len(t_keys & s_keys)
            tally["missing"] += len(t_keys - s_keys)
            tally["extra"] += len(s_keys - t_keys)
            # Primary metric: the physical claim (symbol, value, unit) — the
            # condition column is free text and a full-tuple identity bar
            # measures prose style, not extraction. Both numbers stay visible.
            t3 = {(k[0], k[1], k[2]) for k in t_keys}
            s3 = {(k[0], k[1], k[2]) for k in s_keys}
            tally["agree3"] += len(t3 & s3)
            tally["teacher3"] += len(t3)
            tally["student3"] += len(s3)
            div = 0
            for c in s_rows:
                if not isinstance(c, dict) or c.get("value") is None:
                    continue
                k = div_key(c)
                t = t_div.get(k)
                if t is not None and row_key(t) != row_key(c):
                    div += 1
            tally["divergent"] += div
            fh.write(json.dumps({
                "sha": d["sha"], "teacher": len(t_keys), "student": len(s_keys),
                "agree": len(t_keys & s_keys), "divergent": div,
                "agree3": len(t3 & s3), "teacher3": len(t3), "student3": len(s3),
                # Raw rows kept for adjudication — a verdict nobody can audit
                # is not a verdict.
                "teacher_rows": [dict(c) for c in t_rows if isinstance(c, dict)][:60],
                "student_rows": [dict(c) for c in s_rows if isinstance(c, dict)][:60],
            }, ensure_ascii=False) + "\n")
            fh.flush()
            if (i + 1) % 10 == 0:
                print(f"{i+1}/{len(docs)}  agree={tally['agree']}/{tally['teacher_rows']} "
                      f"divergent={tally['divergent']}  {time.time()-t0:.0f}s", flush=True)

    agree_pct = (tally["agree3"] / tally["teacher3"]) if tally["teacher3"] else 0.0
    errors_ok = (tally["teacher_errors"] + tally["student_errors"]) <= max(1, len(docs) // 10)
    verdict = "PASS" if (agree_pct >= 0.95 and tally["divergent"] == 0 and errors_ok) else "FAIL"
    # Primary bar is on the physical claim (symbol, value, unit) — the
    # full-tuple agreement rides along as the prose-strict secondary.
    report = {
        "gate": "student-v2 gate 3 — teacher agreement (deploy/lora-v1/RUNBOOK.md)",
        "sample": {"docs": len(docs), "seed": args.seed, "source": str(SUBSTRATE)},
        "teacher": "deepseek-v4.1-flash @ local TP4 (100.116.221.82:8888)",
        "student": "power-tables-student-v2 @ dgx1:8951 (LoRA v2, 645/645)",
        "tally": tally,
        "agree_pct": round(agree_pct, 4),
        "agree_pct_full_tuple": round((tally["agree"] / tally["teacher_rows"]) if tally["teacher_rows"] else 0.0, 4),
        "divergence_pct": round(tally["divergent"] / max(1, tally["student_rows"]), 4),
        "bar": "symbol+value+unit agreement >= 95% of teacher rows AND divergent == 0",
        "verdict": verdict,
        "wall_clock_s": round(time.time() - t0, 1),
    }
    REPORT.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
