#!/usr/bin/env python3
"""BCMP scenario generator — TP4 (dgx2:8888, deepseek-v4.1-flash).

Demand-seeded MCU engineering questions, emitted as strict JSON with
*proposed atoms*. Validation mirrors the engine-side knife declaration
(single source: the knife-spec JSON emitted by `dw-bcmp-run
--emit-knife-spec`); nothing the model says becomes scorable without passing
it. Duplicate atom sets are skipped; their wordings are preserved in
variants.jsonl for semantic training (synthetic intent, kept separate from
observed demand).

Settings law (MODEL_SETTINGS.md, TP4 row):
  chat_template_kwargs {"thinking": false}, max_tokens 6000.

Concurrency: default 4 (shared-endpoint headroom law; measured ceiling 8).

Usage:
  bcmp_generate.py --job-dir DIR --knife-spec spec.json --feed pillars.m5.jsonl \
      [--seeds holes.json] [--endpoint http://192.168.4.45:8888] \
      [--max-unique 300] [--max-minutes 600] [--conc 4] [--selftest]
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import urllib.request

AXIS_UNIT = {}  # axis -> unit, from knife spec
AXIS_RANGE = {}  # axis -> (min, max)
AXIS_OP = {}
VALID_AXES = set()
PARKED_AXES = {}  # axis -> reason (engine-known but undeclared here)


def load_spec(path: str) -> None:
    global AXIS_UNIT, AXIS_RANGE, AXIS_OP, VALID_AXES, PARKED_AXES
    spec = json.load(open(path))
    for k in spec["knives"]:
        AXIS_OP[k["axis"]] = k["op"]
        AXIS_UNIT[k["axis"]] = k["unit"]
        AXIS_RANGE[k["axis"]] = (float(k["value_min"]), float(k["value_max"]))
    VALID_AXES = set(AXIS_OP)
    PARKED_AXES = dict(spec.get("parked_axes", {}))


def canonical_value(v: float) -> float:
    return round(v * 10000.0) / 10000.0


def canonical_key(atoms: list[dict]) -> str:
    canon = sorted(
        (
            {
                "axis": a["axis"],
                "op": AXIS_OP[a["axis"]],
                "value": canonical_value(float(a["value"])),
                "unit": AXIS_UNIT[a["axis"]],
            }
            for a in atoms
            if a.get("hard", True) and a["axis"] in VALID_AXES
        ),
        key=lambda a: (a["axis"], a["op"]),
    )
    return hashlib.sha256(
        json.dumps(canon, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def existing_keys(job_dir: str) -> set[str]:
    """Canonical keys already in the job's scenario files (restart safety)."""
    keys: set[str] = set()
    sdir = os.path.join(job_dir, "scenarios")
    if not os.path.isdir(sdir):
        return keys
    for name in sorted(os.listdir(sdir)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(sdir, name)) as h:
            for line in h:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                hard = [a for a in row.get("proposed_atoms", []) if a.get("hard", True)]
                if hard:
                    keys.add(canonical_key(row["proposed_atoms"]))
    return keys


def feed_histograms(feed_path: str) -> dict:
    """Observed value distributions from the released feed (demand grounding:
    thresholds the corpus actually prints — observed sources only)."""
    fields = ("flash_kb", "clk_max_mhz", "n_can", "n_usb", "temp_range_c")
    hist = {f: collections.Counter() for f in fields}
    mfrs = collections.Counter()
    with open(feed_path) as h:
        for line in h:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("identity_status") != "stamped":
                continue
            if r.get("_verify", {}).get("verdict") != "verified":
                continue
            f = r.get("field")
            v = r.get("value")
            if f in hist and isinstance(v, (int, float)) and math.isfinite(v):
                hist[f][v] += 1
            if r.get("manufacturer"):
                mfrs[r["manufacturer"]] += 1
    out = {}
    for f, c in hist.items():
        top = [v for v, _ in c.most_common(12)]
        out[f] = top
    out["_manufacturers"] = [m for m, _ in mfrs.most_common(12)]
    return out


def build_prompt(seed: dict, hist: dict, idx: int) -> tuple[str, str]:
    """Return (system, user) prompts for one scenario."""
    system = (
        "You write one realistic embedded-engineering part-selection question "
        "per call, as strict JSON. You propose hard requirement atoms ONLY "
        "from the HONORED knife list, with plausible engineering values. "
        "You may also name requirements outside that list in the question "
        "text, but NEVER invent atoms for them — unsupported requirements "
        "belong in the question prose only. Output ONE JSON object, no "
        "markdown, no commentary."
    )
    honored = [
        {
            "axis": ax,
            "op": AXIS_OP[ax],
            "unit": AXIS_UNIT[ax],
            "value_band": [AXIS_RANGE[ax][0], AXIS_RANGE[ax][1]],
        }
        for ax in sorted(VALID_AXES)
    ]
    user = json.dumps(
        {
            "task": "Write ONE MCU selection question with 1-3 hard atoms.",
            "honored_knives": honored,
            "realistic_values_observed_in_corpus": {
                "flash_kb": hist.get("flash_kb", [])[:10],
                "cpu_mhz": hist.get("clk_max_mhz", [])[:10],
                "can_counts": hist.get("n_can", [])[:6],
                "usb_counts": hist.get("n_usb", [])[:6],
            },
            "demand_seed": seed,
            "variation": idx,
            "output_schema": {
                "question": "one-sentence engineer question, concrete application",
                "occasion": "short occasion phrase",
                "applications": ["0-2 bench-able end products"],
                "atoms": [
                    {
                        "axis": "one of honored_knives axes",
                        "value": "number inside the band",
                        "hard": True,
                    }
                ],
            },
            "rules": [
                "values must sit inside the given band",
                "1 to 3 atoms, all hard",
                "at least one atom must be flash_kb or freq_mhz",
                "no axes outside honored_knives",
            ],
        },
        separators=(",", ":"),
    )
    return system, user


def call_tp4(endpoint: str, model: str, system: str, user: str, timeout: int = 120) -> str:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.85,
            "max_tokens": 6000,
            "chat_template_kwargs": {"thinking": False},
        }
    ).encode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"] or ""


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def validate_atoms(raw: dict) -> tuple[list[dict], list[dict]]:
    """Mirror of the engine-side validator. Returns (honored, parked)."""
    honored, parked = [], []
    for a in raw.get("atoms", []):
        axis = a.get("axis")
        if axis not in VALID_AXES:
            parked.append(
                {
                    "proposal": a,
                    "code": "unsupported_axis" if axis not in PARKED_AXES else "undeclared_knife",
                    "why": PARKED_AXES.get(axis, "axis not in dw-decide registry"),
                }
            )
            continue
        if not a.get("hard", True):
            parked.append({"proposal": a, "code": "soft_atom", "why": "pilot scores hard atoms"})
            continue
        try:
            v = float(a.get("value"))
        except (TypeError, ValueError):
            parked.append({"proposal": a, "code": "non_numeric_value", "why": "value not numeric"})
            continue
        lo, hi = AXIS_RANGE[axis]
        if not (lo <= v <= hi):
            parked.append(
                {"proposal": a, "code": "value_out_of_range", "why": f"{v} outside [{lo},{hi}]"}
            )
            continue
        honored.append(
            {
                "axis": axis,
                "op": AXIS_OP[axis],
                "value": v,
                "unit": AXIS_UNIT[axis],
                "hard": True,
            }
        )
    return honored, parked


def load_seeds(path: str | None) -> list[dict]:
    seeds = []
    if path and os.path.exists(path):
        h = json.load(open(path))
        yellow = h.get("yellow_list")
        if isinstance(yellow, list):
            for y in yellow:
                if isinstance(y, dict) and y.get("phrase"):
                    seeds.append({"occasion": y["phrase"]})
                elif isinstance(y, str):
                    seeds.append({"occasion": y})
        elif isinstance(yellow, dict):
            # The measured yellow-list shape: facet name -> coverage stats.
            # These are observed coverage holes (real demand surface); seed
            # one scenario per under-covered facet.
            for facet, stats in yellow.items():
                frac = stats.get("fraction", 1.0) if isinstance(stats, dict) else 1.0
                seeds.append(
                    {
                        "occasion": f"application leaning on {facet.replace('_', ' ')}",
                        "facet": facet,
                        "corpus_filled_fraction": frac,
                    }
                )
        if h.get("goal"):
            seeds.append({"occasion": None, "goal": h["goal"][:200]})
    # Always allow the empty seed (free variation).
    seeds.append({"occasion": None})
    return seeds or [{"occasion": None}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--knife-spec", required=True)
    ap.add_argument("--feed", required=True)
    ap.add_argument("--seeds")
    ap.add_argument("--endpoint", default="http://192.168.4.45:8888")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--max-unique", type=int, default=300)
    ap.add_argument("--max-minutes", type=float, default=600.0)
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    load_spec(args.knife_spec)
    os.makedirs(os.path.join(args.job_dir, "scenarios"), exist_ok=True)
    hist = feed_histograms(args.feed)
    seeds = load_seeds(args.seeds)
    done = existing_keys(args.job_dir)
    stop_path = os.path.join(args.job_dir, "STOP")

    out_path = os.path.join(
        args.job_dir, "scenarios", f"gen-{time.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    parked_f = open(os.path.join(args.job_dir, "parked.log.jsonl"), "a")
    variants_f = open(os.path.join(args.job_dir, "variants.jsonl"), "a")
    out_f = open(out_path, "a")

    rng = random.Random(20261022)
    deadline = time.time() + args.max_minutes * 60
    calls = 0
    written = 0
    dupes = 0
    parked_atoms = 0
    errors = 0
    seq = 0

    def one_call(i: int) -> dict | None:
        seed = seeds[i % len(seeds)]
        system, user = build_prompt(seed, hist, i)
        prompt_sha = hashlib.sha256((system + user).encode()).hexdigest()
        text = call_tp4(args.endpoint, args.model, system, user)
        return {"raw": extract_json(text), "prompt_sha256": prompt_sha, "text": text}

    print(
        f"bcmp-generate: existing_unique={len(done)} max_unique={args.max_unique} "
        f"conc={args.conc} endpoint={args.endpoint}",
        flush=True,
    )

    while not args.selftest:
        if os.path.exists(stop_path):
            print("STOP file present; halting generator", flush=True)
            break
        if len(done) >= args.max_unique:
            print("unique target reached", flush=True)
            break
        if time.time() > deadline:
            print("time budget reached", flush=True)
            break
        if args.max_calls and calls >= args.max_calls:
            print("call budget reached", flush=True)
            break

        batch = min(args.conc * 2, args.max_unique - len(done) + args.conc)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.conc) as ex:
            results = list(ex.map(one_call, range(calls, calls + batch)))
        calls += batch

        for r in results:
            if len(done) >= args.max_unique or time.time() > deadline:
                break
            if not r or not r["raw"]:
                errors += 1
                parked_f.write(
                    json.dumps({"code": "model_output_unparseable", "row": {"text": (r or {}).get("text", "")[:400]}})
                    + "\n"
                )
                continue
            raw = r["raw"]
            honored, parked = validate_atoms(raw)
            parked_atoms += len(parked)
            for p in parked:
                parked_f.write(json.dumps({"code": p["code"], "row": p}) + "\n")
            if not honored:
                parked_f.write(
                    json.dumps({"code": "no_scorable_atoms", "row": {"question": raw.get("question", "")[:200]}})
                    + "\n"
                )
                continue
            seq += 1
            key = canonical_key(honored)
            row = {
                "scenario_id": f"tp4-{time.strftime('%Y%m%d')}-{seq:04d}-{key[:8]}",
                "question": raw.get("question", ""),
                "occasion": raw.get("occasion"),
                "applications": raw.get("applications", [])[:2],
                "proposed_atoms": honored,
                "generated_by": {
                    "source": "tp4",
                    "model": args.model,
                    "endpoint": args.endpoint,
                    "prompt_sha256": r["prompt_sha256"],
                },
            }
            if key in done:
                dupes += 1
                variants_f.write(json.dumps({"atom_set_sha256": key, "scenario": row}) + "\n")
                continue
            done.add(key)
            out_f.write(json.dumps(row) + "\n")
            out_f.flush()
            written += 1
        print(
            f"calls={calls} unique={len(done)} written={written} dupes={dupes} "
            f"parked_atoms={parked_atoms} errors={errors}",
            flush=True,
        )

    out_f.close()
    parked_f.close()
    variants_f.close()
    print(
        json.dumps(
            {
                "calls": calls,
                "unique": len(done),
                "written": written,
                "dupes": dupes,
                "parked_atoms": parked_atoms,
                "errors": errors,
                "out": out_path,
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
