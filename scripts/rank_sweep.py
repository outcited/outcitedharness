#!/usr/bin/env python3
"""rank_sweep — Layer 1: deterministic rank boards over catalog-live.db.

Sam directive 2026-10-22: a rank board is a materialized knife. Sweep every
attribute symbol (>= MIN_CLAIMS, synonym-collapsed) x direction x cohort
(all-parts, automotive-AEC). Zero model calls. Evidence on every row.

Output: OUT_DIR/boards.v1/<slug>.json + index.jsonl with knife definitions.
Recompute per release (catalog mtime stamped into each board).
"""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB = os.environ.get("CATALOG_DB", "/Users/samkim/Harnessv1/results/catalog-live.db")
OUT = Path(os.environ.get("RANKBOARD_DIR", "/Users/samkim/lane-out/rankboards"))
MIN_CLAIMS = int(os.environ.get("RANKBOARD_MIN_CLAIMS", "100"))
TOP_N = 100

# v1 synonym collapse: variant vendor phrasings -> canonical symbol group.
# Honest: unmapped symbols pass through verbatim (never dropped).
SYNONYMS = {
    "CURRENT_DEEP_SLEEP": "CURRENT_DEEPSLEEP",
    "CURRENT_DEEP_SLEEP_MODE": "CURRENT_DEEPSLEEP",
    "CURRENT_POWER_DOWN_CURRENT": "CURRENT_POWER_DOWN",
    "CURRENT_SHUTDOWN_CURRENT": "CURRENT_SHUTDOWN",
    "CURRENT_LOW_POWER_RUN": "CURRENT_LPRUN",
    "C ISS": "CISS", "C OSS": "COSS", "C RSS": "CRSS",
    "CO(ER)": "CO_ER", "CO(TR)": "CO_TR",
}


UNIT_CANON = {  # canonical string -> SI scale
    "ua": ("uA", 1e-6), "\u00b5a": ("uA", 1e-6), "\u03bca": ("uA", 1e-6),
    "na": ("nA", 1e-9), "ma": ("mA", 1e-3), "a": ("A", 1.0),
    "c/w": ("C/W", 1.0), "k/w": ("C/W", 1.0), "\u00b0c/w": ("C/W", 1.0),
    "mw": ("mW", 1e-3), "w": ("W", 1.0), "uw": ("uW", 1e-6),
    "ns": ("ns", 1e-9), "us": ("us", 1e-6), "ms": ("ms", 1e-3), "s": ("s", 1.0),
    "pf": ("pF", 1e-12), "nf": ("nF", 1e-9), "uf": ("uF", 1e-6), "pf(f)": ("pF", 1e-12), "ff": ("fF", 1e-15), "mf": ("mF", 1e-3), "f": ("F", 1.0),
    "nc": ("nC", 1e-9), "uc": ("uC", 1e-6), "mc": ("mC", 1e-3),
    "mv": ("mV", 1e-3), "v": ("V", 1.0), "uv": ("uV", 1e-6),
    "\u00b0c": ("C", 1.0), "c": ("C", 1.0), "k": ("K", 1.0), "\u00b0f": ("F", 1.0),
    "mhz": ("MHz", 1e6), "khz": ("kHz", 1e3), "hz": ("Hz", 1.0),
    "mj": ("mJ", 1e-3), "uj": ("uJ", 1e-6), "j": ("J", 1.0),
    "mohm": ("mOhm", 1e-3), "ohm": ("Ohm", 1.0), "\u03a9": ("Ohm", 1.0),
    "mohm(s)": ("mOhm", 1e-3),
}
NON_NEGATIVE = ("CURRENT_", "RTHETA", "PSI", "R_", "RDS", "COSS", "CISS", "CRSS", "QG", "PD", "VGS")


def unit_si(unit):
    if not unit:
        return None, None
    key = unit.strip().lower().replace(" ", "")
    key = key.replace("\u03bc", "u").replace("\u00b5", "u").replace("\u2103", "\u00b0c").replace("\u2126", "ohm").replace("\u03c9", "ohm")
    if key in UNIT_CANON:
        return UNIT_CANON[key]
    return unit.strip(), 1.0  # unknown unit: identity scale, kept verbatim


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _unit_base(u):
    SI = {"f", "p", "n", "u", "m", "k", "M"}
    if len(u) > 1 and u[0] in SI and u[1:].lower() != "ohm":
        return u[1:].lower()
    return u.lower()


def _board_rows(group, rows):
    fams = {}
    for r in rows:
        uc, _ = unit_si(r["unit"])
        if uc:
            b = _unit_base(uc)
            fams[b] = fams.get(b, 0) + 1
    if not fams:
        return []
    dominant = max(fams, key=fams.get)
    out = []
    excluded_off_family = 0
    for r in rows:
        if r["value"] is None:
            continue
        uc, scale = unit_si(r["unit"])
        if uc is None:
            continue  # no unit: not a physical quantity row
        if _unit_base(uc) != dominant:
            excluded_off_family += 1
            continue  # off-family units (incl. per-X like uA/MHz in a current board)
        si = r["value"] * scale
        if group.startswith(NON_NEGATIVE) and si <= 0:
            continue  # negative current/thermal/cap = range bound or misparse, not a spec
        r2 = dict(r)
        r2["value_si"] = si
        r2["unit_canon"] = uc
        out.append(r2)
    for r in out:
        r["off_family_rows_excluded"] = excluded_off_family
    return out


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    release = int(os.stat(DB).st_mtime)
    rows = con.execute(
        "SELECT symbol, count(*) n FROM claims_canonical GROUP BY symbol"
    ).fetchall()
    by_n = {r["symbol"]: r["n"] for r in rows}
    groups = {}
    for sym, n in by_n.items():
        g = SYNONYMS.get(sym, sym)
        groups[g] = groups.get(g, 0) + n
    swept = sorted((g for g, n in groups.items() if n >= MIN_CLAIMS))
    print(f"symbols total={len(by_n)} groups={len(groups)} swept(>= {MIN_CLAIMS})={len(swept)}")

    aec_parts = {r[0] for r in con.execute(
        "SELECT DISTINCT opn FROM claims_canonical WHERE symbol='AEC_GRADE'"
        " AND value_text IN ('1','2','3')")}

    OUT.mkdir(parents=True, exist_ok=True)
    boards_dir = OUT / "boards.v1"
    boards_dir.mkdir(exist_ok=True)
    index = []
    n_boards = 0
    for g in (s for s in swept if slug(s)):
        wanted = [s for s in by_n if SYNONYMS.get(s, s) == g]
        qmarks = ",".join("?" * len(wanted))
        base = con.execute(
            f"SELECT opn, symbol, qualifier, value, value_text, unit, condition_norm,"
            f" doc_sha256, provenance FROM claims_canonical"
            f" WHERE symbol IN ({qmarks}) AND value IS NOT NULL", wanted).fetchall()
        quals = ("typ", "max", "min")
        for cohort_name, parts_filter in (("all", None), ("aec", aec_parts)):
            rows_all = [dict(r) for r in base
                        if parts_filter is None or r["opn"] in parts_filter]
            for qual in quals:
                rows_c = [r for r in rows_all if (r["qualifier"] or "typ").lower() == qual]
                rows_c = _board_rows(g, rows_c)
                if len(rows_c) < 5:
                    continue
                for direction in ("asc", "desc"):
                    ranked = sorted(rows_c, key=lambda r: r["value_si"],
                                    reverse=(direction == "desc"))[:TOP_N]
                    name = f"{slug(g)}-{qual}-{direction}-{cohort_name}"
                    prov = None
                    try:
                        prov = json.loads(ranked[0]["provenance"] or "{}")
                    except json.JSONDecodeError:
                        pass
                    board = {
                        "schema": "harness.rankboard.v1",
                        "name": name,
                        "symbol_group": g, "symbols_folded": wanted,
                        "direction": direction, "cohort": cohort_name,
                        "qualifier": qual,
                        "knife": {"axis": g, "op": "lte" if direction == "asc" else "gte",
                                  "statistic": qual},
                        "ranked_parts": len({r["opn"] for r in ranked}),
                        "coverage": {"rows_in_cohort": len(rows_c),
                                     "distinct_parts": len({r["opn"] for r in rows_c})},
                        "release_mtime": release, "generated_at": time.time(),
                        "rows": [{k: r[k] for k in
                                  ("opn", "value", "value_si", "value_text", "unit",
                                   "unit_canon", "qualifier", "condition_norm",
                                   "doc_sha256")} for r in ranked],
                    }
                    (boards_dir / f"{name}.json").write_text(json.dumps(board))
                    index.append({"name": name, "symbol_group": g,
                                  "qualifier": qual, "direction": direction,
                                  "cohort": cohort_name,
                                  "ranked_parts": board["ranked_parts"]})
                    n_boards += 1
    (OUT / "index.v1.jsonl").write_text(
        "\n".join(json.dumps(i) for i in index) + "\n")
    print(f"boards written: {n_boards} -> {boards_dir}")


if __name__ == "__main__":
    main()
