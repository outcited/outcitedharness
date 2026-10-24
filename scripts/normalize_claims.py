#!/usr/bin/env python3
"""S3 — L3 normalization: verified claims → canonical units + names.

Deterministic transforms on verified rows:
  1. Unit canonicalization (µ/μ folding, mV→V, mA→A, °C/W stays)
  2. Mode-name canon (Run/RUN/run → run, Deep Sleep/DEEPSLEEP → deep_sleep)
  3. Rating-class separation (typ/min/max roles preserved, never conflated)
  4. Sig-fig policy (values kept as printed; normalization adds canonical_value)

usage: python3 normalize_claims.py --rows bound/thermal.bound.jsonl \
    --out normalized/thermal.norm.jsonl
"""
from __future__ import annotations
import argparse, json, sys, re
from collections import Counter
from pathlib import Path

# --- unit canonicalization ---
MICRO = {"μ": "µ", "μa": "µa", "μf": "µf", "μh": "µh", "μs": "µs", "μw": "µw"}

# mode name canon: measured 75 spellings fold to these
MODE_CANON = {
    "run": "run", "active": "run", "normal": "run",
    "sleep": "sleep", "light sleep": "sleep", "sleep mode": "sleep",
    "deep sleep": "deep_sleep", "deepsleep": "deep_sleep", "deep-sleep": "deep_sleep",
    "deep-sleep mode": "deep_sleep", "deep sleep mode current": "deep_sleep",
    "stop": "stop", "stop 0": "stop_0", "stop 1": "stop_1", "stop 2": "stop_2",
    "standby": "standby", "standby mode": "standby",
    "hibernate": "hibernate", "hibernate mode": "hibernate",
    "shutdown": "shutdown", "shutdown mode": "shutdown",
    "idle": "idle", "snooze": "snooze",
    "lpm": "lpm", "lpm3": "lpm3", "lpm3.5": "lpm3_5",
    "vbatt": "vbatt", "vbat": "vbatt", "bat": "vbatt",
    "current consumption (sleep mode current)": "sleep",
    "deep software standby": "deep_standby",
    "deep software standby mode": "deep_standby",
}

def canon_unit(unit: str | None) -> str | None:
    if not unit: return unit
    u = unit.strip().lower()
    for k, v in MICRO.items():
        u = u.replace(k, v)
    return u

def canon_mode(mode: str | None) -> str | None:
    if not mode: return mode
    m = " ".join(mode.strip().lower().split())
    return MODE_CANON.get(m, m.replace(" ", "_"))

def canon_value(value, unit: str | None) -> float | None:
    """Scale to the canonical unit (SI) for the quantity kind."""
    if value is None: return None
    try: v = float(value)
    except (TypeError, ValueError): return None
    u = canon_unit(unit) or ""
    factors = {"µa": 1e-6, "ua": 1e-6, "ma": 1e-3, "na": 1e-9, "a": 1.0,
               "mv": 1e-3, "v": 1.0, "kv": 1e3,
               "mω": 1e-3, "kω": 1e3, "mohm": 1e-3, "kohm": 1e3, "ω": 1.0, "ohm": 1.0,
               "mw": 1e-3, "w": 1.0, "kw": 1e3,
               "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "hz": 1.0,
               "°c/w": 1.0, "c/w": 1.0, "k/w": 1.0}
    if u in factors:
        return v * factors[u]
    return v  # dimensionless or unknown — keep as printed

def normalize_row(row: dict) -> dict:
    out = dict(row)
    out["_norm"] = {}
    # unit
    if row.get("unit"):
        out["_norm"]["unit_canon"] = canon_unit(row["unit"])
    # mode
    if row.get("mode"):
        out["_norm"]["mode_canon"] = canon_mode(row["mode"])
    # value
    cv = canon_value(row.get("value"), row.get("unit"))
    if cv is not None:
        out["_norm"]["canonical_value"] = cv
    # rating role (preserve, never conflate)
    role = row.get("value_role")
    if role:
        out["_norm"]["rating_role"] = role
    return out

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tally: Counter = Counter()
    for rp in args.rows:
        rp = Path(rp)
        out_path = out_dir / rp.name.replace(".bound.", ".norm.")
        out_fh = open(out_path, "w")
        for line in open(rp, errors="replace"):
            try: row = json.loads(line)
            except Exception: continue
            norm = normalize_row(row)
            out_fh.write(json.dumps(norm, ensure_ascii=True) + "\n")
            if norm.get("_norm", {}).get("canonical_value") is not None:
                tally["normalized_value"] += 1
            if norm.get("_norm", {}).get("unit_canon"):
                tally["unit_canon"] += 1
            if norm.get("_norm", {}).get("mode_canon"):
                tally["mode_canon"] += 1
            tally["rows"] += 1
        out_fh.close()
    print(json.dumps(dict(sorted(tally.items())), indent=1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
