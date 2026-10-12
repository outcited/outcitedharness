#!/usr/bin/env python3
"""Extraction board — the movement marker.

Per category (aisle), on a part basis: how many addressable parts we have and
what fraction carry each facet we can sell a category with (pinout, ballout,
overview, parametrics, thermal, curves, errata, features). Every run writes a
snapshot; the board prints Δ since the previous snapshot, because movement —
not totals — is the marker of forward progress.

This is THE scoreboard for the migration (PLAN: "the board is the plan"):
the migration is done when every category is at its gold bar here.

Data: /Volumes/M5_4TB/staging/datasheet_extractions/<aisle>/datasheet_extractions/
— the same store scripts/extraction_api.py serves. Aisle == category.

Usage: python3 scripts/extraction_board.py [--json]
Output: results/api-coverage/BOARD.md + results/api-coverage/board/<ts>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path("/Volumes/M5_4TB/staging/datasheet_extractions")
OUT = ROOT / "results" / "api-coverage"
SNAP_DIR = OUT / "board"

# What the front end can render, per part — the saleable facets.
FACETS = [
    "pillars", "pinout", "overview", "description", "parametric_specs",
    "thermal", "curves", "errata", "peripheral_features", "deep_specs",
    "enriched_specs", "electrical", "vision_electrical", "connectors",
    "vout_iq", "power_modes", "qualification", "aec", "claude_ground_truth",
    "mistral_compatibility", "fine_tune_facets",
]

# The gold bar per category: the minimum extracted+provenanced set to sell
# the category above-OPN. Judgement differs per category — MCU is sold on
# family/series documents (one die, many parts) plus the device-identity
# sections; POWER on the ratings and the curves; CONNECTOR on the mechanical
# and electrical interface. The MCU bar is the measured ground-truth section
# set (266-doc registry); POWER/CONNECTOR bars are the lane's own vocab.
GOLD_BAR = {
    "mcu": ["overview", "pinout", "pillars", "parametric_specs", "power_modes"],
    "power": ["pillars", "parametric_specs", "thermal", "curves"],
    "connector": ["connectors", "parametric_specs", "pinout"],
}

AISLES = [
    ("mcu", "mcu_family_content"),
    ("power", "power_family_content"),
    ("connector", "connector_family_content"),
    ("refdesign", "refdesign_family_content"),
]


def _truthy(v) -> bool:
    return bool(v) and v != [] and v != {}


def _load_one(args: tuple[Path, str]):
    d, fn = args
    try:
        return json.loads((d / fn).read_text())
    except Exception:
        return None


def snapshot() -> dict:
    board = {"categories": {}, "totals": {"records": 0, "addressable": 0}}
    for cat, aisle in AISLES:
        d = STAGING / aisle / "datasheet_extractions"
        files = [f for f in os.listdir(d) if f.endswith(".json")] if d.is_dir() else []
        # SMB is latency-bound: parallel readers, serial accounting.
        with ThreadPoolExecutor(max_workers=32) as ex:
            records = [r for r in ex.map(_load_one, [(d, fn) for fn in files]) if r]
        addressable: dict[str, dict] = {}
        facets: dict[str, int] = {}
        facet_fields: dict[str, int] = {}
        for rec in records:
            pn = rec.get("part_number") or "?"
            rows = [(pn, rec)]
            for cov in rec.get("parts_covered") or []:
                pn2 = cov if isinstance(cov, str) else (cov or {}).get("part_number")
                if pn2:
                    rows.append((pn2, rec))
            for key, r in rows:
                addressable.setdefault(key, r)
        for r in addressable.values():
            for f in FACETS:
                if _truthy(r.get(f)):
                    facets[f] = facets.get(f, 0) + 1
            pil = r.get("pillars")
            if isinstance(pil, dict):
                for k, v in pil.items():
                    if _truthy(v):
                        facet_fields[k] = facet_fields.get(k, 0) + 1
        n = len(addressable)
        entry = {
            "records": len(files),
            "addressable_parts": n,
            "facets": dict(sorted(facets.items(), key=lambda x: -x[1])),
            "pillar_fields": dict(sorted(facet_fields.items(), key=lambda x: -x[1])[:25]),
        }
        board["categories"][cat] = entry
        board["totals"]["records"] += len(files)
        board["totals"]["addressable"] += n
    board["generated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return board


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "–"


def _delta(now: int, prev: int | None) -> str:
    if prev is None:
        return ""
    d = now - prev
    return f" ({d:+d})" if d else " (±0)"


def render(cur: dict, prev: dict | None) -> str:
    lines = [
        f"# Extraction board — {cur['generated_at']}",
        "",
        "**Movement is the marker.** Δ since previous snapshot in `( )`.",
        f"Total: **{cur['totals']['addressable']:,} addressable parts** from "
        f"{cur['totals']['records']:,} extraction records.",
    ]
    if prev:
        d = cur["totals"]["addressable"] - prev["totals"]["addressable"]
        lines.append(f"Since last board: **{d:+d} parts**.")
    lines += ["", "## The headline — per category, on a part basis", ""]
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        f = e["facets"]
        bits = [
            f"pinout {_pct(f.get('pinout', 0), n)}",
            f"pillars {_pct(f.get('pillars', 0), n)}",
            f"overview {_pct(f.get('overview', 0), n)}",
            f"parametrics {_pct(f.get('parametric_specs', 0), n)}",
            f"thermal {_pct(f.get('thermal', 0), n)}",
            f"curves {_pct(f.get('curves', 0), n)}",
            f"connectors {_pct(f.get('connectors', 0), n)}",
            f"features {_pct(f.get('peripheral_features', 0), n)}",
        ]
        lines.append(f"- **{cat.upper()}** — {n:,} parts: " + ", ".join(bits))
    lines += ["", "## Per category — parts carrying each saleable facet", ""]
    cols = ["pinout", "pillars", "overview", "parametric_specs", "thermal",
            "curves", "connectors", "errata", "peripheral_features"]
    lines.append("| category | parts | " + " | ".join(cols) + " |")
    lines.append("|---|---:|" + "---:|" * len(cols))
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        prev_n = (prev or {}).get("categories", {}).get(cat, {}).get("addressable_parts")
        row = [cat, f"**{n:,}**{_delta(n, prev_n)}"]
        for c in cols:
            row.append(_pct(e["facets"].get(c, 0), n))
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Gold bars — the minimum to sell the category (above-OPN)", ""]
    lines.append("| category | bar facet | carrying | coverage |")
    lines.append("|---|---|---:|---:|")
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e or cat not in GOLD_BAR:
            continue
        n = e["addressable_parts"]
        for bf in GOLD_BAR[cat]:
            c = e["facets"].get(bf, 0)
            lines.append(f"| {cat} | {bf} | {c:,} | {_pct(c, n)} |")
    lines += ["", "## Pillar fields — comparison coverage", ""]
    top_fields = {}
    for cat, _ in AISLES:
        for k, v in (cur["categories"].get(cat) or {}).get("pillar_fields", {}).items():
            top_fields[k] = top_fields.get(k, 0) + v
    for k, v in sorted(top_fields.items(), key=lambda x: -x[1])[:15]:
        d = ""
        if prev:
            def _prev_field(key: str) -> int:
                total = 0
                for c, _ in AISLES:
                    pf = (prev.get("categories", {}).get(c) or {}).get("pillar_fields", {})
                    total += pf.get(key, 0)
                return total
            d = _delta(v, _prev_field(k))
        lines.append(f"- {k}: {v:,}{d}")
    lines += [
        "",
        "## Denominator note (D9)",
        "",
        "A part *carries* a facet when the extraction record holds a truthy",
        "facet value for it. The parts API (`/v1/coverage`) counts presence",
        "under its own rule and reports higher pinout/overview numbers;",
        "both denominators are named, neither is blended. Movement within",
        "one board's rule is the signal.",
    ]
    lines += ["", "## Facet detail per category", ""]
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        lines.append(f"### {cat} — {n:,} parts / {e['records']:,} records")
        for f, c in e["facets"].items():
            if c:
                lines.append(f"- {f}: {c:,} ({_pct(c, n)})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    cur = snapshot()
    prev = None
    snaps = sorted(SNAP_DIR.glob("*.json"))
    if snaps:
        prev = json.loads(snaps[-1].read_text())
    ts = cur["generated_at"]
    (SNAP_DIR / f"{ts}.json").write_text(json.dumps(cur, indent=1, sort_keys=True))
    if args.json:
        print(json.dumps(cur, indent=1, sort_keys=True))
    else:
        board = render(cur, prev)
        (OUT / "BOARD.md").write_text(board + "\n")
        print(board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
